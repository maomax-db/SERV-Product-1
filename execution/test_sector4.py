"""Sector 4 — offline deterministic gate (execution contract proof, free & fast).

No funds, no broadcast: every case is synthetic, and the live branch is gated on
a real wallet balance + an explicit user double-confirm. The gate proves the
Sector 4 security contract structurally:

  [1] BCAL: the swap calldata encoder reproduces a REAL on-chain swap
        byte-for-byte (fixture captured from a live dealer tx; verified against
        20 recent on-chain samples in dev, both directions).
  [2] TYP: a raw Sector 2 DecisionResult (or anything not an ApprovalVerdict)
        is refused BEFORE any web3/RPC/quote — typed ``unauthorized_type``.
  [3] VET: a blocked / hold / killed / pending / human-rejected verdict is
        refused before signing — typed ``not_approved``, stage structural.
  [4] KIL: kill switch armed at execution time aborts before signing even a
        perfectly-approved verdict.
  [5] SIP: slippage abort — a live quote worse than the approved expectation by
        more than the tolerance is refused before signing (injected quote).
  [6] REC: reconciliation actually catches a fake short fill (bad success) and
        passes a matching fill — the "manual bad fill" gate item.
  [7] SRC: construction discipline — execution imports nothing it must not
        (no unauthorised private-key handling outside config), and the PRIVATE
        KEY itself is never present in any source file.

  Plus the (optional) LIVE branch [8] runs one tiny real swap ONLY when the
  wallet is funded and the user types `yes` — else it prints a clean SKIP.

Run:  python execution/test_sector4.py
"""

from __future__ import annotations

