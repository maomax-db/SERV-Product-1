"""Sector 3 — pure hard-rule evaluators (stateless, deterministic, offline).

Every rule in here is a **pure function of its inputs** — no I/O, no clock, no
network, no SERV, no spend. That is what makes Sector 3 testable offline with
canned proposals and reproducible byte for byte on any machine.

Each rule returns a :class:`RuleCheck`; the engine in ``engine.py`` fans a
proposal across the whole set, and a freshly parsed ``ProposedDecision`` is
*always* subject to every check. Verdicts never execute anything — this file
cannot and must not sign, broadcast, or touch a private key.

Rules implemented (the Sector 3 hard gate set):

  R1 Approved-list   — trade must be in a non-approved asset → BLOCKED.
                       Redundant with Sector 2 ON PURPOSE: independent layers
                       must enforce the same fence; you never trust one seam.
  R2 Position size   — proposed new position (abs human units, and as % of the
                       wallet's current value) must stay under the cap.
  R3 Trade vs wallet — a single trade size must not exceed the per-trade
                       wallet-share cap.
  R4 Daily count     — number of approved trades already recorded for the day
                       must stay under the daily cap.
  R5 Slippage        — quoted output vs expected output must respect the max
                       slippage tolerance.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RuleCheck:  # noqa: D101
    rule: str
    ok: bool
    detail: str = ""


def _is_positive_number(v) -> bool:  # noqa: ANN001, ANN202
    try:
        return math.isfinite(float(v)) and float(v) > 0
    except (TypeError, ValueError):
        return False


def check_approved_list(asset: str, approved: set | list) -> RuleCheck:  # noqa: ANN201
    """R1 — asset must be on the approved set (Sector 3 enforces solo too)."""
    ok = asset in set(approved)
    return RuleCheck("approved_list", ok,
                     f"asset={asset!r} in approved" if ok
                     else f"asset={asset!r} NOT on the approved list")


def check_position_size(asset: str, size: float, wallet_value: float,
                        policy: dict) -> RuleCheck:  # noqa: ANN201
    """R2 — proposed position size under both abs and %-of-wallet caps."""
    capped_points = []
    if _is_positive_number(size) and _is_positive_number(wallet_value):
        pct = size / wallet_value
        if "max_position_abs" in policy and size > float(policy["max_position_abs"]):
            capped_points.append(
                f"size={size} > max_position_abs={policy['max_position_abs']}")
        if "max_position_pct" in policy and pct > float(policy["max_position_pct"]):
            capped_points.append(
                f"{size / wallet_value:.1%} of wallet > "
                f"max_position_pct={policy['max_position_pct']:.0%}")
    ok = not capped_points
    return RuleCheck("position_size", ok,
                     "; ".join(capped_points) if capped_points
                     else f"size={size} inside position caps")


def check_trade_share(size: float, wallet_value: float, policy: dict) -> RuleCheck:  # noqa: ANN201
    """R3 — a single trade must be a bounded share of wallet value."""
    cap = policy.get("max_trade_pct_of_wallet", 1.0)
    ok = True
    detail = f"size={size} ≤ {cap:.0%} of wallet"
    if _is_positive_number(size) and _is_positive_number(wallet_value):
        pct = size / wallet_value
        ok = pct <= float(cap)
        if not ok:
            detail = (f"trade={size} ({pct:.1%} of wallet) > {cap:.0%} "
                      f"per-trade cap")
    return RuleCheck("trade_share", ok, detail)


def check_daily_count(approved_today: int, policy: dict) -> RuleCheck:  # noqa: ANN201
    """R4 — approved trades already counted today must stay under the cap."""
    cap = policy.get("max_daily_trades")
    ok = cap is None or int(approved_today) < int(cap)
    return RuleCheck("daily_count", ok,
                     (f"{approved_today}/{cap} today" if cap is not None
                      else f"count={approved_today} (no cap set)"))


def check_slippage(quote_amount_out_human: str | float,
                   expected_amount_out_human: str | float,
                   policy: dict) -> RuleCheck:  # noqa: ANN201
    """R5 — live quoted output vs protocol-expected output within tolerance."""
    tol = policy.get("max_slippage_bps", 50)  # basis points
    try:
        got, want = float(quote_amount_out_human), float(expected_amount_out_human)
    except (TypeError, ValueError):
        return RuleCheck("slippage", False,
                         "quote or expected output missing/unparsable")
    if want <= 0:
        return RuleCheck("slippage", False, "expected output is not positive")
    slip_bps = (got - want) / want * 10_000
    ok = abs(slip_bps) <= float(tol)
    return RuleCheck("slippage", ok,
                     f"{slip_bps:+.1f} bps (tol ±{tol} bps)")


def run_hard_rules(decision: dict, policy: dict, *,  # noqa: ANN201, ANN202
                   wallet_value: float, approved_today: int,
                   quote_amount_out_human=None,
                   expected_amount_out_human=None) -> list[RuleCheck]:
    """Fan a parsed proposal across every hard rule; return all checks."""
    action = str(decision.get("action") or "hold").lower()
    checks: list[RuleCheck] = []
    if action == "hold":
        checks.append(RuleCheck(
            "approved_list", True,
            f"hold ({decision.get('asset')}): no new position, no spend"))
        checks.append(RuleCheck("position_size", True, "hold: no new position"))
        checks.append(RuleCheck("trade_share", True, "hold: no trade"))
        checks.append(RuleCheck("daily_count", True,
                                f"{approved_today} approved today (hold: none)"
                                ))
        checks.append(RuleCheck(
            "slippage", True,
            "hold: no quote-dependent liquidity discharge"))
        return checks
    checks.append(check_approved_list(str(decision.get("asset")),
                                      policy.get("approved", ()).split() if
                                      isinstance(policy.get("approved"), str)
                                      else policy.get("approved", ())))
    checks.append(check_position_size(decision.get("asset"),
                                      float(decision.get("size") or 0),
                                      wallet_value, policy))
    checks.append(check_trade_share(float(decision.get("size") or 0),
                                    wallet_value, policy))
    checks.append(check_daily_count(int(approved_today), policy))
    checks.append(check_slippage(quote_amount_out_human,
                                 expected_amount_out_human, policy))
    return checks


def first_failure(checks: list[RuleCheck]) -> RuleCheck | None:  # noqa: ANN201
    """First failing check, or None if every check passed."""
    return next((c for c in checks if not c.ok), None)


def blocked_rules(checks: list[RuleCheck]) -> list[str]:  # noqa: ANN201
    return [c.rule for c in checks if not c.ok]
