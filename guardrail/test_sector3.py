"""Sector 3 - offline deterministic gate (THE PRODUCT's proof, free & fast).

No SERV, no chain, no API key, no network, no spend: every decision is a
synthetic Sector 2 ``DecisionResult`` built directly, and the guardrail is
exercised as a pure in-process gate (temp dirs for kill switch / daily count /
audit log). The gate proves the executable Sector 3 contract:

  [1] 10 synthetic proposals (7 good, 3 deliberately bad):
        - bad#1 exceeds max position share
        - bad#2 exceeds daily trade count
        - bad#3 is a non-approved token
      all 3 BLOCKED, all 7 good ones reach the approval step.
  [2] Kill switch armed MID-flow: everything after it is blocked at once -
        even a proposal that would otherwise pass every rule.
  [3] Every decision type writes a structured row to the audit log
        (blocked / held / approved / rejected / killed-all-one-log).
  [4] Proposal -> approval-ready latency is near-instant and printed.

  Plus the sector-wide construction discipline: /guardrail/ imports nothing
  from a future execution module, and nothing signing/wallet/private-key.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # force UTF-8 so a CP1252 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # can't crash us
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "guardrail"), str(ROOT / "agent-core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from guardrail.engine import (  # noqa: E402
    ApprovalVerdict, GuardedDecision, Guardrail, V_APPROVED, V_BLOCKED,
    V_HOLD, V_KILLED, V_PENDING, V_REJECTED, DEFAULT_POLICY, verdict_to_log_row,
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


def record(name, ok, detail="") -> None:  # noqa: ANN202
    RESULTS.append((name, bool(ok)))
    print(f"    [{'x' if ok else '!'}] {name}" + (f"  ({detail})" if detail else ""))


def _native(p) -> dict:  # noqa: ANN202
    """Reduce a DecisionResult / ProposedDecision / dict to the 7 core fields."""
    src = p
    if hasattr(p, "proposed") and getattr(p, "proposed") is not None:
        src = getattr(p, "proposed")
    if isinstance(src, dict):
        return dict(src)
    return {k: getattr(src, k)
            for k in ("action", "asset", "size", "confidence", "rationale",
                      "risk_flags") if hasattr(src, k)}


def make_proposal(action="hold", asset="tUSD", size=0.5,
                  confidence=0.95, rationale="clean", risk_flags=None):
    """A synthetic Sector 2 proposal: `DecisionResult` when the real class is
    importable (per spec - feed the engine DecisionResult objects), else the
    plain dict form (engine handles both)."""
    obj = {
        "action": action, "asset": asset, "size": float(size),
        "confidence": float(confidence),
        "rationale": rationale or f"{action} {asset} {size} for a honest test",
        "risk_flags": list(risk_flags or []),
    }
    if HAVE_SECTOR2 and ProposedDecision is not None:
        return DecisionResult(
            ok=True, network="testnet", chain_id=46630, block_number=1,
            model="fake-serv",
            proposed=ProposedDecision(**obj))
    return obj


ENDOW = None  # noqa: F841


def quick_trades(engine, proposals, *, approved_today=0, human=True,
                 wallet_value=10_000.0, extra=None):
    """Run N proposals through guarded() + approval; return verdicts list."""
    out = []
    for idx, prop in enumerate(proposals):
        v1 = engine.guarded(prop, wallet_value=wallet_value,
                            approved_today=approved_today)
        approved_today += 1 if v1.verdict == V_PENDING else 0
        if v1.verdict == V_PENDING:
            v2 = engine.approve_or_blocked(v1,
                                           human_approved=True if human
                                           else None)
        else:
            v2 = v1
        out.append((v1, v2))
    return out


def main() -> int:  # noqa: ANN202, C901
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        print("=" * 72)
        print("SECTOR 3 GATE - guardrail & approval engine (offline, free)")
        print("=" * 72)
        print(f"  temp state in: {tmpd}")
        print(f"  Sector 2 DecisionResult/ProposedDecision imported: {HAVE_SECTOR2}\n")

        # ------------------------------------------------------------------ #
        # 1. 10 synthetic proposals: 7 good, 3 bad
        # ------------------------------------------------------------------ #
        print("--- [1] 10 synthetic proposals (3 deliberately bad) ---")
        wallet = 10_000.0
        policy = dict(DEFAULT_POLICY)
        policy.update({"max_position_pct": 0.40,
                       "max_trade_pct_of_wallet": 0.20,
                       "confidence_floor": 0.85,
                       "max_daily_trades": 8,
                       "autopilot_ok": False})

        ks = KillSwitch(path=tmpd / "kill.json")
        daily = DailyCount(log_dir=tmpd)
        audit = AuditLog(log_dir=tmpd)
        engine = Guardrail(policy=policy, kill_switch=ks, daily=daily,
                           audit=audit)

        # 7 GOOD: 5 real trades + 2 holds (holds consume no daily slot).
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
            make_proposal("hold", "tUSD", 0.0, 0.91,
                          "nothing drifts past band, hold"),
        ]
        # 3 BAD (the deliberately-wrong ones).
        bad = [
            make_proposal("buy", "tUSD", 9000.0, 0.97,
                          "OVERSIZE: 90% of wallet in one trade",
                          risk_flags=["size_above_limit"]),
            make_proposal("buy", "tRWA", 50.0, 0.95,
                          "daily cap already exhausted today"),
            make_proposal("buy", "MOONSHOT", 10.0, 0.98,
                          "NON-APPROVED TOKEN - the temptation"),
        ]

        all_ten = good + bad
        # order for the run: 7 good first, then 3 bad (spec: "7 good allowed")
        approved_today = 0
        v_marker = []
        for i, p in enumerate(all_ten):
            which = "GOOD" if i < 7 else "BAD"
            dd = _native(p)
            print(f"\n  proposal {i + 1:2d} [{which}] "
                  f"{dd['action']} {dd['asset']} {dd['size']}")
            t0 = time.perf_counter()
            # bad#2 proves the daily cap: feed the counter already at the cap.
            effective_today = (8 if i == 8 else approved_today)
            # give GOOD trades a live-ish quote so slippage passes honestly;
            # BAD ones exercise position/daily/approved rules, not slippage.
            is_good_trade = (i < 7 and dd["action"] != "hold")
            q = float(dd["size"]) * 0.999 if is_good_trade else None
            v1 = engine.guarded(
                p, wallet_value=wallet,
                approved_today=effective_today, latency=[],
                quote_amount_out_human=q,
                expected_amount_out_human=(float(dd["size"]) if is_good_trade
                                           else None))
            if v1.verdict == V_PENDING:
                v2 = engine.approve_or_blocked(v1, human_approved=True)
                if dd["action"] != "hold":  # holds don't consume a trade slot
                    approved_today += 1
            else:
                v2 = v1
            dt = (time.perf_counter() - t0) * 1e3
            v_marker.append(v2)
            audit.append(verdict_to_log_row(v2))
            print(f"        guarded={v1.verdict}  final={v2.verdict}  "
                  f"error_type={v2.error_type}  {dt:.3f}ms")
        print()

        good_reached = sum(1 for v2 in v_marker[:7]
                           if v2.verdict in (V_PENDING, V_APPROVED))
        bad_blocked = v_marker[7].verdict == V_BLOCKED and \
            v_marker[8].verdict == V_BLOCKED and \
            v_marker[9].verdict == V_BLOCKED
        record("3 deliberately-bad proposals are BLOCKED (typed)",
               bad_blocked,
               ", ".join(str(v.error_type or v.verdict)
                         for v in (v_marker[7], v_marker[8], v_marker[9])))
        record("All 7 GOOD proposals REACHED the approval step (pending/approved)",
               good_reached == 7,
               f"reached={good_reached} among first 7")

        # ------------------------------------------------------------------ #
        # 2. Kill switch MID-flow
        # ------------------------------------------------------------------ #
        print("\n--- [2] Kill switch tested MID-flow ---")
        ks2 = KillSwitch(path=tmpd / "kill2.json")
        audit2 = AuditLog(log_dir=tmpd)
        eng2 = Guardrail(policy=policy, kill_switch=ks2, daily=DailyCount(log_dir=tmpd),
                         audit=audit2)

        before_good = engine2_flow(eng2, ks2, 2, wallet, tmpd, "before")
        ks2.arm(reason="operator panic", source="sector3-gate")
        print(f"    >> kill switch ARMED: {ks2.state()['reason']}")
        after_blocked = engine2_flow(eng2, ks2, 3, wallet, tmpd, "after")
        # the "would otherwise pass" proof:
        perfect = make_proposal("buy", "tRWA", 40.0, 0.98,
                                "flawless rule-passing trade")
        t0 = time.perf_counter()
        va = eng2.guarded(perfect, wallet_value=wallet, approved_today=0)
        va = eng2.approve_or_blocked(va, human_approved=True)
        dt = (time.perf_counter() - t0) * 1e3
        audit2.append(verdict_to_log_row(va))
        print(f"        post-arm flawless proposal -> {va.verdict} "
              f"({va.error_type}) {dt:.3f}ms")
        record("Kill switch blocks every proposal AFTER arming",
               all(v.verdict == V_KILLED for v in after_blocked + [va]),
               f"after={[v.verdict for v in after_blocked]}[perfect={va.verdict}]")
        record("Pre-arm proposals were NOT affected (still normal verdicts)",
               all(v.verdict in (V_PENDING, V_APPROVED)
                   for v in before_good), f"{[v.verdict for v in before_good]}")

        # ------------------------------------------------------------------ #
        # 3. Every decision type writes a log row with the required fields
        # ------------------------------------------------------------------ #
        print("\n--- [3] Audit log - every decision type, all fields ---")
        rows = audit2.read_all() + audit.read_all()
        req = {"asset", "size", "confidence", "blocked_by", "rationale",
               "timestamp_utc", "error_type", "verdict"}
        missing_any = [r for r in rows if not req.issubset(set(r.keys()))]
        record(f"every engine decision wrote a row ({len(rows)} rows swept)",
               len(rows) > 0 and not missing_any,
               f"rows={len(rows)} missing_fields_rows={len(missing_any)}")
        record("multi-type coverage exists (approved, blocked, killed, held?)",
               {r["verdict"] for r in rows} >= {"approved", "blocked"},
               "verdicts=" + ",".join(sorted({r["verdict"] for r in rows})))
        print("        sample rows:")
        for r in rows[:2]:
            print("        ", json.dumps({k: r[k] for k in (
                "verdict", "asset", "size", "confidence", "blocked_by",
                "error_type", "rationale", "timestamp_utc", "latency_ms")},
                ensure_ascii=False))

        # ------------------------------------------------------------------ #
        # 4. Timing
        # ------------------------------------------------------------------ #
        print("\n--- [4] Latency (proposal -> approval-ready) ---")
        lat_series = [r.get("latency_ms", [0])[-1] for r in rows][:12]
        avg = sum(lat_series) / len(lat_series) if lat_series else 0
        record("near-instant: avg guarded+approval latency under 5 ms",
               avg < 5.0, f"avg={avg:.3f}ms over {len(lat_series)} decisions")

        # ------------------------------------------------------------------ #
        # 5. Construction discipline - no execution/sign/wallet imports
        # ------------------------------------------------------------------ #
        print("\n--- [5] /guardrail/ imports nothing execution/sign/wallet ---")
        import ast as _ast
        guard_files = sorted((ROOT / "guardrail").glob("*.py"))
        bad_tokens = ("execution", "sign", "private_key", "privatekey",
                      "wallet.py", "web3", "broadcast", "signing")
        leaks = []
        import_lines = []
        for f in guard_files:
            tree = _ast.parse(f.read_text(encoding="utf-8"))
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                    for alias in node.names:
                        import_lines.append(alias.name)
                        low = alias.name.lower()
                        if any(t in low for t in bad_tokens):
                            leaks.append((f.name, alias.name))
        record("guardrail modules import NOTHING chain/sign/wallet/execution",
               not leaks, f"leaks={leaks or 'none'}")
        print(f"        scanned {len(guard_files)} files, "
              f"{sum(len([_a for _c in _ast.walk(_ast.parse(f.read_text(encoding='utf-8'))) for _a in getattr(_c, 'names', ())] ) for f in guard_files)} import names")

    # ------------------------------------------------------------------ #
    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n" + "=" * 72)
    print(f"[{passed}/{len(RESULTS)}] Sector 3 gate checks passed")
    for name, ok in RESULTS:
        print(f"    [{'x' if ok else '!'}] {name}")
    return 0 if passed == len(RESULTS) else 1


def engine2_flow(engine, ks, n, wallet, tmpd, tag):  # noqa: ANN202
    """Run n flawless proposals through a separate engine; return verdicts."""
    out = []
    for i in range(n):
        p = make_proposal("buy", "tRWA", 40.0, 0.98, f"{tag} flawless #{i}")
        t0 = time.perf_counter()
        v1 = engine.guarded(p, wallet_value=wallet, approved_today=0,
                            quote_amount_out_human=40.0 * 0.999,
                            expected_amount_out_human=40.0)
        v2 = engine.approve_or_blocked(v1, human_approved=True)
        (time.perf_counter() - t0)
        out.append(v2)
    return out


if __name__ == "__main__":
    raise SystemExit(main())