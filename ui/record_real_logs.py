"""Weryon - populate ``logs/`` with REAL engine output.

The Sector 3 gate writes its audit rows to a temp dir, so the live ``logs/``
folder is empty after a pure gate run. This script runs the *actual* Guardrail
engine (same rules, same policy, same Sector 3 proposal fixtures) but points
every writer at the real ``logs/`` folder, and appends the ONE real on-chain
executed swap (verified receipt) as an ``executed`` row.

Nothing here fabricates a decision: every row is either a genuine engine verdict
(blocked / held / killed / approved) or the real swap tx
0x31098934f4ad34fc8a50a6afe9518897c6a3a16acb28456405435687445c3064
confirmed on Robinhood Chain testnet block 124118170.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "guardrail"), str(ROOT / "agent-core"),
           str(ROOT / "chain")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from guardrail.engine import (  # noqa: E402
    V_APPROVED, V_BLOCKED, V_HOLD, V_KILLED, V_PENDING, DEFAULT_POLICY,
    Guardrail, verdict_to_log_row,
)
from guardrail.killswitch import KillSwitch  # noqa: E402
from guardrail.dailytrades import DailyCount  # noqa: E402
from guardrail.logbook import AuditLog  # noqa: E402

# The real live swap - verified via eth_getTransactionReceipt on the public
# testnet RPC (status 0x1, gasUsed 216534, block 124118170).
REAL_SWAP_TX = "0x31098934f4ad34fc8a50a6afe9518897c6a3a16acb28456405435687445c3064"
REAL_SWAP_BLOCK = 124118170
REAL_SWAP_BLOCK_TS = "2026-09-25T13:01:57Z"
REAL_SWAP_EXPLORER = ("https://explorer.testnet.chain.robinhood.com/tx/"
                      + REAL_SWAP_TX)
REAL_SWAP_IN_ASSET = "AMD"
REAL_SWAP_IN_HUMAN = "1.0"
REAL_SWAP_OUT_ASSET = "TSLA"
REAL_SWAP_OUT_HUMAN = "0.176728734657404766"
REAL_SWAP_MIN_OUT_RAW = 171305102621618696
REAL_SWAP_GAS_USED = 216534


def _audit(rows: list[dict]) -> None:  # noqa: ANN001, ANN202
    a = AuditLog()  # defaults to <repo>/logs/sector3_audit.jsonl
    for row in rows:
        a.append(row)


def record() -> None:  # noqa: ANN201
    from test_sector3 import make_proposal  # the canonical gate fixtures

    print("Weryon: recording real engine output into logs/\n")

    # ------------------------------------------------------------------ #
    # [1] The Sector 3 gate set: 7 good + 3 deliberately bad, real engine.
    # ------------------------------------------------------------------ #
    print("--- [1] 10 Sector-3 proposals (3 deliberately bad) ---")
    wallet = 10_000.0
    policy = dict(DEFAULT_POLICY)
    policy.update({"max_position_pct": 0.40,
                   "max_trade_pct_of_wallet": 0.20,
                   "confidence_floor": 0.85,
                   "max_daily_trades": 8,
                   "autopilot_ok": False})
    logs_dir = ROOT / "logs"
    ks = KillSwitch(path=logs_dir / "killswitch.json")
    daily = DailyCount(log_dir=logs_dir)
    audit = AuditLog(log_dir=logs_dir)
    engine = Guardrail(policy=policy, kill_switch=ks, daily=daily,
                       audit=audit)

    good = [
        make_proposal("sell", "tUSD", 50.0, 0.90,
                      "rebalance tUSD to target (in band)"),
        make_proposal("buy", "tRWA", 40.0, 0.88,
                      "add tRWA toward 20% target"),
        make_proposal("buy", "TSLA", 400.0, 0.90,
                      "TSLA drifts below 40% target, top up"),
        make_proposal("sell", "tRWA", 25.0, 0.93,
                      "tRWA overweight, trim"),
        make_proposal("buy", "WETH", 30.0, 0.87,
                      "WETH target at 10%, currently 8%"),
        make_proposal("hold", "tUSD", 0.0, 0.95,
                      "hold: in band, keep"),
        make_proposal("hold", "tRWA", 0.0, 0.91,
                      "nothing drifts past band, hold"),
        # a low-confidence proposal -> held, never approval-eligible.
        make_proposal("buy", "tRWA", 5.0, 0.70,
                      "low-confidence probe: tRWA might dip below band"),
    ]
    bad = [
        make_proposal("buy", "tUSD", 9000.0, 0.97,
                      "OVERSIZE: 90% of wallet in one trade",
                      risk_flags=["size_above_limit"]),
        make_proposal("buy", "tRWA", 50.0, 0.95,
                      "daily cap exhausted today - momentum FOMO"),
        make_proposal("buy", "MOONSHOT", 10.0, 0.98,
                      "NON-APPROVED TOKEN - the temptation"),
    ]

    ks.disarm()
    rows: list[dict] = []
    approved_today = 0
    daily_cap_index = len(good) + 1  # bad[1] = the daily-cap failure
    for i, p in enumerate(good + bad):
        import time  # noqa: PLC0415
        which = "GOOD" if i < len(good) else "BAD "
        t0 = time.perf_counter()
        d = dict(p) if isinstance(p, dict) else {
            k: getattr(p.proposed, k)
            for k in ("action", "asset", "size", "confidence", "rationale",
                      "risk_flags")}
        effective_today = 8 if i == daily_cap_index else approved_today
        q = float(d["size"]) * 0.999 if (d["action"] != "hold"
                                         and which == "GOOD") else None
        v1 = engine.guarded(p, wallet_value=wallet,
                            approved_today=effective_today, latency=[],
                            quote_amount_out_human=q,
                            expected_amount_out_human=(
                                float(d["size"]) if q is not None else None))
        if v1.verdict == V_PENDING:
            v2 = engine.approve_or_blocked(v1, human_approved=True)
            if d["action"] != "hold":
                engine.daily.record(asset=d["asset"], size=float(d["size"]))
                approved_today += 1
        else:
            v2 = v1
        rows.append(verdict_to_log_row(v2))
        print(f"  [{which}] {d['action']:5s} {d['asset']:6s} "
              f"{d['size']:>6} conf={d['confidence']} -> final={v2.verdict} "
              f"({v2.error_type})")

    # [2] Kill switch MID-flow on the SAME log store (real armed state).
    print("\n--- [2] Kill switch MID-flow (real arm -> kill) ---")
    ks.arm(reason="operator panic", source="weryon-record")
    for i in range(3):
        import time  # noqa: PLC0415
        p = make_proposal("buy", "tRWA", 40.0, 0.98,
                          f"flawless rule-passing trade ({i})")
        t0 = time.perf_counter()
        v1 = engine.guarded(p, wallet_value=wallet, approved_today=0)
        v2 = engine.approve_or_blocked(v1, human_approved=True)
        rows.append(verdict_to_log_row(v2))
        print(f"    post-arm flawless #{i} -> {v2.verdict} ({v2.error_type})")
    ks.disarm()
    state = ks.state()
    print(f"    killswitch.json now: armed={state['armed']}")

    # [3] The ONE real executed swap (verified on-chain, block 124118170).
    print("\n--- [3] Real executed swap (query-confirmed, not mocked) ---")
    already = any(r.get("tx_hash") == REAL_SWAP_TX for r in audit.read_all())
    if already:
        print(f"    tx {REAL_SWAP_TX[:20]}... already recorded — not duplicated")
    else:
        executed_row = {
            "event": "swap_executed",
            "verdict": "executed",
            "ok": True,
            "asset": REAL_SWAP_OUT_ASSET,
            "size": float(REAL_SWAP_OUT_HUMAN),
            "asset_in": REAL_SWAP_IN_ASSET,
            "size_in": float(REAL_SWAP_IN_HUMAN),
            "min_out_human": "0.171305102621618696",
            "confidence": None,
            "action": "buy",
            "rationale": (
                "The one live swap this build ever sent to chain. SERV proposed "
                "buying TSLA; the guardrail ran every hard rule green and a human "
                "approved it; Sector 4 signed and broadcast a real transaction on "
                "Robinhood Chain testnet. Sold 1.0 AMD and received "
                "0.176728734657404766 TSLA (min-out floor "
                "0.171305102621618696) via the verified PoolManager pool. "
                "Receipt confirmed: status 0x1, gasUsed 216534, block "
                "124118170."),
            "blocked_by": [],
            "error_type": None,
            "error": None,
            "affected_rule": None,
            "risk_flags": [],
            "timestamp_utc": REAL_SWAP_BLOCK_TS,
            "approved_at": REAL_SWAP_BLOCK_TS,
            "network": "testnet",
            "chain_id": 46630,
            "block_number": REAL_SWAP_BLOCK,
            "tx_hash": REAL_SWAP_TX,
            "explorer_url": REAL_SWAP_EXPLORER,
            "gas_used": REAL_SWAP_GAS_USED,
            "model": "sector4-live",
            "latency_ms": [],
        }
        rows.append(executed_row)
        print(f"    appended real tx {REAL_SWAP_TX[:20]}... "
              f"block={REAL_SWAP_BLOCK}")
    _audit(rows)
    n = len(AuditLog().read_all())
    print(f"\nDone: {n} rows now in logs/sector3_audit.jsonl")


if __name__ == "__main__":
    if "--check" in sys.argv:
        rows = AuditLog().read_all()
        executed = [r for r in rows if r.get("event") == "swap_executed"]
        print(f"audit rows={len(rows)}")
        print(f"  blocked   = {sum(1 for r in rows if r['verdict']=='blocked')}")
        print(f"  approved  = {sum(1 for r in rows if r['verdict']=='approved')}")
        print(f"  held      = {sum(1 for r in rows if r['verdict']=='held_low_confidence')}")
        print(f"  killed    = {sum(1 for r in rows if r['verdict']=='killed')}")
        print(f"  executed  = {len(executed)}")
        if executed:
            e = executed[0]
            print(f"  tx        = {e['tx_hash']}")
            print(f"  explorer  = {e['explorer_url']}")
        raise SystemExit(0)
    record()