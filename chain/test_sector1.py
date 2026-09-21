"""Sector 1 test gate — read-only wallet & market-data layer.

Prints a PASS/FAIL report against the BUILD_PLAN Sector 1 Gate:
  1. Full wallet snapshot (all token balances) is internally consistent and
     matches what the block explorer shows for the same address.
  2. A live quote for a real liquid pair, numbers sane (cross-checkable on the
     explorer — the pool id / sqrt / liquidity are printed).
  3. Snapshot / quote / history functions return in seconds, with timings.
  4. Malformed RPC input is caught and logged, never crashes the caller.

Read-only. Signs nothing, simulates nothing.
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "agent-core"))

from web3 import Web3  # noqa: E402

from chain import market, wallet  # noqa: E402

# canonical project wallet (Sector 0) + token addresses verified in research
WALLET = "0x2170105c880B8a5782EDE8ec7B02465f9d3cd981"
TOKENS = [
    market.TOKEN_TSLA,
    market.TOKEN_WETH,
    market.TOKEN_TUSD,
    market.TOKEN_TRWA,
]

PASS, FAIL = "PASS", "FAIL"
results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'x' if ok else '!'}] {name}: {'OK' if ok else 'FAILED'} {detail}")


def timed(label, fn):
    t0 = time.perf_counter()
    value = fn()
    dt = time.perf_counter() - t0
    print(f"    ({label}: {dt:.2f}s)")
    return value, dt


def main() -> int:
    print("== Sector 1 test gate (Robinhood Chain testnet) ==")
    w3 = wallet.connect(wallet.NETWORKS["testnet"][0])
    chain_id = w3.eth.chain_id
    print(f"chain id {chain_id}  (expect 46630)  latest block {w3.eth.block_number}")
    assert chain_id == 46630, "wrong network"

    # ---- Gate 1: wallet snapshot -------------------------------------------
    print("\n[1] Wallet snapshot")
    snap, dt_snap = timed("snapshot", lambda: wallet.get_full_snapshot(
        w3, WALLET, tokens=TOKENS, network="testnet"))
    for b in snap.balances:
        print(f"      {b.symbol:<6} {b.human:>14}   raw {b.raw}")
    eth = next(b for b in snap.balances if b.symbol == "ETH")
    tsla = next(b for b in snap.balances if b.symbol == "TSLA")
    gate1 = (eth.human >= 0.009 and tsla.raw == str(5 * 10 ** 18))
    record("snapshot TSLA == 5.0 & ETH == 0.01 (faucet claim)",
           gate1, f"ETH={eth.human} TSLA={tsla.human}")
    record("snapshot returns < 30s", dt_snap < 30, f"{dt_snap:.2f}s")
    print(f"      explorer: {__import__('config').ROBINHOOD_TESTNET_EXPLORER}/address/{WALLET}")

    # ---- Gate 2: live quote for the liquid tUSD/tRWA pool -------------------
    print("\n[2] Live quote — tUSD -> tRWA (verified liquid v4 pool)")
    quote, dt_q = timed("quote", lambda: market.get_quote(
        w3, market.TOKEN_TUSD, market.TOKEN_TRWA, 100, network="testnet"))
    print(f"      {quote.to_dict()}")
    sane = (quote.ok and quote.amount_out_human is not None
            and 0 < float(quote.amount_out_human) < float(quote.amount_in_human)
            and quote.liquidity and quote.liquidity > 0
            and quote.sqrt_price_x96 and quote.sqrt_price_x96 > 0)
    record("quote ok, out-in bounds sane", quote.ok and sane,
           f"100 tUSD -> {quote.amount_out_human} tRWA")
    record("quote returns < 30s", dt_q < 30, f"{dt_q:.2f}s")
    print(f"      pool id: 0x{quote.pool_id}")
    print(f"      explorer check: pool manager {market.POOL_MANAGER_8366}")

    # ---- no-liquidity pools: permanent no-liquidity test case ------------------
    print("\n[3] No-liquidity pairs — must fail cleanly, loudly, and distinctly")
    no_liq_pairs = [
        ("TSLA/WETH", market.TOKEN_TSLA, market.TOKEN_WETH),   # both live, no pool
        ("TSLA/tRWA", market.TOKEN_TSLA, market.TOKEN_TRWA),   # both live, no pool
        ("WETH/tUSD", market.TOKEN_WETH, market.TOKEN_TUSD),   # both live, no pool
        ("TSLA/USDG", market.TOKEN_TSLA, market.USDG_CANDIDATES[0]),  # USDG has no testnet code
    ]
    for name, a, b in no_liq_pairs:
        q, _ = timed(f"quote {name}", lambda aa=a, bb=b: market.get_quote(
            w3, aa, bb, 1, network="testnet"))
        print(f"      -> error_type={q.error_type}  error={q.error}")
        is_zero_quote = q.ok and (q.amount_out_human or "0").startswith("0")
        distinct_failure = (not q.ok and q.error and q.error_type in
                            {market.ERR_POOL_NOT_FOUND, market.ERR_TOKEN_METADATA})
        record(f"{name}: distinct failure (not a silent skip, not a zero-quote)",
               distinct_failure and not is_zero_quote,
               f"ok={q.ok} error_type={q.error_type}")
        record(f"{name}: error message is specific & loggable",
               ("no pool found for pair" in (q.error or "")
                or "no deployed code" in (q.error or "")),
               f"{q.error}")

    print("      programmatic detectability: find_pool() raises market.PoolNotFound")
    pn_type = market.PoolNotFound
    try:
        market.find_pool(w3, market.TOKEN_TSLA, market.TOKEN_WETH)
        raises_pool_not_found = False
    except pn_type:
        raises_pool_not_found = True
    catchable = (raises_pool_not_found and isinstance(pn_type, type)
                 and issubclass(pn_type, Exception) and pn_type is not Exception)
    record("distinct catchable exception type PoolNotFound", catchable,
           f"{pn_type.__module__}.PoolNotFound")

    # ---- Gate 4: malformed input --------------------------------------------
    print("\n[4] Malformed input handling")
    q, _ = timed("quote bad address", lambda: market.get_quote(
        w3, "0xNOTANADDRESS", market.TOKEN_TRWA, 1, network="testnet"))
    record("bad token address -> error Quote, no crash", not q.ok and "error" in q.to_dict(),
           f"-> {q.error[:60] if q.error else 'no error'}")

    # ---- transaction history (faucet claim) ---------------------------------
    print("\n[5] Transaction history (TSLA/WETH/tUSD/tRWA transfers)")
    hist, dt_h = timed("history", lambda: wallet.get_transaction_history(
        w3, WALLET, tokens=TOKENS, lookback_blocks=700_000))
    mints = [h for h in hist if h.from_addr.lower() == "0x" + "0" * 40]
    print(f"      {len(hist)} transfer events; {len(mints)} mint(s) from zero-address")
    for h in hist[-6:]:
        print(f"      b{h.block_number} {h.direction:>3} {h.symbol} {h.amount_human} "
              f"{h.from_addr[:10]}.. -> {h.to_addr[:10]}..  ({(h.tx_hash or '')[:18]}...)")
    if not hist:
        print("      (no transfers found in window)")
    gate5 = any(h.symbol == "TSLA" and h.amount_human == 5.0 for h in hist)
    record("TSLA faucet claim (5.0) visible in history", gate5)
    record("history returns < 30s", dt_h < 30, f"{dt_h:.2f}s")

    # ---- summary -------------------------------------------------------------
    print("\n================================")
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print(f"  [{PASS if ok else FAIL}] {name}" + (f"  ({detail})" if detail else ""))
    print(f"  => {passed}/{len(results)} gate checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()