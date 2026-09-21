"""Sector 1 — Uniswap v4 market-data / quote module (read-only).

Robinhood Chain (testnet, chain 46630, and mainnet 4663) hosts a Uniswap v4.1
PoolManager. This module reads a pool's live state **directly from the contract's
storage** (verified against real Swap events: no quotes-via-simulation, no
third-party pricing API):

* pool id = keccak256(abi.encode(PoolKey)) — full 32 bytes (verified)
* ``_pools`` mapping slot = 6
* ``sqrtPriceX96`` = low 128 bits of ``_pools[id] + 0`` (verified vs Swap event)
* ``liquidity``      = low 128 bits of ``_pools[id] + 3`` (verified vs Swap event)

It returns a normalized :class:`chain.schema.Quote`. Nothing here signs or
simulates anything — the eth_call storage reads and the constant-product math
are strictly read-only, so this is safe for Sector 2 (proposals) to consume.

Research findings encoded below (probe logs 1–22, `%TEMP%\\opencode`):
* Active PoolManager (hundreds of swaps): TESTNET_MANAGER_8366.
* The tUSD/tRWA pool is the only pool this module was verified against
  (liquidity/sqrt match Swap events exactly); it trades every ~few hundred
  blocks at liquidity ~2.9e15.
* No TSLA/WETH or TSLA/USDG pool exists on testnet among the tried config grid;
  stock-meme LP positions exist (~121,24x,xxx blocks) but are dormant.
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent-core"))

from web3 import Web3  # noqa: E402

from chain import rpc as _rpc  # noqa: E402
from chain.schema import Quote  # noqa: E402
from chain.wallet import NETWORKS, token_meta  # noqa: E402

POOL_MANAGER_8366 = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
POOL_MANAGER_5528 = "0x552815eF68E6eb418A3d65D0AA1043d93204F612"
POOLS_MAPPING_SLOT = 6  # verified for POOL_MANAGER_8366

# Hook seen on all actively-trading pools found on testnet:
HOOK_ACTIVE = "0xa6608F01263E6d598A22D1BC2E93740a01612Aa0"
HOOK_ZERO = "0x0000000000000000000000000000000000000000"

# (fee_tier, tick_spacing) configs observed/expected on testnet pools.
POOL_CONFIGS = [
    (8_388_608, 60, HOOK_ACTIVE),   # 0x800000 LP tier — tUSD/tRWA verified
    (0, 60, HOOK_ACTIVE),
    (0, 200, HOOK_ACTIVE),
    (3_000, 60, HOOK_ACTIVE),
    (3_000, 100, HOOK_ACTIVE),
    (100, 60, HOOK_ACTIVE),
    (100, 10, HOOK_ACTIVE),
    (500, 10, HOOK_ACTIVE),
    (10, 10, HOOK_ACTIVE),
    (8_388_608, 60, HOOK_ZERO),
    (3_000, 60, HOOK_ZERO),
]

# Token registry used by the demo (verified addresses from Sector 0/1 research).
TOKEN_TSLA = "0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E"
TOKEN_WETH = "0x33e4191705c386532ba27cBF171Db86919200B94"
TOKEN_TUSD = "0x43d412f25B2792895A5311689aB07E0E56fCb033"
TOKEN_TRWA = "0x57f637b5b92ea47598fE2C5e0734E98800D0Cdda"
USDG_CANDIDATES = [
    "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",  # mainnet-noted; no testnet code
]

Q96 = 2 ** 96


def _w3rpc(w3) -> str:
    uri = getattr(w3.provider, "endpoint_uri", None)
    if not uri:
        raise RuntimeError("provider has no endpoint_uri")
    return uri


def pool_id(w3: Web3, currency0: str, currency1: str, fee: int,
            tick_spacing: int, hooks: str) -> bytes:
    """Full 32-byte Uniswap v4 pool id (keccak256 of abi.encode(PoolKey))."""
    c0, c1 = sorted([Web3.to_checksum_address(currency0),
                     Web3.to_checksum_address(currency1)])
    try:
        encoded = w3.codec.encode(
            ["address", "address", "uint24", "int24", "address"],
            [c0, c1, int(fee), int(tick_spacing), Web3.to_checksum_address(hooks)],
        )
    except AttributeError:  # older web3 fallback
        from eth_abi import encode  # noqa: PLC0415
        encoded = encode(["address", "address", "uint24", "int24", "address"],
                         [c0, c1, int(fee), int(tick_spacing),
                          Web3.to_checksum_address(hooks)])
    return w3.keccak(encoded)


def read_pool_state(w3: Web3, manager: str, pool_id_bytes: bytes) -> dict:
    """Live pool state straight from PoolManager storage.

    Returns ``{"sqrt_price_x96": int, "liquidity": int}`` with both verified
    against same-moment Swap events for the tUSD/tRWA pool.
    """
    rpc = _w3rpc(w3)
    base_slot = int(
        Web3.solidity_keccak(["bytes32", "uint256"], [pool_id_bytes, POOLS_MAPPING_SLOT]).hex(),
        16,
    )
    mask128 = (1 << 128) - 1
    word0 = _rpc.json_rpc(rpc, "eth_getStorageAt",
                          [Web3.to_checksum_address(manager), hex(base_slot), "latest"])
    word3 = _rpc.json_rpc(rpc, "eth_getStorageAt",
                          [Web3.to_checksum_address(manager),
                           hex(base_slot + 3), "latest"])
    return {
        "sqrt_price_x96": int(word0, 16) & mask128,
        "liquidity": int(word3, 16) & mask128,
        "manager": manager,
    }


def find_pool(w3: Web3, token_in: str, token_out: str,
              manager: str = POOL_MANAGER_8366) -> dict:
    """Locate a live pool for (token_in, token_out), or raise when not found."""
    fee_layout = None
    for fee, spacing, hooks in POOL_CONFIGS:
        pid = pool_id(w3, token_in, token_out, fee, spacing, hooks)
        state = read_pool_state(w3, manager, pid)
        if state["liquidity"] > 0:
            fee_layout = {"fee": fee, "tick_spacing": spacing, "hooks": hooks}
            break
    if fee_layout is None:
        raise PoolNotFound(f"no pool for {token_in}/{token_out} on {manager}")
    return {"manager": manager, "pool_id": pid.hex(), **fee_layout, **state}


class PoolNotFound(Exception):
    """Raised when no live pool exists for a given pair on a manager."""


ERR_POOL_NOT_FOUND = "pool_not_found"
ERR_BAD_INPUT = "bad_input"
ERR_TOKEN_METADATA = "token_metadata_error"
ERR_RPC = "rpc_error"


def get_quote(w3: Web3, token_in: str, token_out: str, amount_in_human,
              network: str = "testnet") -> Quote:
    """Constant-product quote: exact ``amount_in`` of token_in -> amount_out.

    Read-only; never simulates a swap. Returns a Quote (ok=False with a typed
    error for any malformed input, missing token, or missing pool) — never
    raises for missing liquidity, so callers (e.g. the future guardrail engine)
    can reliably key on ``quote.ok`` / ``quote.error_type``.
    """
    chain_id = int(w3.eth.chain_id)
    block_number = int(w3.eth.block_number)

    def fail(error, error_type=ERR_RPC):
        return Quote.error_quote(network=network, chain_id=chain_id,
                                 block_number=block_number, error=error,
                                 error_type=error_type)

    try:
        c_in = Web3.to_checksum_address(token_in)
        c_out = Web3.to_checksum_address(token_out)
    except Exception as exc:  # noqa: BLE001
        return fail(f"invalid token address for {token_in or token_out}: {exc}",
                    ERR_BAD_INPUT)

    rpc = _w3rpc(w3)
    for label, addr in (("token_in", c_in), ("token_out", c_out)):
        try:
            code = _rpc.eth_get_code(rpc, addr)
        except Exception as exc:  # noqa: BLE001
            return fail(f"could not read code for {label} {addr}: {exc}", ERR_RPC)
        if not code or code == "0x":
            return fail(f"token {label} {addr} has no deployed code on '{network}' "
                        "(token does not exist on this network)", ERR_TOKEN_METADATA)

    try:
        sym_in, dec_in = token_meta(w3, c_in)
        sym_out, dec_out = token_meta(w3, c_out)
        amount_raw = int(Decimal(str(amount_in_human)) * (10 ** dec_in))
        if amount_raw <= 0:
            return fail("amount_in must be > 0", ERR_BAD_INPUT)
        pool = find_pool(w3, c_in, c_out)
    except PoolNotFound:
        return fail(
            f"no pool found for pair {sym_in}/{sym_out} on '{network}' "
            f"({c_in}/{c_out}) — tried {len(POOL_CONFIGS)} pool configs on manager "
            f"{POOL_MANAGER_8366}", ERR_POOL_NOT_FOUND)
    except Exception as exc:  # noqa: BLE001
        return fail(f"{type(exc).__name__}: {exc}", ERR_RPC)

    sqrt = pool["sqrt_price_x96"]
    liq = pool["liquidity"]
    fee = pool["fee"]
    if liq <= 0 or sqrt <= 0:
        return fail("pool has zero liquidity")

    # token ordering inside the pool (sorted by address)
    c0, c1 = sorted([Web3.to_checksum_address(token_in),
                     Web3.to_checksum_address(token_out)])
    token_in_is0 = Web3.to_checksum_address(token_in) == c0

    sqrt_decimal = Decimal(sqrt) / Decimal(Q96)
    # fee tiers > 1e6 (e.g. 0x800000) are dynamic-fee encodings, not literal
    # basis-point fees; the quote then assumes no LP fee (spot-ish).
    fee_fraction = Decimal(1) - Decimal(fee) / Decimal(1_000_000)
    if fee_fraction <= 0 or fee_fraction > 1:
        fee_fraction = Decimal(1)

    # price of token0 in token1, scaled to human units
    price1_per_0_raw = float((sqrt_decimal ** 2) * (10 ** dec_in) / (10 ** dec_out))
    price0_per_1_raw = 1.0 / price1_per_0_raw if price1_per_0_raw else 0.0

    amount_in_raw = Decimal(amount_raw)
    x_after_fee = amount_in_raw * fee_fraction

    if token_in_is0:
        one_over_next = 1 / sqrt_decimal + x_after_fee / Decimal(liq)
        sqrt_next = 1 / one_over_next
        amount_out_raw = Decimal(liq) * (sqrt_decimal - sqrt_next)
    else:
        sqrt_next = sqrt_decimal + x_after_fee / Decimal(liq)
        amount_out_raw = Decimal(liq) * (1 / sqrt_decimal - 1 / sqrt_next)

    amount_out_raw_int = max(0, int(amount_out_raw))
    quote = Quote(
        ok=True, network=network, chain_id=chain_id,
        block_number=int(w3.eth.block_number),
        pool_id=pool["pool_id"],
        token_in=Web3.to_checksum_address(token_in),
        token_out=Web3.to_checksum_address(token_out),
        token_in_symbol=sym_in, token_out_symbol=sym_out,
        amount_in_raw=str(amount_raw),
        amount_in_human=str(float(amount_in_human)),
        amount_out_raw=str(amount_out_raw_int),
        amount_out_human=f"{float(amount_out_raw_int) / 10 ** dec_out:.6f}",
        price1_per_0=float(price1_per_0_raw),
        price0_per_1=float(price0_per_1_raw),
        fee_basis=int(fee),
        sqrt_price_x96=int(sqrt),
        liquidity=int(liq),
    )
    return quote


def demo_pairs(w3: Web3, pairs, amount: float = 1.0) -> list:
    """Convenience: run get_quote() for a list of (in, out) pairs."""
    out = []
    for token_in, token_out in pairs:
        q = get_quote(w3, token_in, token_out, amount)
        out.append(q)
    return out