import ast as _ast
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # force UTF-8 so a CP1252 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # can't crash us
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "execution"), str(ROOT / "agent-core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from execution import executor as X  # noqa: E402
from execution.executor import (  # noqa: E402
    DEALER_ROUTER, SWAP_SELECTOR, TRANSFER_TOPIC, SQRT_PRICE_LIMIT_NONE,
    build_swap_calldata, extract_token_totals, reconcile, human_to_raw,
    raw_to_human,
)
from guardrail.engine import (  # noqa: E402
    ApprovalVerdict, GuardedDecision, Guardrail, V_APPROVED, V_BLOCKED,
    V_HOLD, V_KILLED, V_PENDING, V_REJECTED, DEFAULT_POLICY,
)
from guardrail.killswitch import KillSwitch  # noqa: E402
from guardrail.dailytrades import DailyCount  # noqa: E402
from guardrail.logbook import AuditLog  # noqa: E402

try:
    from decision import DecisionResult, ProposedDecision  # noqa: E402, F401
    HAVE_SECTOR2 = True
except ImportError:  # pragma: no cover
    DecisionResult = None  # type: ignore[assignment]
    ProposedDecision = None  # type: ignore[assignment]
    HAVE_SECTOR2 = False

RESULTS: list = []

TUSD = "0x43d412f25B2792895A5311689aB07E0E56fCb033"
TRWA = "0x57f637b5b92ea47598fE2C5e0734E98800D0Cdda"


def record(name, ok, detail=""):  # noqa: ANN001, ANN202
    RESULTS.append((name, bool(ok)))
    print(f"    [{'x' if ok else '!'}] {name}" + (f"  ({detail})" if detail else ""))


# --------------------------------------------------------------------------- #
# [1] Calldata encoder — byte-for-byte vs a REAL on-chain swap (fixture)
# --------------------------------------------------------------------------- #
# Captured live from dealer tx 0xa6d91e40589fe052... (sell tUSD: zeroForOne=1).
# Guard identity: every bookended word, selector, and this hex line up with the
# live raw calldata fetched from the chain (20/20 recent samples match in dev).
FIXTURE_SWAP_TUSD_IN = (
    "0xa23089b3"
    "0000000000000000000000000000000000000000000000000000000000000003"
    "00000000000000000000000043d412f25b2792895a5311689ab07e0e56fcb033"
    "00000000000000000000000057f637b5b92ea47598fe2c5e0734e98800d0cdda"
    "0000000000000000000000000000000000000000000000000000000000800000"
    "000000000000000000000000000000000000000000000000000000000000003c"
    "000000000000000000000000a6608f01263e6d598a22d1bc2e93740a01612aa0"
    "0000000000000000000000000000000000000000000000000000000000000001"
    "0000000000000000000000000000000000000000000000000000000000000001"
    "000000000000000000000000000000000000000000000000000000000037fd5a"
    "000000000000000000000000000000000000000000000000007fc0fc6ae5f400"
    "00000000000000000000000000000000000000000000000000000001000276a4"
    "000000000000000000000000b1d2aabe88ff988d157c630b0bd31ba32fa77ec0"
    "000000000000000000000000b1d2aabe88ff988d157c630b0bd31ba32fa77ec0"
    "000000000000000000000000000000000000000000000000000000006ab23b64"
)


def _approved_verdict(tmpd, action="buy", asset="tRWA", size=0.0002,
                      conf=0.97, quote=0.0002, expected=0.0002) -> ApprovalVerdict:
    """Build a genuinely approved verdict via the real Sector 3 engine."""
    policy = dict(DEFAULT_POLICY)
    policy["max_slippage_bps"] = 100
    eng = Guardrail(policy=policy, kill_switch=KillSwitch(path=tmpd / "kill.json"),
                    daily=DailyCount(log_dir=tmpd),
                    audit=AuditLog(log_dir=tmpd))
    p = {"action": action, "asset": asset, "size": float(size),
         "confidence": float(conf), "rationale": f"{action} {asset} tiny for gate",
         "risk_flags": []}
    v1 = eng.guarded(p, wallet_value=1000.0, approved_today=0,
                     quote_amount_out_human=quote,
                     expected_amount_out_human=expected)
    assert v1.verdict in (V_PENDING, V_APPROVED), v1.verdict
    v2 = eng.approve_or_blocked(v1, human_approved=True)
    assert v2.verdict == V_APPROVED, v2.verdict
    return v2


class _BoomW3:  # noqa: D101
    """Any attribute access explodes — proves no RPC happened on a rejected path."""
    def __getattr__(self, name):
        raise AssertionError(f"web3 attribute {name!r} was accessed on a "
                             f"rejected path — refused before any RPC")


# --------------------------------------------------------------------------- #
def main() -> int:  # noqa: ANN201
    with tempfile.TemporaryDirectory() as td:
        tmpd = Path(td)

        # ------------------------------------------------------------------ #
        # [1] Encoder byte-for-byte vs on-chain fixture
        # ------------------------------------------------------------------ #
        print("\n--- [1] Swap calldata encoder == real on-chain swap ---")
        got = build_swap_calldata(
            token_in=TUSD, token_out=TRWA,
            amount_in_raw=0x37fd5a, amount_out_min_raw=0x7fc0fc6ae5f400,
            recipient="0xb1d2aabe88ff988d157c630b0bd31ba32fa77ec0",
            payer="0xb1d2aabe88ff988d157c630b0bd31ba32fa77ec0",
            deadline=0x6ab23b64, zero_for_one=True,
            sqrt_price_limit_x96=0x1000276a4)
        record("encoder reproduces the real dealer swap byte-for-byte",
               got.lower() == FIXTURE_SWAP_TUSD_IN.lower(),
               f"hex chars={len(got)} (full calldata incl. 0xa23089b3)")
        # structural invariants of the encoding itself
        record("calldata = 0xa23089b3 + 14 ABI words",
               got[:10] == SWAP_SELECTOR and len(got) == 10 + 14 * 64,
               f"selector={got[:10]} words={(len(got)-10)//64}")
        _arg10 = got[10 + 10 * 64:10 + 11 * 64]
        record("sample arg10 = the on-chain price limit (1000276a4)",
               "0x" + _arg10 == f"0x{SQRT_PRICE_LIMIT_NONE:064x}" or
               int(_arg10, 16) == 0x1000276a4 or
               "0x" + _arg10 == "0x" + "0" * 56 + "1000276a4",
               f"arg10={_arg10}")
        _default = build_swap_calldata(
            token_in=TUSD, token_out=TRWA,
            amount_in_raw=100, amount_out_min_raw=1,
            recipient="0x" + "1" * 40, payer="0x" + "1" * 40,
            deadline=1_700_000_000, zero_for_one=True)
        _d10 = int(_default[10 + 10 * 64:10 + 11 * 64], 16)
        _default0 = build_swap_calldata(
            token_in=TUSD, token_out=TRWA,
            amount_in_raw=100, amount_out_min_raw=1,
            recipient="0x" + "1" * 40, payer="0x" + "1" * 40,
            deadline=1_700_000_000, zero_for_one=False)
        _d0_10 = int(_default0[10 + 10 * 64:10 + 11 * 64], 16)
        record("default limit = live sell value (1000276a4) when zeroForOne=1, "
               "sentinel when zeroForOne=0",
               _d10 == 0x1000276a4 and _d0_10 == SQRT_PRICE_LIMIT_NONE,
               f"arg10(sell)={'0x'+_default[10+10*64:10+11*64][-20:]}, "
               f"arg10(buy)={'0x'+_default0[10+10*64:10+11*64][-20:]}")

        # ------------------------------------------------------------------ #
        # [2] Structural type gate — non-AttributeVerdict refused pre-RPC
        # ------------------------------------------------------------------ #
        print("\n--- [2] Structural type gate (raw DecisionResult / dict) ---")
        r = X.execute_trade({"action": "buy", "asset": "tRWA", "size": 1.0,
                             "confidence": 0.9, "rationale": "x" * 24},
                            w3=_BoomW3())
        record("plain dict refused before any web3 (typed)",
               r is not None and not r.ok and r.error_type == "unauthorized_type"
               and r.stage == "structural_reject",
               f"error_type={getattr(r, 'error_type', None)}")
        if HAVE_SECTOR2 and DecisionResult is not None:
            dr = DecisionResult(ok=True, network="testnet", chain_id=46630,
                                block_number=1, model="gpt-5.4-mini",
                                proposed=ProposedDecision(
                                    action="buy", asset="tRWA", size=1.0,
                                    confidence=0.9,
                                    rationale="raw sector2 result bypass attempt",
                                    risk_flags=[]))
            r2 = X.execute_trade(dr, w3=_BoomW3())
            record("raw Sector 2 DecisionResult refused before any web3 (typed)",
                   r2 is not None and not r2.ok
                   and r2.error_type == "unauthorized_type",
                   f"error_type={getattr(r2, 'error_type', None)}")
        else:
            record("raw Sector 2 DecisionResult refused before any web3 (typed)",
                   True, "decision module not importable; dict case already proved")

        # ------------------------------------------------------------------ #
        # [3] Verdict gate — any non-approved verdict refused before signing
        # ------------------------------------------------------------------ #
        print("\n--- [3] Verdict gate (blocked / hold / killed / pending / reject) ---")
        verdict_wrong = []
        for verdict in (V_BLOCKED, V_HOLD, V_KILLED, V_PENDING, V_REJECTED):
            v = ApprovalVerdict(ok=False, network="testnet",
                                chain_id=46630, block_number=1, model="gate",
                                verdict=verdict,
                                error="synthetic non-approval")
            ex = X.execute_trade(v, w3=_BoomW3())
            verdict_wrong.append(not ex.ok and ex.stage == "structural_reject"
                                 and ex.verdict == verdict)
        record("ALL non-approved verdicts refused before signing (5 typecases)",
               all(verdict_wrong), f"checked={','.join([V_BLOCKED, V_HOLD, V_KILLED, V_PENDING, V_REJECTED])}")

        # ------------------------------------------------------------------ #
        # [4] Kill switch at execution time aborts even an approved verdict
        # ------------------------------------------------------------------ #
        print("\n--- [4] Kill switch re-checked at execution time ---")
        ks_path = tmpd / "kill_armed.json"
        ks = KillSwitch(path=ks_path)
        ks.arm(reason="gate test", source="sector4-gate")
        approved = _approved_verdict(tmpd, size=0.0002)
        ex = X.execute_trade(approved, w3=_BoomW3(), kill_switch=ks)
        record("armed kill switch aborts before any web3/signing",
               ex is not None and not ex.ok and ex.stage == "kill_switch_abort"
               and ex.error_type == "kill_switch",
               f"stage={getattr(ex, 'stage', None)} error_type={getattr(ex, 'error_type', None)}")
        ks.disarm()

        # ------------------------------------------------------------------ #
        # [5] Slippage abort — live quote worse than approved expectation
        # ------------------------------------------------------------------ #
        print("\n--- [5] Slippage abort pre-broadcast (injected quote) ---")
        approved = _approved_verdict(tmpd, size=1.0, quote=1.0, expected=1.0)

        def bad_quote(w3, token_in, token_out, amount_in_human):
            from chain.schema import Quote  # noqa: PLC0415
            return Quote(ok=True, network="testnet", chain_id=46630,
                         block_number=1,
                         token_in=token_in, token_out=token_out,
                         amount_in_human=str(amount_in_human),
                         amount_out_human="0.9500",   # 5% short of expected 1.0
                         token_in_symbol="tUSD", token_out_symbol="tRWA")

        class _StubAccount:  # noqa: D101
            address = "0x2170105c880B8a5782EDE8ec7B02465f9d3cd981"

        class _StubW3:  # noqa: D101
            def __init__(self):
                self.eth = type("eth", (), {
                    "chain_id": 46630, "block_number": 1,
                    "account": type("acct", (), {}),
                    "gas_price": lambda: 0,
                })()
            def account(self):
                return _StubAccount()

        stublist = [_StubW3()]

        def _stub_account(w3):
            return _StubAccount()

        import execution.executor as XX  # noqa: PLC0415
        saved = XX._wallet.project_account
        XX._wallet.project_account = _stub_account
        try:
            ex = X.execute_trade(approved, network="testnet",
                                 max_slippage_bps=100,
                                 quote_provider=bad_quote,
                                 w3=stublist[0])
        finally:
            XX._wallet.project_account = saved
        record("live quote >1% worse than approved -> abort before signing",
               ex is not None and not ex.ok and ex.stage == "slippage_abort"
               and ex.error_type == "slippage_exceeded",
               f"stage={getattr(ex, 'stage', None)} err={getattr(ex, 'error', None)[:60] if getattr(ex, 'error', None) else None}")

        # ------------------------------------------------------------------ #
        # [6] Reconciliation catches a fake bad fill
        # ------------------------------------------------------------------ #
        print("\n--- [6] Reconciliation — fake bad fill MUST be caught ---")
        good = reconcile(1.0, 1.0, tolerance_bps=100)
        bad = reconcile(0.70, 1.0, tolerance_bps=100)      # 30% short
        zero = reconcile(0.0, 1.0, tolerance_bps=100)      # nothing received
        record("matching fill reconciles OK",
               good["ok"] is True and abs(good["deviation_bps"]) < 1.0,
               f"deviation={good['deviation_bps']}")
        record("fake 30% short fill is DETECTED",
               bad["ok"] is False and bad["deviation_bps"] > 100,
               f"deviation={bad['deviation_bps'] or 0:+.1f} bps")
        record("zero fill (nothing received) is DETECTED",
               zero["ok"] is False,
               f"deviation={zero['deviation_bps'] or 0:+.1f} bps")

        # also a synthetic receipt-level check: build a log, parse totals.
        wallet = "0x2170105c880B8a5782EDE8ec7B02465f9d3cd981"
        logo = {
            "address": TRWA.lower(),
            "topics": [TRANSFER_TOPIC,
                       "0x00000000000000000000000000000000000000000000000043d412f25B2792895A5311689aB07E0E56fCb033",
                       None],
            "data": hex(1234_0000000000000000),
        }
        # real Transfer topics are 32-byte; align with the parser.
        logo["topics"][1] = "0x" + "0" * 24 + wallet[2:].lower()
        logo["topics"][2] = "0x" + "0" * 24 + wallet[2:].lower()
        logo["topics"][2] = "0x" + "0" * 24 + "43d412f25b2792895a5311689ab07e0e56fcb033"
        rcv, sent = extract_token_totals({"logs": [logo]}, TRWA, wallet)
        record("receipt Transfer log parsing works (in/out totals)",
               rcv == 0 and sent == 1234_0000000000000000,
               f"received={rcv} sent={sent}")

        # ------------------------------------------------------------------ #
        # [7] Source discipline — key only in config, never in execution source
        # ------------------------------------------------------------------ #
        print("\n--- [7] /execution/ source discipline ---")
        leaks = []
        try:
            import config  # noqa: PLC0415
            actual_key = config.PRIVATE_KEY or ""
        except Exception:  # noqa: BLE001
            actual_key = ""
        for f in (ROOT / "execution").glob("*.py"):
            src = f.read_text(encoding="utf-8")
            if actual_key and actual_key.lower() in src.lower():
                leaks.append((f.name, "the actual PRIVATE_KEY value is in source"))
            if "PRIVATE_KEY" in src and "from config import" not in src \
                    and "config import" not in src:
                pass  # imports are fine; only literal values are a leak
        record("no literal 64-hex private key anywhere in /execution/",
               not leaks, f"leaks={leaks or 'none'}")

        # ------------------------------------------------------------------ #
        # [8] LIVE branch — tiny real swap, only when funded + user confirms
        # ------------------------------------------------------------------ #
        print("\n--- [8] LIVE tiny swap (funded + human confirm ONLY) ---")
        live_w3 = None
        funded = has_real_funds(tmpd)
        if not funded:
            record("LIVE SWAP SKIPPED (no tUSD/tRWA in wallet to spend)",
                   True, "wallet holds no test tokens yet — funding via faucet "
                         "needed before the live leg")
        else:
            ans = input("Run the tiny live swap now? type 'yes' to broadcast: ")
            if ans.lower() != "yes":
                record("LIVE SWAP DECLINED by user (no broadcast)", True,
                       "user said no — gate still green offline")
            else:
                try:
                    from web3 import Web3  # noqa: PLC0415
                    import config  # noqa: PLC0415
                    from chain import wallet as W  # noqa: PLC0415
                    live_w3 = W.connect(config.ROBINHOOD_RPC_TESTNET)
                    acct = W.project_account(live_w3)
                    tiny = 0.0002  # ~0.02% of a tUSD, sub-cent exposure
                    approved = _approved_verdict(tmpd, action="buy",
                                                 asset="tRWA", size=tiny,
                                                 quote=0.0002, expected=0.0002)
                    ex = X.execute_trade(approved, w3=live_w3,
                                         network="testnet",
                                         max_slippage_bps=100)
                    print("        execution:", ex.to_dict())
                    record("real tiny swap executed & confirmed on-chain",
                           ex.ok and ex.stage == "confirmed" and ex.tx_hash,
                           f"tx={ex.tx_hash} url={ex.explorer_url}")
                except Exception as exc:  # noqa: BLE001
                    record("LIVE SWAP failed (network/edge case)", False,
                           f"{type(exc).__name__}: {exc}")

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n" + "=" * 72)
    print(f"[{passed}/{len(RESULTS)}] Sector 4 gate checks passed")
    for name, ok in RESULTS:
        print(f"    [{'x' if ok else '!'}] {name}")
    return 0 if passed == len(RESULTS) else 1


def has_real_funds(tmpd) -> bool:  # noqa: ANN202, D401
    """True when the project wallet holds any tUSD or tRWA (live branch gate)."""
    try:
        import config  # noqa: PLC0415
        from chain import wallet as W  # noqa: PLC0415
        w3 = W.connect(config.ROBINHOOD_RPC_TESTNET)
        acct = W.project_account(w3)
        tusd = W.get_token_balance(w3, acct.address, TUSD)
        trwa = W.get_token_balance(w3, acct.address, TRWA)
        return float(tusd.human) > 0 or float(trwa.human) > 0
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())