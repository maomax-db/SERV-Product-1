"""Weryon — server.py: the live cycle in the browser.

A tiny local Flask server that exposes the EXACT Sector 1/2/3 pipeline the
terminal demo (demo/live_run.py) runs, over three JSON endpoints, and serves
the existing static dashboard (ui/) plus the real logs/ folder:

    POST /api/propose   steps 1-4 of demo/live_run.py: live wallet snapshot,
                        live Uniswap v4 quote, a REAL SERV proposal, and the
                        Sector 3 guardrail gate (hard rules + confidence fence)
                        with the same bounded real-proposal retry (max 6 fresh
                        SERV asks; low-confidence fence-catches logged as they
                        happen). Returns the gated proposal + every rule check.
                        A proposal that passes every hard rule is held in
                        memory as a pending verdict. Nothing is auto-approved
                        and nothing is executed here.
    POST /api/approve   human yes/no for a proposal_id -> engine.approve_or_
                        blocked(human_approved=...) — the SAME Sector 3
                        approval path live_run.py calls after its CLI prompt —
                        then verdict_to_log_row -> AuditLog.append (and today's
                        DailyCount on approval). Still no executor, no signing.
    GET  /api/ledger    the current real decision ledger + kill-switch state,
                        so the page can redraw without a manual file reload.

The private key lives ONLY where it already lives: .env -> agent-core/config
-> chain/wallet.project_account() which derives just the read-only address for
the snapshot. This process never imports execution/executor.py and offers no
route that signs or broadcasts anything. View + approve only, exactly like
demo/live_run.py.

Usage:  python server.py [port]
        open http://127.0.0.1:8899/ui/
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (str(ROOT), str(ROOT / "chain"), str(ROOT / "agent-core"),
           str(ROOT / "guardrail")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Single source of truth for the demo pipeline semantics — live_run.py's own
# helpers, untouched. Reused so the API and the terminal demo cannot drift.
from demo.live_run import (  # noqa: E402
    DEMO_FUNDING_BASE_TUSD, DEMO_STRATEGY, NETWORK, POLICY, QUOTE_AMOUNT_TUSD,
    TOKENS, forward_quote_params, priced_wallet_value,
)

from chain import market, wallet  # noqa: E402
from decision import propose_trade  # noqa: E402
from flask import Flask, jsonify, redirect, request, send_from_directory  # noqa: E402
from guardrail.engine import (  # noqa: E402
    Guardrail, V_APPROVED, V_HOLD, V_PENDING, verdict_to_log_row,
)
from guardrail.dailytrades import DailyCount  # noqa: E402
from guardrail.killswitch import KillSwitch  # noqa: E402
from guardrail.logbook import AuditLog  # noqa: E402

MAX_PROPOSAL_ATTEMPTS = 6          # same bounded real-proposal retry as live_run
CONFIDENCE_FLOOR = float(POLICY.get("confidence_floor", 0.0))

app = Flask(__name__, static_folder="ui", static_url_path="/ui")

# Module-level Sector 3 state, shared by every request (same singletons the
# terminal demo builds once in main()).
ks = KillSwitch()
daily = DailyCount()
audit = AuditLog()
engine = Guardrail(policy=POLICY, kill_switch=ks, daily=daily, audit=audit)

# proposal_id -> ApprovalVerdict that passed every hard rule and awaits the
# human. In-memory only; never written, never contains secrets.
PENDING: dict = {}


# --------------------------------------------------------------------------- #
# Sector 1 + Sector 2 + Sector 3 stages 1-4 — the SAME calls live_run makes.
# --------------------------------------------------------------------------- #
def run_propose() -> dict:
    if ks.is_armed():
        return {"ok": False,
                "error": "kill switch is ARMED — disarm it (logs/killswitch.json) "
                         "before proposing."}
    w3 = wallet.connect(wallet.NETWORKS[NETWORK][0])
    acct = wallet.project_account(w3)                 # read-only address derive
    snap = wallet.get_full_snapshot(w3, acct.address, tokens=TOKENS,
                                    network=NETWORK)
    q_ref = market.get_quote(w3, market.TOKEN_TUSD, market.TOKEN_TRWA,
                             QUOTE_AMOUNT_TUSD, network=NETWORK)
    if not q_ref.ok:
        return {"ok": False,
                "error": f"live pool quote unavailable: "
                         f"{q_ref.error_type}: {q_ref.error}"}

    priced_total, priced, unpriced = priced_wallet_value(
        snap.to_dict(), float(q_ref.price0_per_1 or 1.0))
    wallet_value = priced_total if priced_total > 0 else DEMO_FUNDING_BASE_TUSD
    approved_today = daily.pending()

    attempt = 0
    while True:
        attempt += 1
        res = propose_trade(snap.to_dict(), q_ref.to_dict(),
                            strategy=DEMO_STRATEGY, timeout=120)
        if not res.ok or res.proposed is None:
            if attempt >= MAX_PROPOSAL_ATTEMPTS:
                return {"ok": False,
                        "error": f"SERV returned nothing schema-valid after "
                                 f"{attempt} asks ({res.error_type}: "
                                 f"{res.error}). No verdict exists — no row "
                                 f"appended."}
            continue                                     # transient flake: retry
        d = res.proposed

        quote_amount_out = expected_amount_out = amount_in_human = None
        slippage = None
        fpq = forward_quote_params(d.to_dict(), q_ref.to_dict())
        if fpq is not None:
            tin, tout, spend, expected = fpq
            fwd = market.get_quote(w3, tin, tout, spend, network=NETWORK)
            if fwd.ok:
                quote_amount_out = fwd.amount_out_human
                expected_amount_out = str(expected)
                amount_in_human = str(spend)
                slippage = {
                    "in_symbol": fwd.token_in_symbol,
                    "out_symbol": fwd.token_out_symbol,
                    "amount_in": spend,
                    "live_out": fwd.amount_out_human,
                    "expected_out": expected,
                    "block": fwd.block_number,
                }

        v1 = engine.guarded(res, wallet_value=wallet_value,
                            approved_today=approved_today,
                            quote_amount_out_human=quote_amount_out,
                            expected_amount_out_human=expected_amount_out,
                            amount_in_human=amount_in_human,
                            latency=res.latency_ms)

        if v1.verdict == V_HOLD and attempt < MAX_PROPOSAL_ATTEMPTS:
            audit.append(verdict_to_log_row(v1))        # real fence-catch, logged
            continue                                    # ask SERV for a new one
        break

    payload = {
        "ok": True,
        "verdict": v1.verdict,
        "error": v1.error,
        "error_type": v1.error_type,
        "decision": v1.decision.to_dict() if v1.decision else None,
        "model": res.model,
        "latency_ms": res.latency_ms,
        "hard_checks": [{"rule": c.rule, "ok": c.ok, "detail": c.detail}
                    for c in v1.hard_checks],
        "confidence_floor": CONFIDENCE_FLOOR,
        "slippage": slippage,
        "wallet": {
            "address": acct.address,
            "value": round(wallet_value, 2),
            "funding_fallback": priced_total <= 0,
            "priced": priced,
            "unpriced": unpriced,
        },
        "daily": {"approved_today": approved_today,
                  "max": int(POLICY["max_daily_trades"])},
        "kill_switch": ks.is_armed(),
        "snapshot": {
            "queried_at": snap.queried_at,
            "chain_id": int(snap.chain_id),
            "block_number": int(snap.block_number),
            "balances": [{"symbol": b.symbol, "human": b.human,
                          "raw": str(b.raw)} for b in snap.balances],
        },
        "quote": {
            "pool_id": q_ref.pool_id,
            "amount_in_human": q_ref.amount_in_human,
            "amount_out_human": q_ref.amount_out_human,
            "price0_per_1": q_ref.price0_per_1,
            "price1_per_0": q_ref.price1_per_0,
        },
    }

    if v1.verdict != V_PENDING:
        # Exactly live_run step 5 for a non-approval terminal verdict: the
        # decision happened (blocked / killed / exhausted-hold) and is logged.
        audit.append(verdict_to_log_row(v1))
        payload["row_logged"] = True
    else:
        pid = secrets.token_hex(4)
        PENDING[pid] = v1
        payload["proposal_id"] = pid
        payload["row_logged"] = False
    return payload


# --------------------------------------------------------------------------- #
# Routes.
# --------------------------------------------------------------------------- #
@app.get("/")
def home():
    return redirect("/ui/")


@app.get("/ui/")
def ui_home():
    return redirect("/ui/index.html")


@app.get("/logs/<path:filename>")
def logs_file(filename: str):
    return send_from_directory(ROOT / "logs", filename, as_attachment=False)


@app.post("/api/propose")
def api_propose():
    try:
        return jsonify(run_propose())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.post("/api/approve")
def api_approve():
    body = request.get_json(silent=True) or {}
    pid = body.get("proposal_id")
    v1 = PENDING.pop(pid, None) if pid else None
    if v1 is None:
        return jsonify({"ok": False, "error": "unknown or stale proposal_id — "
                                              "click Propose first."}), 404
    human_approved = body.get("decision") in ("approve", "yes", "accept", True, "1")
    final = engine.approve_or_blocked(v1, human_approved=human_approved)
    row = verdict_to_log_row(final)
    audit.append(row)
    resp = {"ok": True, "final": row}
    if final.verdict == V_APPROVED and final.decision is not None:
        daily.record(asset=final.decision.asset,
                     size=float(final.decision.size))
        resp["daily_pending"] = daily.pending()
    return jsonify(resp)


@app.get("/api/ledger")
def api_ledger():
    killswitch = None
    try:
        ks_path = ROOT / "logs" / "killswitch.json"
        if ks_path.exists():
            killswitch = json.loads(ks_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        killswitch = None
    return jsonify({"rows": audit.read_all(), "killswitch": killswitch})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f"Weryon — interactive cycle at http://127.0.0.1:{port}/ui/")
    print("view + approve only — no signing, no broadcast, no executor import.")
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()