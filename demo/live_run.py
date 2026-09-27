"""Weryon — demo/live_run.py: one full live cycle, end-to-end, in the terminal.

For the demo video: this is a THIN ORCHESTRATOR that calls the already-tested
Sector 1 / 2 / 3 functions in sequence and prints every step. It adds no new
guardrail, execution, or logging logic — and it deliberately does NOT import or
call execution/executor.py. If a human approves, the trade is LOGGED as
approved only; nothing is signed or broadcast. We already have one proven
live swap on chain; a recorded demo must not force a second broadcast.

Pipeline (each step is a real call, nothing mocked):

  1. Sector 1  wallet: connect to Robinhood Chain testnet, derive the project
                wallet (PRIVATE_KEY from .env), read a full read-only snapshot.
  2. Sector 1  market: live Uniswap v4 quote from the tUSD/tRWA PoolManager
                pool (direct storage read — the only live-quotable pair).
  3. Sector 2  SERV: propose_trade(snapshot, quote, strategy) — a real SERV
                reasoning call producing ONE structured proposal. SERV's
                confidence is stochastic, so if the Sector 3 confidence fence
                catches a proposal (held_low_confidence < 0.85), that real
                fence-catch is logged as it happened and a NEW real SERV
                proposal is asked for — bounded to 6 attempts so a recorded
                run almost always surfaces a proposal with confidence at or
                above the 0.85 floor (SERV on this degenerate wallet (0
                tUSD/0 tRWA) genuinely proposes HOLD; every fence-catch is
                logged as it happened). A blocked / killed / pending verdict
                ends the loop at once. A SERV refusal/parse flake costs one
                attempt budget and triggers another ask, so transient model/
                serialization hiccups never abort a recorded run.
  4. Sector 3  guardrail: Guardrail.guarded() runs the hard rules (approved
                list, position size, trade share, daily count, slippage) plus
                the confidence fence — each check printed as it is evaluated,
                then the kill-switch re-check and the REAL CLI y/n prompt via
                guardrail.approval.prompt_approval().
  5. Sector 3  audit: the exact same logging the system already uses —
                verdict_to_log_row(final) appended to logs/sector3_audit.jsonl
                (and today's DailyCount file on approval). No new log format.

The strategy passed to SERV is scoped to {tUSD, tRWA}: the tUSD/tRWA pool is
the only pair with live liquidity on testnet, so the guardrail's slippage rule
can be evaluated against a REAL forward quote for whatever SERV proposes.

Usage:  python demo/live_run.py        (answer the y/n prompt for the video)

If the wallet snapshot reports no priced (tUSD-peg) balance, the position-caps
run against DEMO_FUNDING_BASE_TUSD = 10,000 — the same funding base the
recorded ledger (ui/record_real_logs.py and logs/sector3_audit.jsonl) uses.
This is printed loudly whenever it happens so it is never mistaken for a real
wallet valuation."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "chain"), str(ROOT / "agent-core"),
           str(ROOT / "guardrail")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from chain import market, wallet  # noqa: E402
from decision import propose_trade  # noqa: E402
from guardrail.approval import prompt_approval  # noqa: E402
from guardrail.dailytrades import DailyCount  # noqa: E402
from guardrail.engine import (  # noqa: E402
    DEFAULT_POLICY, Guardrail, V_APPROVED, V_HOLD, V_PENDING,
    verdict_to_log_row,
)
from guardrail.killswitch import KillSwitch  # noqa: E402
from guardrail.logbook import AuditLog  # noqa: E402

NETWORK = "testnet"
TOKENS = [market.TOKEN_TSLA, market.TOKEN_WETH, market.TOKEN_TUSD, market.TOKEN_TRWA]
QUOTE_AMOUNT_TUSD = 50.0            # demo reference quote size (tUSD -> tRWA)

# The guardrail policy stays the canonical Sector 3 policy used by the recorded
# ledger (DEFAULT_POLICY + the same overrides record_real_logs.py applies).
POLICY = dict(DEFAULT_POLICY)
POLICY.update({"max_position_pct": 0.40,
               "max_trade_pct_of_wallet": 0.20,
               "confidence_floor": 0.85,
               "max_daily_trades": 8,
               "autopilot_ok": False})

# Sector 2 strategy input: only proposes trades in the one live-quotable pair.
# The approved-list is still enforced by Sector 3's OWN policy (all 4 assets).
DEMO_STRATEGY = {
    "approved": ["tUSD", "tRWA"],
    "targets": {"tUSD": 0.50, "tRWA": 0.50},
    "rebalance_band": 0.10,
    "momentum_guard": 0.25,
    "max_proposal_size": 5.0,
}

DEMO_FUNDING_BASE_TUSD = 10_000.0   # matches the recorded ledger's funding base


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def step(title: str) -> None:
    print(f"\n============================================================")
    print(f"  {title}")
    print(f"============================================================")


def priced_wallet_value(snap: dict, tusd_per_trwa: float) -> float:
    """tUSD-peg value of the snapshot: tUSD at 1.0, tRWA at live pool price.

    Assets the system cannot price on-chain (ETH / TSLA / WETH) are excluded
    and reported separately — no invented prices.
    """
    total = 0.0
    priced = []
    unpriced = []
    for b in snap.get("balances", []):
        sym = str((b.get("symbol") or "?")).upper()
        human = float(b.get("human") or 0.0)
        if sym == "TUSD":
            total += human
            priced.append(f"{sym} {human:.6f} @ 1.0000 tUSD")
        elif sym == "TRWA":
            total += human * tusd_per_trwa
            priced.append(f"{sym} {human:.6f} @ {tusd_per_trwa:.4f} tUSD (live pool)")
        else:
            unpriced.append(f"{sym} {human:.6f} (no live pool — excluded)")
    return total, priced, unpriced


def forward_quote_params(proposal: dict, ref_quote: dict) -> tuple:
    """Map a proposal to a live forward quote for the slippage rule.

    Returns (token_in, token_out, amount_in_human, expected_out_human). Uses
    the reference pool price only to DERIVE the spend; the forward quote is a
    fresh live read. Returns None for a 'hold'.
    """
    action = str(proposal["action"]).lower()
    size = float(proposal["size"] or 0.0)
    if action == "hold" or size <= 0:
        return None
    price_tusd_per_trwa = float(ref_quote["price0_per_1"] or 1.0)
    price_trwa_per_tusd = float(ref_quote["price1_per_0"] or 0.0)
    asset = str(proposal["asset"]).lower()
    if action == "buy":
        if asset == "trwa":      # spend tUSD to receive size tRWA
            spend = size * price_tusd_per_trwa
            return market.TOKEN_TUSD, market.TOKEN_TRWA, round(spend, 6), size
        # buy tUSD: spend tRWA to receive size tUSD
        spend = size * price_trwa_per_tusd
        return market.TOKEN_TRWA, market.TOKEN_TUSD, round(spend, 6), size
    if asset == "trwa":          # sell size tRWA -> receive tUSD
        expected = size * price_tusd_per_trwa
        return market.TOKEN_TRWA, market.TOKEN_TUSD, size, round(expected, 6)
    # sell tUSD -> receive tRWA
    expected = size * price_trwa_per_tusd
    return market.TOKEN_TUSD, market.TOKEN_TRWA, size, round(expected, 6)


def main() -> int:
    ks = KillSwitch()
    daily = DailyCount()
    audit = AuditLog()

    print(f"Weryon — LIVE CYCLE DEMO   ({now()})\n")

    if ks.is_armed():
        print(f"FATAL: kill switch is ARMED ({ks.state().get('reason')}) — "
              f"disarm it (logs/killswitch.json) before running this demo.")
        return 1

    # ------------------------------------------------------------------ #
    # [1] Sector 1 — live wallet snapshot                                #
    # ------------------------------------------------------------------ #
    step("[1/5] Sector 1 — live wallet snapshot (read-only RPC)")
    print("  connecting to Robinhood Chain testnet ...")
    w3 = wallet.connect(wallet.NETWORKS[NETWORK][0])
    acct = wallet.project_account(w3)
    print(f"  network        = {NETWORK}  chain_id={w3.eth.chain_id}  "
          f"block={w3.eth.block_number}")
    print(f"  wallet         = {acct.address}")
    snap = wallet.get_full_snapshot(w3, acct.address, tokens=TOKENS,
                                    network=NETWORK)
    print(f"  snapshot       = {snap.queried_at} (live read, "
          f"{len(snap.balances)} balances)")
    for b in snap.balances:
        print(f"    {b.symbol:>5}  {b.human:.6f}  (raw {b.raw})")

    # ------------------------------------------------------------------ #
    # [2] Sector 1 — live market quote (tUSD/tRWA pool)                   #
    # ------------------------------------------------------------------ #
    step("[2/5] Sector 1 — live market quote (Uniswap v4, direct state read)")
    q_ref = market.get_quote(w3, market.TOKEN_TUSD, market.TOKEN_TRWA,
                             QUOTE_AMOUNT_TUSD, network=NETWORK)
    if not q_ref.ok:
        print(f"  FATAL: live pool quote unavailable: "
              f"{q_ref.error_type}: {q_ref.error}")
        return 1
    print(f"  pool           = 0x{q_ref.pool_id[:12]}... "
          f"(liq {q_ref.liquidity}, sqrt {q_ref.sqrt_price_x96})")
    print(f"  {q_ref.token_in_symbol} -> {q_ref.token_out_symbol}: "
          f"{q_ref.amount_in_human} in -> {q_ref.amount_out_human} out")
    print(f"  price          = 1 tRWA ~ {q_ref.price0_per_1:.4f} tUSD  |  "
          f"1 tUSD ~ {q_ref.price1_per_0:.6f} tRWA")

    # ------------------------------------------------------------------ #
    # [3] + [4] Sector 2 proposal -> Sector 3 gate.
    # SERV's confidence is stochastic; a proposal the confidence fence
    # catches (held_low_confidence) is real and is LOGGED as it happened,
    # then a brand-new real SERV proposal is asked for (bounded retry). A
    # blocked / killed / pending verdict ends the loop immediately.
    # ------------------------------------------------------------------ #
    engine = Guardrail(policy=POLICY, kill_switch=ks, daily=daily, audit=audit)

    priced_total, priced, unpriced = priced_wallet_value(
        snap.to_dict(), float(q_ref.price0_per_1 or 1.0))
    wallet_value = priced_total if priced_total > 0 else DEMO_FUNDING_BASE_TUSD
    approved_today = daily.pending()

    MAX_PROPOSAL_ATTEMPTS = 6
    attempt = 0
    while True:
        attempt += 1
        step(f"[3/5] Sector 2 — SERV reasoning "
             f"(real proposal call, attempt {attempt})")
        print(f"  strategy       = approved {DEMO_STRATEGY['approved']}, "
              f"max_size {DEMO_STRATEGY['max_proposal_size']}")
        res = propose_trade(snap.to_dict(), q_ref.to_dict(),
                            strategy=DEMO_STRATEGY, timeout=120)
        if not res.ok or res.proposed is None:
            print(f"  SERV did not produce a proposal: "
                  f"[{res.error_type}] {res.error}")
            if attempt >= MAX_PROPOSAL_ATTEMPTS:
                print("\n  Retry budget exhausted without a valid proposal. "
                      "Nothing to gate or log — exiting (only already-logged "
                      "fence-catch rows above remain).")
                return 1
            print(f"  Treating as one failed attempt ({attempt}/"
                  f"{MAX_PROPOSAL_ATTEMPTS}); asking SERV again ...")
            continue
        d = res.proposed
        print(f"  model          = {res.model}  latency_ms={res.latency_ms}")
        print(f"  PROPOSAL       = {d.action.upper()} {d.asset} "
              f"size={d.size} conf={d.confidence:.2f}")
        print(f"    rationale    : {d.rationale}")
        print(f"    risk_flags   : {d.risk_flags}")

        step(f"[4/5] Sector 3 — guardrail hard-rule checks "
             f"(proposal #{attempt})")
        print(f"  wallet_value   = {wallet_value:.2f} tUSD")
        if priced:
            print("    (priced from snapshot: " + ", ".join(priced) + ")")
        if unpriced:
            print("    (unpriced, excluded:   " + "; ".join(unpriced) + ")")
        if priced_total <= 0:
            print(f"    NOTE: snapshot priced portion is $0 -> using demo "
                  f"funding base {DEMO_FUNDING_BASE_TUSD:,.0f} tUSD (matches "
                  f"recorded ledger semantics).")
        print(f"  daily count    = {approved_today}/{POLICY['max_daily_trades']} "
              f"approved today (before this run)")
        print(f"  kill switch    = {'ARMED' if ks.is_armed() else 'off'}")

        quote_amount_out = None
        expected_amount_out = None
        amount_in_human = None
        fpq = forward_quote_params(d.to_dict(), q_ref.to_dict())
        if fpq is not None:
            tin, tout, spend, expected = fpq
            sym_in = "tUSD" if tin == market.TOKEN_TUSD else "tRWA"
            sym_out = "tUSD" if tout == market.TOKEN_TUSD else "tRWA"
            print(f"  slippage source= live forward quote {sym_in} -> {sym_out} "
                  f"(in {spend})")
            fwd = market.get_quote(w3, tin, tout, spend, network=NETWORK)
            if fwd.ok:
                quote_amount_out = fwd.amount_out_human
                expected_amount_out = str(expected)
                amount_in_human = str(spend)
                print(f"    live quote   : in {spend} -> out "
                      f"{fwd.amount_out_human} (block {fwd.block_number})")
                print(f"    expected out : {expected}")

        v1 = engine.guarded(res, wallet_value=wallet_value,
                            approved_today=approved_today,
                            quote_amount_out_human=quote_amount_out,
                            expected_amount_out_human=expected_amount_out,
                            amount_in_human=amount_in_human,
                            latency=res.latency_ms)

        print("  --- hard-rule gate (order fixed by Sector 3) ---")
        for c in v1.hard_checks:
            mark = "PASS" if c.ok else "FAIL"
            print(f"    [{mark}] {c.rule}: {c.detail}")
        floor = float(POLICY.get("confidence_floor", 0.0))
        conf_ok = d.confidence >= floor
        print(f"    [{'PASS' if conf_ok else 'FAIL'}] confidence_floor: "
              f"{d.confidence:.2f} >= floor {floor:.2f}")
        print(f"  verdict        = {v1.verdict}"
              + (f"  ({v1.error_type}: {v1.error})" if v1.error else ""))

        if v1.verdict == V_HOLD and attempt < MAX_PROPOSAL_ATTEMPTS:
            audit.append(verdict_to_log_row(v1))
            held_lines = sum(1 for r in audit.read_all()
                             if r.get("verdict") == "held_low_confidence")
            print(f"\n  CONFIDENCE FENCE: proposal #{attempt} held "
                  f"(conf {d.confidence:.2f} < floor {floor:.2f}). "
                  f"This fence-catch is logged as a held_low_confidence row "
                  f"(ledger held rows now {held_lines}).")
            print("  Asking SERV for a NEW real proposal ...")
            continue
        break

    # ------------------------------------------------------------------ #
    # [5] Sector 3 — human approval prompt + audit log                    #
    # ------------------------------------------------------------------ #
    step("[5/5] Sector 3 — human approval + audit log")
    if v1.verdict == V_PENDING:
        try:
            answer = prompt_approval(v1)      # the real CLI y/N prompt
        except EOFError:
            answer = False                    # no input = fail-closed NO
            print("  (no input received — treated as NO per fail-closed)")
        final = engine.approve_or_blocked(v1, human_approved=answer)
        print(f"  human said    = {'YES' if answer else 'NO'} "
              f"-> final verdict {final.verdict}")
    elif v1.verdict == V_HOLD:
        final = v1
        print("  NOT presented for approval "
              f"(verdict={v1.verdict}: low confidence hold).")
    else:
        final = v1
        print(f"  NOT presented for approval (verdict={v1.verdict}).")

    row = verdict_to_log_row(final)
    audit.append(row)
    print(f"\n  appended row to logs/sector3_audit.jsonl:")
    print("    " + json.dumps(row))

    if final.verdict == V_APPROVED:
        daily.record(asset=d.asset, size=float(d.size))
        print(f"  daily count now = {daily.pending()}/{POLICY['max_daily_trades']}")

    print("\n  NO EXECUTION: approval was logged only — nothing signed, "
          "nothing broadcast.")
    print("  Refresh  http://localhost:8899/ui/  to see the new row in the "
          "decision ledger.")
    total = len(audit.read_all())
    vcounts = {}
    for r in audit.read_all():
        vcounts[r["verdict"]] = vcounts.get(r["verdict"], 0) + 1
    print(f"  ledger now    = {total} rows  {vcounts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())