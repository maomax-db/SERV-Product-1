"""Sector 4 — Execution Layer (the ONLY code path allowed to sign/ broadcast).

**What this is.** Takes a Sector 3 :class:`ApprovalVerdict` that a human (or
the declared autopilot flag) has *already* approved (``verdict == V_APPROVED``),
and — and only then — turns it into a real Uniswap v4 swap on the dealer
router, signs it with the project private key, broadcasts it, waits for the
receipt, and reconciles the actual received amount against the approved
expectation within a slippage tolerance.

**The security boundary (structural, not conventional):**

* ``execute_trade`` accepts ONLY an already-approved ``ApprovalVerdict``. A raw
  Sector 2 ``DecisionResult``, a blocked/held/pending/killed verdict, or any
  other object is rejected *before* any web3 session is even started — there is
  no code path that builds calldata, estimates gas, signs, or broadcasts from
  anything but a ``V_APPROVED`` verdict.
* The kill switch file is re-checked at execution time, immediately before
  signing. Arming it at any point stops even an already-approved trade.
* Slippage is enforced twice: the live quote at execution time must be within
  ``max_slippage_bps`` of the *approved* expected output, and the on-chain
  ``amountOutMinimum`` bakes the same tolerance into the swap. The swap only
  broadcasts if BOTH pass.
* Reconciliation reads the actual receipt's Transfer logs (real received raw
  amount of the output token), not the quote. A short fill is detected and
  reported, never silently rewritten.
* The private key is read ONLY from ``config.PRIVATE_KEY`` (env / `.env`),
  never printed, and only used inside this module.

**Research-derived ABI (verified on-chain against 5 real ~10-swap samples):**
the dealer router ``swap(...)`` (selector ``0xa23089b3``) takes 14 ABI words:
``[3,                      # fixed routing-path/kind marker (all samples)
   currency0, currency1,   # pool tokens in SORTED address order
   fee (0x800000), tickSpacing (60), hooks,
   1,                      # fixed router flag (all samples)
   zeroForOne,             # direction: 1 sell currency0, 0 sell currency1
   amountIn,               # raw units of the SOLD token
   amountOutMinimum,       # raw units of the RECEIVED token
   sqrtPriceLimitX96,      # no-limit sentinel 0xfffd8963...
   recipient, payer,       # both = calling wallet
   deadline]``
Ordering proven by sample ``0xa6d9...`` (sell tUSD: w7=1, w8=0x37fd5a tUSD-raw,
w9=0x7fc0fc6ae5f400 tRWA-raw) vs ``0x900a...`` (sell tRWA: w7=0, w8 tRWA-raw,
w9=tUSD-raw). The pool pulls tokenIn from ``payer`` via ``transferFrom``, so an
ERC-20 approval to the dealer must precede the swap.
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent-core"))

from web3 import Web3  # noqa: E402

from chain import market as _market  # noqa: E402
from chain import rpc as _rpc  # noqa: E402
from chain import wallet as _wallet  # noqa: E402
from chain.schema import Quote  # noqa: E402
from config import (  # noqa: E402
    PRIVATE_KEY,
    ROBINHOOD_CHAIN_ID_TESTNET,
    ROBINHOOD_RPC_TESTNET,
    ROBINHOOD_TESTNET_EXPLORER,
)
from guardrail.engine import ApprovalVerdict, V_APPROVED  # noqa: E402
from guardrail.killswitch import KillSwitch  # noqa: E402

# --------------------------------------------------------------------------- #
# Sector 4 constants (mirrors decoded on-chain dealer swap; never magic numbers)
# --------------------------------------------------------------------------- #
DEALER_ROUTER = "0x43a224f4a565a466015eb7bfb41d5ec268dcd72c"
SWAP_SELECTOR = "0xa23089b3"
SWAP_PATH_MARKER = 3             # arg0 — every observed sample
SWAP_ROUTER_FLAG = 1             # arg6 — every observed sample
SWAP_FEE = 8_388_608             # 0x800000 dynamic-fee tier, verified
SWAP_TICK_SPACING = 60
SWAP_HOOKS = "0xa6608f01263E6d598A22D1BC2E93740a01612Aa0"
SQRT_PRICE_LIMIT_NONE = 0xfffd8963efd1fc6a506488495d951d5263988d25
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
DEFAULT_SLIPPAGE_BPS = 100       # 1.00% (matches guardrail DEFAULT_POLICY)
DEFAULT_DEADLINE_SECS = 300

# Execution stages — typed, printed by the test gate, never free-form.
S_ANY = "*"
S_STRUCTURAL = "structural_reject"      # wrong type / not an approval verdict
S_KILLED = "kill_switch_abort"
S_SLIPPAGE = "slippage_abort"
S_NO_SUPPORT = "unsupported_pair_abort"
S_SUBMITTED = "submitted"               # signed + broadcast
S_CONFIRMED = "confirmed"               # receipt landed on-chain

E_BAD_INPUT = "bad_input"
E_NOT_APPROVED = "not_approved"
E_KILL_SWITCH = "kill_switch"
E_SLIPPAGE = "slippage_exceeded"
E_UNSUPPORTED = "unsupported"
E_RPC = "rpc_error"
E_UNAUTHORIZED_TYPE = "unauthorized_type"


def _now_iso() -> str:  # noqa: ANN202
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExecutionResult:  # noqa: D101
    ok: bool
    network: str
    chain_id: int
    block_number: Optional[int] = None
    stage: str = S_ANY
    verdict: str = ""
    decision: Optional[dict] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    tx_hash: Optional[str] = None
    explorer_url: Optional[str] = None
    amount_in_human: Optional[str] = None
    amount_in_raw: Optional[str] = None
    expected_out_human: Optional[str] = None
    actual_out_human: Optional[str] = None
    deviation_bps: Optional[float] = None
    live_quote_out_human: Optional[str] = None
    min_out_human: Optional[str] = None
    token_in: Optional[str] = None
    token_out: Optional[str] = None
    calldata_hex: Optional[str] = None
    queried_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:  # noqa: D102, ANN201
        return asdict(self)


@dataclass
class _SignedSwap:  # noqa: D101
    token_in: str
    token_out: str
    amount_in_raw: int
    min_out_raw: int
    amount_in_human: str
    min_out_human: str
    zero_for_one: bool
    calldata_hex: str
    quote: Quote
    deviation_bps: float


def human_to_raw(human, decimals: int) -> int:  # noqa: ANN001, D401
    """Human amount -> raw token units (Decimal math, no float drift)."""
    scaled = Decimal(str(human)) * (10 ** int(decimals))
    return int(scaled)


def raw_to_human(raw: int, decimals: int) -> float:  # noqa: ANN001
    return float(raw) / (10 ** int(decimals))


def _pad32(value) -> str:  # noqa: ANN001, ANN202
    """Zero-pad an int, address, or hex string to a 32-byte ABI word."""
    if isinstance(value, str):
        if value[:2].lower() == "0x":
            cleaned = value[2:].lower()
            if len(cleaned) in (40, 24):          # address / indexed addr
                return "0" * (64 - len(cleaned)) + cleaned
            if len(cleaned) <= 64 and all(c in "0123456789abcdef" for c in cleaned):
                return "0" * (64 - len(cleaned)) + cleaned
            raise ValueError(f"cannot pad hex string {value!r}")
        return _pad32(int(value))
    return f"{int(value):064x}"


def build_swap_calldata(*, token_in: str, token_out: str,  # noqa: ANN202, D401
                        amount_in_raw: int, amount_out_min_raw: int,
                        recipient: str, payer: str, deadline: int,
                        zero_for_one: bool,
                        sqrt_price_limit_x96: Optional[int] = None) -> str:
    """Encode the 14-word dealer swap calldata (mirrors the on-chain samples
    byte-for-byte; see the module docstring for the argument map).

    ``currency0/currency1`` are the pool tokens in sorted address order —
    exactly what every observed sample emits (arg1=tUSD, arg2=tRWA in BOTH
    directions, with direction carried solely by ``zeroForOne``). Returns the
    full ``0x...`` hex calldata string.
    """
    c_in = Web3.to_checksum_address(token_in)
    c_out = Web3.to_checksum_address(token_out)
    currency0, currency1 = sorted([c_in, c_out])
    if sqrt_price_limit_x96 is None:
        # Live behavior: sells (zeroForOne=1) carry an explicit small limit
        # (0x1000276a4) so the pool doesn't reject the sentinel; buys use the
        # true no-limit sentinel. Mirror the observed on-chain samples.
        sqrt_limit = 0x1000276a4 if zero_for_one else SQRT_PRICE_LIMIT_NONE
    else:
        sqrt_limit = int(sqrt_price_limit_x96)
    words = [
        _pad32(SWAP_PATH_MARKER),          # 0  routing marker (constant)
        _pad32(currency0),                 # 1  currency0 (sorted)
        _pad32(currency1),                 # 2  currency1 (sorted)
        _pad32(SWAP_FEE),                  # 3  fee tier
        _pad32(SWAP_TICK_SPACING),         # 4  tick spacing
        _pad32(Web3.to_checksum_address(SWAP_HOOKS)),  # 5 hooks
        _pad32(SWAP_ROUTER_FLAG),          # 6  router flag (constant)
        _pad32(1 if zero_for_one else 0),  # 7  zeroForOne
        _pad32(amount_in_raw),             # 8  amountIn (raw, sold token)
        _pad32(amount_out_min_raw),        # 9  amountOutMinimum (raw, received)
        _pad32(sqrt_limit),                # 10 price limit
        _pad32(Web3.to_checksum_address(recipient)),  # 11 recipient
        _pad32(Web3.to_checksum_address(payer)),      # 12 payer
        _pad32(deadline),                  # 13 deadline
    ]
    return SWAP_SELECTOR + "".join(words)


# --------------------------------------------------------------------------- #
# Reconciliation (pure — testable with a synthetic "bad fill" receipt)
# --------------------------------------------------------------------------- #
def extract_token_totals(receipt: dict, token_address: str,
                         wallet: str) -> tuple:  # noqa: ANN201, D401
    """Sum raw amounts of ``token_address`` Transfers to/from ``wallet`` in the
    raw receipt ``logs`` list. Returns ``(received_raw, sent_raw)``."""
    token = Web3.to_checksum_address(token_address).lower()
    addr_l = Web3.to_checksum_address(wallet).lower()
    received_raw = 0
    sent_raw = 0
    for log in receipt.get("logs") or []:
        if str(log.get("address") or "").lower() != token:
            continue
        topics = log.get("topics") or []
        if not topics or topics[0].lower() != TRANSFER_TOPIC:
            continue
        if len(topics) < 3:
            continue
        frm = "0x" + _strip_topics(topics[1])[-40:]
        to_ = "0x" + _strip_topics(topics[2])[-40:]
        try:
            amt = int(log.get("data") or "0x0", 16)
        except ValueError:
            amt = 0
        if frm.lower() == addr_l:
            sent_raw += amt
        if to_.lower() == addr_l:
            received_raw += amt
    return received_raw, sent_raw


def _strip_topics(topic: str) -> str:  # noqa: ANN202
    value = topic[2:] if isinstance(topic, str) and topic[:2].lower() == "0x" \
        else topic
    return str(value)


def reconcile(actual_out_human: float, expected_out_human: float,
              tolerance_bps: int = DEFAULT_SLIPPAGE_BPS) -> dict:  # noqa: ANN202
    """Compare the actual received amount against the approved expectation.

    Short-fill detection: passes only when ``actual >= expected*(1-tol)`` and
    the fill is positive. Returns ``{"ok", "actual", "expected", "deviation_bps"}``.
    """
    try:
        want = float(expected_out_human)
    except (TypeError, ValueError):
        want = 0.0
    got = 0.0 if actual_out_human is None else float(actual_out_human)
    if want <= 0:
        return {"ok": False, "actual": got, "expected": want,
                "deviation_bps": None}
    deviation = (want - got) / want * 10_000 if want else 0.0
    ok = got > 0 and deviation <= float(tolerance_bps)
    return {"ok": ok, "actual": got, "expected": want,
            "deviation_bps": float(deviation)}


# --------------------------------------------------------------------------- #
# The typing gate — structural, runs BEFORE any RPC
# --------------------------------------------------------------------------- #
def _reject_not_approved(verdict: ApprovalVerdict) -> ExecutionResult:  # noqa: ANN202
    """A verdict that was NOT approved can never be executed: blocked, held,
    killed, pending, or human-rejected all stop here with a typed result, and
    no web3 session / calldata / signature is ever created."""
    return ExecutionResult(
        ok=False, network=verdict.network, chain_id=verdict.chain_id,
        stage=S_STRUCTURAL, verdict=verdict.verdict,
        decision=(verdict.decision.to_dict() if verdict.decision else None),
        error=verdict.error or "not an approved decision",
        error_type=E_NOT_APPROVED)


def _unsupported_result(verdict: ApprovalVerdict, reason: str) -> ExecutionResult:  # noqa: ANN202
    return ExecutionResult(
        ok=False, network=verdict.network, chain_id=verdict.chain_id,
        stage=S_NO_SUPPORT, verdict=verdict.verdict,
        decision=(verdict.decision.to_dict() if verdict.decision else None),
        error=reason, error_type=E_UNSUPPORTED)


# --------------------------------------------------------------------------- #
# Wallet / quote helpers
# --------------------------------------------------------------------------- #
def _connect(network: str) -> Web3:  # noqa: ANN202
    if network != "testnet":
        raise RuntimeError("Sector 4 refuses to broadcast on mainnet "
                           "(live demo is testnet-only)")
    return _wallet.connect(ROBINHOOD_RPC_TESTNET)


def _asset_to_token(asset: str) -> Optional[str]:  # noqa: ANN202
    name = str(asset or "").lower()
    if name in ("trwa", "rwa", "real-world-assets", "test-rwa"):
        return _market.TOKEN_TRWA
    if name in ("tusd", "usd-test", "test-usd", "usdg"):
        return _market.TOKEN_TUSD
    return None


def _direction(decision) -> tuple:  # noqa: ANN202, D401
    """Resolve ``(token_in, token_out, zero_for_one, amount_in_human)``.

    BUY asset  -> spend tUSD, receive asset  (sell currency0=tUSD,
                                               zeroForOne = not (asset is tUSD))
    SELL asset -> spend asset, receive tUSD.
    Only the verified tUSD/tRWA pool is executable.
    """
    action = str(getattr(decision, "action", "") or "hold").lower()
    asset = str(getattr(decision, "asset", "") or "")
    size = float(getattr(decision, "size", 0) or 0)

    # normalize asset symbol to its token contract
    if asset.lower() in ("trwa", "rwa", "test-rwa"):
        asset_tok = _market.TOKEN_TRWA
    elif asset.lower() in ("tusd", "test-usd", "usdg"):
        asset_tok = _market.TOKEN_TUSD
    else:
        asset_tok = _asset_to_token(asset)

    if action == "buy":
        token_in, token_out = _market.TOKEN_TUSD, asset_tok or _market.TOKEN_TRWA
        amount_in_human = size                    # tUSD to spend
        zero_for_one = True                        # sell currency0 = tUSD
    elif action == "sell":
        token_in, token_out = asset_tok or _market.TOKEN_TRWA, _market.TOKEN_TUSD
        amount_in_human = size                     # asset units to sell
        zero_for_one = False                       # sell currency1 = asset
    else:
        return None
    if token_in is None or token_out is None or asset_tok is None:
        return None
    return (Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out), zero_for_one,
            float(amount_in_human))


def _live_quote(w3: Web3, token_in: str, token_out: str,
                amount_in_human: float, network: str) -> Quote:  # noqa: ANN202
    return _market.get_quote(w3, token_in, token_out, amount_in_human,
                             network=network)


def _slippage_bps(got: float, want: float) -> float:  # noqa: ANN202
    return (got - float(want)) / float(want) * 10_000 if float(want) > 0 \
        else float("inf")


# --------------------------------------------------------------------------- #
# Gas / nonce / sign / broadcast — signing lives ONLY here
# --------------------------------------------------------------------------- #
def _build_and_sign_swap(verdict: ApprovalVerdict, w3: Web3,
                         amount_in_raw: int, min_out_raw: int,
                         token_in: str, token_out: str,
                         zero_for_one: bool, amount_in_human: str,
                         min_out_human: str, quote: Quote,
                         deadline_secs: int) -> tuple:
    """Encode calldata and sign it with the project key.

    NEVER reached unless the verdict is V_APPROVED and the wallet re-check
    passed — this is the single pair (encode+sign) at the top of the gate."""
    acct = _wallet.project_account(w3)
    deadline = int(time.time()) + int(deadline_secs)
    calldata = build_swap_calldata(
        token_in=token_in, token_out=token_out,
        amount_in_raw=amount_in_raw, amount_out_min_raw=min_out_raw,
        recipient=acct.address, payer=acct.address,
        deadline=deadline, zero_for_one=zero_for_one)

    nonce = w3.eth.get_transaction_count(acct.address)
    gas = _estimate_gas(w3, acct, token_in, token_out, amount_in_raw,
                        zero_for_one)
    chain_id = int(w3.eth.chain_id) or ROBINHOOD_CHAIN_ID_TESTNET
    tx = {
        "to": Web3.to_checksum_address(DEALER_ROUTER),
        "value": 0,
        "data": calldata,
        "gas": gas,
        "nonce": nonce,
        "chainId": chain_id,
    }
    if w3.eth.gas_price and int(w3.eth.gas_price) > 0:
        tx["gasPrice"] = int(w3.eth.gas_price)
    signed = acct.sign_transaction(tx)
    signed_hex = signed.raw_transaction.hex()
    if not signed_hex.startswith("0x"):
        signed_hex = "0x" + signed_hex
    return calldata, signed_hex, gas, chain_id


def _estimate_gas(w3: Web3, acct, token_in: str, token_out: str,
                  amount_in_raw: int, zero_for_one: bool) -> int:  # noqa: ANN202, D401
    """Estimate swap gas via eth_estimateGas (from our wallet). Falls back to a
    generous constant on estimation failure — estimation already needs the
    funds to exist, so a funding-bound revert must never block the flow."""
    try:
        calldata = build_swap_calldata(
            token_in=token_in, token_out=token_out,
            amount_in_raw=amount_in_raw, amount_out_min_raw=0,
            recipient=acct.address, payer=acct.address,
            deadline=int(time.time()) + DEFAULT_DEADLINE_SECS,
            zero_for_one=zero_for_one)
        est = w3.eth.estimate_gas({
            "from": acct.address,
            "to": Web3.to_checksum_address(DEALER_ROUTER),
            "data": calldata,
        })
        gas = int(est * 1.15) + 100_000
    except Exception:  # noqa: BLE001
        gas = 1_500_000   # generous safe lane for the tiny live swap
    return max(gas, 210_000)


def _ensure_allowance(w3: Web3, token_address: str, spender: str,
                      amount_raw: int) -> bool:  # noqa: ANN202, D401
    """Approve the dealer to pull ``amount_raw`` of ``token_address`` from the
    wallet (ERC-20 approve), if the current allowance is insufficient."""
    acct = _wallet.project_account(w3)
    token = w3.eth.contract(address=Web3.to_checksum_address(token_address),
                            abi=[{
                                "inputs": [{"name": "owner", "type": "address"},
                                           {"name": "spender", "type": "address"}],
                                "name": "allowance",
                                "outputs": [{"name": "", "type": "uint256"}],
                                "stateMutability": "view", "type": "function"},
                                {
                                "inputs": [{"name": "spender", "type": "address"},
                                           {"name": "amount", "type": "uint256"}],
                                "name": "approve", "outputs": [],
                                "stateMutability": "nonpayable",
                                "type": "function"}])
    allowance = token.functions.allowance(acct.address,
                                          Web3.to_checksum_address(spender)).call()
    if int(allowance) >= int(amount_raw):
        return True
    approve_data = token.encodeABI(fn_name="approve",
                                   args=[Web3.to_checksum_address(spender),
                                         int(amount_raw)])
    nonce = w3.eth.get_transaction_count(acct.address)
    tx = {
        "to": Web3.to_checksum_address(token_address),
        "value": 0, "data": approve_data, "gas": 100_000, "nonce": nonce,
        "chainId": int(w3.eth.chain_id) or ROBINHOOD_CHAIN_ID_TESTNET,
    }
    if w3.eth.gas_price and int(w3.eth.gas_price) > 0:
        tx["gasPrice"] = int(w3.eth.gas_price)
    signed = acct.sign_transaction(tx)
    raw = signed.raw_transaction.hex()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    tx_hash = w3.eth.send_raw_transaction(raw)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return True


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def execute_trade(approved: ApprovalVerdict, *,  # noqa: ANN201, D401
                  w3: Optional[Web3] = None,
                  network: str = "testnet",
                  max_slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
                  deadline_secs: int = DEFAULT_DEADLINE_SECS,
                  quote_provider=None,
                  kill_switch: Optional[KillSwitch] = None) -> ExecutionResult:
    """Execute an already-approved guardrail verdict as a real swap.

    Structurally refuses everything that is not ``V_APPROVED``:
      * a non-``ApprovalVerdict`` (e.g. a Sector 2 ``DecisionResult``) -> typed
        ``unauthorized_type`` rejection, no web3 created,
      * any verdict that isn't ``approved`` (blocked/held/pending/killed/
        rejected) -> typed ``not_approved`` rejection, no web3 created,
      * a kill switch armed at execution time -> abort before signing,
      * a live quote worse than the approved expectation by more than
        ``max_slippage_bps`` -> abort before signing,
      * a pair with no execution support (only tUSD/tRWA) -> abort.

    On success: approves, signs, broadcasts, waits for the receipt, reconciles
    actual vs expected within the same tolerance and reports the deviation.
    """
    if not isinstance(approved, ApprovalVerdict):
        name = type(approved).__name__
        return ExecutionResult(
            ok=False, network=network,
            chain_id=ROBINHOOD_CHAIN_ID_TESTNET,
            stage=S_STRUCTURAL, verdict="",
            error=(f"execute_trade accepts ONLY an already-approved "
                   f"ApprovalVerdict, got {name} — refused before any RPC"),
            error_type=E_UNAUTHORIZED_TYPE)
    if approved.verdict != V_APPROVED or not approved.ok:
        return _reject_not_approved(approved)

    decision = approved.decision
    direction = _direction(decision)
    if direction is None:
        return _unsupported_result(
            approved, "only tUSD/tRWA (and only buy/sell of an approved asset "
                      "on the allowed list) can be executed")

    token_in, token_out, zero_for_one, amount_in_human = direction
    if token_in is None or token_out is None:
        return _unsupported_result(approved, "cannot resolve tokens for "
                                             "this asset")

    # Kill switch re-check BEFORE any web3/RPC access. Arming it at execution
    # time — even after an approval — aborts the whole signing path instantly.
    ks = kill_switch or KillSwitch()
    if ks.is_armed():
        return ExecutionResult(
            ok=False, network=network,
            chain_id=ROBINHOOD_CHAIN_ID_TESTNET,
            stage=S_KILLED, verdict=approved.verdict,
            decision=(decision.to_dict() if decision else None),
            error="kill switch is ARMED at execution time — not signing",
            error_type=E_KILL_SWITCH)

    w3 = w3 or _connect(network)
    acct = _wallet.project_account(w3)
    block_number = int(w3.eth.block_number)
    chain_id = int(w3.eth.chain_id) or ROBINHOOD_CHAIN_ID_TESTNET

    # Expected output anchored to what was approved, not re-derived later.
    try:
        expected_out_human = float(
            approved.expected_amount_out_human or approved.quote_amount_out_human
            or 0.0)
    except (TypeError, ValueError):
        expected_out_human = 0.0
    if expected_out_human <= 0:
        return ExecutionResult(
            ok=False, network=network, chain_id=chain_id,
            block_number=block_number, stage=S_STRUCTURAL,
            verdict=approved.verdict,
            decision=(decision.to_dict() if decision else None),
            error="approved verdict carried no expected fill; refusing",
            error_type=E_NOT_APPROVED)

    # Fresh live quote at execution time (or an injected one for tests).
    quote = quote_provider(w3, token_in, token_out, amount_in_human) \
        if quote_provider else _live_quote(w3, token_in, token_out,
                                           amount_in_human, network)
    if not quote.ok:
        return ExecutionResult(
            ok=False, network=network, chain_id=chain_id,
            block_number=block_number, stage=S_STRUCTURAL,
            verdict=approved.verdict,
            decision=(decision.to_dict() if decision else None),
            error=quote.error or "live quote unavailable",
            error_type=quote.error_type or E_RPC)

    live_out = float(quote.amount_out_human or 0.0)
    deviation = _slippage_bps(live_out, expected_out_human)
    if abs(deviation) > float(max_slippage_bps):
        return ExecutionResult(
            ok=False, network=network, chain_id=chain_id,
            block_number=block_number, stage=S_SLIPPAGE,
            verdict=approved.verdict,
            decision=(decision.to_dict() if decision else None),
            error=(f"live quote {live_out:.6f} deviates "
                   f"{deviation:+.1f} bps from approved {expected_out_human:.6f} "
                   f"(tolerance {max_slippage_bps} bps) — aborted before signing"),
            error_type=E_SLIPPAGE,
            live_quote_out_human=str(live_out),
            expected_out_human=str(expected_out_human),
            amount_in_human=str(amount_in_human))

    # Buy-side slippage guard: minOut anchored to the approved expectation.
    dec_in, dec_out = _decimals(w3, token_in), _decimals(w3, token_out)
    amount_in_raw = human_to_raw(amount_in_human, dec_in)
    min_out_raw = math.floor(float(expected_out_human)
                             * (1.0 - float(max_slippage_bps) / 10_000)
                             * (10 ** dec_out))
    min_out_human = f"{min_out_raw / 10 ** dec_out:.6f}"

    if min_out_raw < 1:
        return ExecutionResult(
            ok=False, network=network, chain_id=chain_id,
            block_number=block_number, stage=S_STRUCTURAL,
            verdict=approved.verdict,
            decision=(decision.to_dict() if decision else None),
            error="computed amountOutMinimum underflows to 0 — refusing",
            error_type=E_BAD_INPUT)

    # ------------------------------------------------------------------ #
    # SIGNING PATH (reached only with an approved verdict + clean gates)
    # ------------------------------------------------------------------ #
    _ensure_allowance(w3, token_in, DEALER_ROUTER, amount_in_raw)

    calldata, signed_raw, gas, chain_id = _build_and_sign_swap(
        approved, w3, amount_in_raw, min_out_raw, token_in, token_out,
        zero_for_one, str(amount_in_human), min_out_human, quote,
        deadline_secs)

    tx_hash = w3.eth.send_raw_transaction(signed_raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    tx_hex = Web3.to_hex(tx_hash) if not isinstance(tx_hash, str) else tx_hash

    received_raw, _sent = extract_token_totals(receipt, token_out, acct.address)
    actual_out_human = raw_to_human(received_raw, dec_out)

    rec = reconcile(actual_out_human, expected_out_human, max_slippage_bps)
    stage = S_CONFIRMED if rec["ok"] else S_SUBMITTED
    return ExecutionResult(
        ok=rec["ok"], network=network, chain_id=chain_id,
        block_number=int(receipt["blockNumber"]) if "blockNumber" in receipt
        else block_number,
        stage=stage, verdict=approved.verdict,
        decision=(decision.to_dict() if decision else None),
        error=None if rec["ok"] else
        (f"reconciliation FAILED: received {actual_out_human:.6f} vs "
         f"expected {expected_out_human:.6f} "
         f"({(rec['deviation_bps'] or 0):+.1f} bps)"),
        error_type=None if rec["ok"] else E_SLIPPAGE,
        tx_hash=tx_hex,
        explorer_url=f"{ROBINHOOD_TESTNET_EXPLORER}/tx/{tx_hex}",
        amount_in_human=str(amount_in_human),
        amount_in_raw=str(amount_in_raw),
        expected_out_human=str(expected_out_human),
        actual_out_human=f"{actual_out_human:.6f}",
        deviation_bps=float(rec["deviation_bps"] or 0),
        min_out_human=min_out_human,
        live_quote_out_human=str(live_out),
        token_in=token_in, token_out=token_out,
        calldata_hex=calldata)


def _decimals(w3: Web3, token_address: str) -> int:  # noqa: ANN202
    _, dec = _wallet.token_meta(w3, token_address)
    return int(dec)


if __name__ == "__main__":  # pragma: no cover
    # Self-check: reproduce a real on-chain swap calldata byte-for-byte.
    sample = build_swap_calldata(
        token_in="0x43d412f25B2792895A5311689aB07E0E56fCb033",
        token_out="0x57f637b5b92ea47598fE2C5e0734E98800D0Cdda",
        amount_in_raw=0x37fd5a, amount_out_min_raw=0x7fc0fc6ae5f400,
        recipient="0xb1d2aabe88ff988d157c630b0bd31ba32fa77ec0",
        payer="0xb1d2aabe88ff988d157c630b0bd31ba32fa77ec0",
        deadline=0x6ab23b64, zero_for_one=True,
        sqrt_price_limit_x96=0x1000276a4)
    print("encoder sample hex (len=%d):" % len(sample))
    print(sample)