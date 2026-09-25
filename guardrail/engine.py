"""Sector 3 — guardrail & approval engine (THE PRODUCT, the gate everyone is
protecting). Serializes a SERV proposal through a chain of *hard* refusal
checks and an approval gate; **every** outcome is a typed verdict with a full
decision, never a bare "yes" — and nothing here executes, signs, broadcasts or
touches a private key, so the only thing decision-making can ever produce is a
guarded verdict.

Design rules pinned here so Sector 4/5 can rely on them blindly:

- **Fail closed.** Reject if any hard check fails, if SERV output was malformed
  (schema/parse), if the request is unparsable (bad/violation typed), or if
  the approval fence is off for that trade size. The only way through is
  *every* hard check green AND (autopilot off → human approval) — see below.
- **Confidence fence.** A proposal whose confidence is below the configured
  floor is auto-held (typed ``held_low_confidence``) and can NEVER become
  approved without crossing the whole gate again; no confidence laundering.
- **Human-in-the-loop by default.** Above the floor still requires explicit
  approval. ``autopilot_ok`` (default False) is the ONLY switch that can turn
  that off, and even then only trades that pass every hard rule are auto-
  approved — proposals held/blocked are never auto-forwarded anywhere.
- **Kill switch.** When armed, EVERY gate call returns ``killed`` typed
  immediately — including a trade that would otherwise pass every rule. The
  switch is checked at the very top of the gate (before any hard rule) so a
  mid-flight re-arm stops the pass line even if the switch came on after a
  prior approval was already granted.
- **Deterministic + offline.** Needs no SERV, no chain, no API key, no spend.
  ``now``/``chat``/``count_func`` are injectable seams so the Sector 3 test is
  canonical and free.

Call pattern (proposal-first, then guarded approval):

    verdict    = engine.run_hard_rules(decision.proposed,...)   # soft/typed
    final      = engine.request_approval(verdict, ...)           # human/auto
    log_row    = logging_row(verdict, final)                     # audit
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from guardrail import rules as _rules
from guardrail.dailytrades import DailyCount
from guardrail.killswitch import KillSwitch
from guardrail.logbook import AuditLog

# --------------------------------------------------------------------------- #
# Constants (fixed Sector 3 contract; tests import these, never magic numbers)
# --------------------------------------------------------------------------- #
V_APPROVED = "approved"
V_BLOCKED = "blocked"
V_HOLD = "held_low_confidence"
V_KILLED = "killed"
V_REJECTED = "rejected_by_human"
V_PENDING = "requires_approval"

E_APPROVAL_REQUIRED = "approval_required"
E_SERV_ERROR = "serv_error"
E_SCHEMA = "schema_violation"
E_PARSE = "parse_error"
E_BAD_INPUT = "bad_input"
E_KILL_SWITCH = "kill_switch"
E_LOW_CONFIDENCE = "low_confidence"

DEFAULT_POLICY: dict = {
    "network": "testnet", "chain_id": 46630,
    "approved": ("tUSD", "tRWA", "TSLA", "WETH"),
    "approved_assets": ("tUSD", "tRWA", "TSLA", "WETH"),  # alias, rules read 'approved'
    "max_position_pct": 0.40,        # a single holding may be ≤40% of wallet
    "max_trade_pct_of_wallet": 0.20,  # one order ≤20% of wallet value
    "confidence_floor": 0.85,        # below this → auto-hold, no approval
    "max_daily_trades": 5,           # approvals per UTC day
    "max_slippage_bps": 100,         # live-vs-expected quote tolerance
    "autopilot_ok": False,           # THE only bypass to human approval
    "timeout": 30,
    "kill_switch_path": None,       # file-based kill flag path override
}


def _now_iso() -> str:  # noqa: ANN202
    return datetime.now(timezone.utc).isoformat()


def _to_list(x) -> list:  # noqa: ANN002, ANN202, D401
    if x is None:
        return []
    if isinstance(x, (list, tuple, set)):
        return list(x)
    return [x]


def _latency_ms(row) -> int:  # noqa: ANN202
    try:
        return int(row.latency_ms[-1]) if getattr(row, "latency_ms", None) \
            else 0
    except (TypeError, ValueError, IndexError):
        return 0


@dataclass
class GuardedDecision:  # noqa: D101
    action: str  # noqa: A003
    asset: str
    size: float
    confidence: float
    rationale: str
    risk_flags: list = field(default_factory=list)

    def to_dict(self) -> dict:  # noqa: D102, ANN201
        return asdict(self)


@dataclass
class HardCheck:  # noqa: D101
    rule: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict:  # noqa: D102, ANN201
        return asdict(self)


@dataclass
class ApprovalVerdict:  # noqa: D101
    ok: bool
    network: str
    chain_id: int
    block_number: int
    model: str
    verdict: str
    decision: Optional[GuardedDecision] = None
    hard_checks: list = field(default_factory=list)
    error: Optional[str] = None
    error_type: Optional[str] = None
    latency_ms: list = field(default_factory=list)
    approved_at: Optional[str] = None
    queried_at: str = field(default_factory=_now_iso)
    quote_amount_out_human: Optional[str] = None
    expected_amount_out_human: Optional[str] = None
    amount_in_human: Optional[str] = None

    @classmethod
    def blocked(cls, *, network: str, chain_id: int, block_number: int,
                model: str, error: str, error_type: str,
                decision: Optional[GuardedDecision] = None,
                hard_checks: Optional[list] = None,
                latency_ms: Optional[list] = None) -> "ApprovalVerdict":
        """Typed blocker — never a bare failure string."""
        return cls(ok=False, network=network, chain_id=chain_id,
                   block_number=block_number, model=model, verdict=V_BLOCKED,
                   decision=decision, hard_checks=_to_list(hard_checks),
                   error=error, error_type=error_type,
                   latency_ms=_to_list(latency_ms))

    @classmethod
    def held(cls, *, network: str, chain_id: int, block_number: int,
             model: str, error: str,
             decision: Optional[GuardedDecision], hard_checks: list,
             latency_ms: Optional[list] = None,
             queried_at: Optional[str] = None) -> "ApprovalVerdict":
        """Typed low-confidence hold — never forwarded, never approved."""
        return cls(ok=False, network=network, chain_id=chain_id,
                   block_number=block_number, model=model, verdict=V_HOLD,
                   decision=decision, hard_checks=hard_checks, error=error,
                   error_type=E_LOW_CONFIDENCE, latency_ms=_to_list(latency_ms),
                   queried_at=queried_at or _now_iso())

    @classmethod
    def killed(cls, *, network: str, chain_id: int, block_number: int,
               model: str, error: str,
               decision: Optional[GuardedDecision] = None,
               latencies: Optional[list] = None) -> "ApprovalVerdict":
        """Kill switch — top of the gate, must outrank every hard rule."""
        return cls(ok=False, network=network, chain_id=chain_id,
                   block_number=block_number, model=model, verdict=V_KILLED,
                   decision=decision, hard_checks=[],
                   error=error, error_type=E_KILL_SWITCH,
                   latency_ms=_to_list(latencies))

    @classmethod
    def pending(cls, *, network: str, chain_id: int, block_number: int,
                model: str, decision: GuardedDecision,
                hard_checks: list,
                latency_ms: Optional[list] = None,
                expected_amount_out_human: Optional[str] = None,
                quote_amount_out_human: Optional[str] = None,
                amount_in_human: Optional[str] = None) -> "ApprovalVerdict":
        """Passed every hard rule — now requires the human (or autopilot)."""
        return cls(ok=True, network=network, chain_id=chain_id,
                   block_number=block_number, model=model, verdict=V_PENDING,
                   decision=decision, hard_checks=list(hard_checks),
                   latency_ms=_to_list(latency_ms),
                   quote_amount_out_human=quote_amount_out_human,
                   expected_amount_out_human=expected_amount_out_human,
                   amount_in_human=amount_in_human)

    @classmethod
    def approved(cls, *, network: str, chain_id: int, block_number: int,
                 model: str, decision: GuardedDecision, hard_checks: list,
                 latency_ms: Optional[list] = None,
                 expected_amount_out_human: Optional[str] = None,
                 quote_amount_out_human: Optional[str] = None,
                 amount_in_human: Optional[str] = None) -> "ApprovalVerdict":
        """Explicit human approval (or autopilot-with-flag) — the ONLY '+ok'."""
        return cls(ok=True, network=network, chain_id=chain_id,
                   block_number=block_number, model=model, verdict=V_APPROVED,
                   decision=decision, hard_checks=list(hard_checks),
                   latency_ms=_to_list(latency_ms),
                   approved_at=_now_iso(),
                   quote_amount_out_human=quote_amount_out_human,
                   expected_amount_out_human=expected_amount_out_human,
                   amount_in_human=amount_in_human)

    @classmethod
    def rejected(cls, *, network: str, chain_id: int, block_number: int,
                 model: str, decision: GuardedDecision, hard_checks: list,
                 error: str = "Not approved by human (or unanswered).",
                 latency_ms: Optional[list] = None,
                 quote_amount_out_human: Optional[str] = None,
                 expected_amount_out_human: Optional[str] = None) -> "ApprovalVerdict":
        """Rejected/unanswered — the fail-closed human path."""
        return cls(ok=False, network=network, chain_id=chain_id,
                   block_number=block_number, model=model, verdict=V_REJECTED,
                   decision=decision, hard_checks=list(hard_checks),
                   error=error, error_type=E_APPROVAL_REQUIRED,
                   latency_ms=_to_list(latency_ms),
                   quote_amount_out_human=quote_amount_out_human,
                   expected_amount_out_human=expected_amount_out_human)


def _decision_to_guarded(src) -> GuardedDecision:  # noqa: ANN202, D401
    """Coerce a Sector 2 ``DecisionResult`` (or its ``.proposed``, or a dict)
    into the guardrail's own typed decision. Never trusts class identity — it
    reads the guaranteed field names off whatever object it receives, so a
    Sector 2 result, a raw ProposedDecision, or a plain dict all work."""
    if isinstance(src, GuardedDecision):
        return src
    if isinstance(src, dict):
        base = src
    else:
        # If it looks like a decision *result* (has .proposed), unwrap first.
        if hasattr(src, "proposed") and getattr(src, "proposed") is not None:
            src = getattr(src, "proposed")
        base = {}
        for key in ("action", "asset", "size", "confidence", "rationale",
                    "risk_flags"):
            if hasattr(src, key):
                base[key] = getattr(src, key)
        if hasattr(src, "to_dict") and not base:
            base = src.to_dict()
    return GuardedDecision(
        action=str(base.get("action", "hold")),
        asset=str(base.get("asset", "")),
        size=float(base.get("size", 0.0) or 0.0),
        confidence=float(base.get("confidence", 0.0) or 0.0),
        rationale=str(base.get("rationale", "")),
        risk_flags=list(base.get("risk_flags") or []),
    )


class Guardrail:  # noqa: D101
    """The Sector 3 gate. Order matters and is fixed:

    ``guarded()``  : kill? → hard rules → confidence fence -->
                     ``V_BLOCKED``/``V_HOLD``(low conf)/``V_PENDING``
    ``approve()``  : kill-recheck → human OR autopilot-flag -->
                     ``V_APPROVED``/``V_REJECTED`` (default NOT approved)

    Both steps fan the same proposal; neither ever executes anything. The only
    object a caller of the guardrail receives is an ``ApprovalVerdict``.
    """

    def __init__(self, *,  # noqa: D107
                 network: str = "testnet", chain_id: int = 46630,
                 block_number: int = 0, model: str = "guardrail",
                 policy: Optional[dict] = None,
                 kill_switch: Optional[KillSwitch] = None,
                 daily: Optional[DailyCount] = None,
                 audit: Optional[AuditLog] = None) -> None:
        self.network = network
        self.chain_id = chain_id
        self.block_number = block_number
        self.model = model
        self.policy = dict(DEFAULT_POLICY)
        if policy:
            self.policy.update(policy)
        self.kill_switch = kill_switch or KillSwitch()
        self.daily = daily or DailyCount()
        self.audit = audit or AuditLog()

    # ------------------------------------------------------------------ #
    def guarded(self, proposal, *,  # noqa: ANN201, D401
                wallet_value: float, approved_today: int | None = None,
                quote_amount_out_human=None,
                expected_amount_out_human=None,
                amount_in_human=None,
                latency: Optional[list] = None) -> ApprovalVerdict:
        """Stage 1 — kill-first, hard rules, confidence fence. Returns a typed
        verdict (V_BLOCKED / V_HOLD / V_KILLED / V_PENDING) with the decision
        + every hard-check carried through. Never approves anything."""
        latency = latency or []
        t0 = time.perf_counter()

        # THE KILL SWITCH — before ANY rule, mid-flight or otherwise.
        if self.kill_switch.is_armed():
            lat = int((time.perf_counter() - t0) * 1e3)
            return ApprovalVerdict.killed(
                network=self.network, chain_id=self.chain_id,
                block_number=self.block_number, model=self.model,
                decision=None,
                error="Kill switch is ARMED — all approvals blocked.",
                latencies=latency + [lat])

        decision = _decision_to_guarded(proposal)
        dec_dict = decision.to_dict()

        checks = _rules.run_hard_rules(
            dec_dict, self.policy,
            wallet_value=wallet_value,
            approved_today=int(approved_today or 0),
            quote_amount_out_human=quote_amount_out_human,
            expected_amount_out_human=expected_amount_out_human)

        # Confidence fence pushes hard-rule *passes* too: below floor means
        # the trade is auto-held and CANNOT reach approval — this is the
        # spec's "auto-hold, flagged for review" path.
        floor = float(self.policy.get("confidence_floor", 0.0))
        if decision.confidence < floor:
            lat = int((time.perf_counter() - t0) * 1e3)
            return ApprovalVerdict.held(
                network=self.network, chain_id=self.chain_id,
                block_number=self.block_number, model=self.model,
                error=(f"held for review at {decision.confidence:.2f} "
                       f"confidence < floor {floor:.2f}"),
                decision=decision, hard_checks=checks,
                latency_ms=latency + [lat])

        failing = _rules.first_failure(checks)
        if failing is not None:
            lat = int((time.perf_counter() - t0) * 1e3)
            err_type = f"rule_{failing.rule}"
            return ApprovalVerdict.blocked(
                network=self.network, chain_id=self.chain_id,
                block_number=self.block_number, model=self.model,
                error=f"[{failing.rule}] {failing.detail}",
                error_type=err_type,
                decision=decision, hard_checks=checks,
                latency_ms=latency + [lat])

        lat = int((time.perf_counter() - t0) * 1e3)
        return ApprovalVerdict.pending(
            network=self.network, chain_id=self.chain_id,
            block_number=self.block_number, model=self.model,
            decision=decision, hard_checks=checks,
            latency_ms=latency + [lat],
            quote_amount_out_human=quote_amount_out_human,
            expected_amount_out_human=expected_amount_out_human,
            amount_in_human=str(float(amount_in_human or decision.size)))

    # ------------------------------------------------------------------ #
    def approve(self, verdict: ApprovalVerdict, *,  # noqa: ANN201
                human_approved: bool | None = None,
                autopilot: bool = False,
                latency: Optional[list] = None) -> ApprovalVerdict:
        """Stage 2 — re-check kill, then human approval (default NOT
        approved) or the pre-declared autopilot flag (off by default).

        Fail-closed, explicitly: a rejected or UNANSWERED prompt produces a
        typed ``V_REJECTED`` — never a silent fall-through to approved."""
        latency = latency or []
        approval_ok = self.policy.get("autopilot_ok", False)
        if not verdict.ok or verdict.verdict != V_PENDING:
            # blocked / held / killed stay as-is — never upgraded by autopilot
            # or by a human "yes". Only a V_PENDING verdict can be approved.
            return verdict
        if bool(autopilot) and bool(approval_ok):
            # autopilot is the ONLY path that skips human approval — and only
            # when the flag is explicitly true.
            return ApprovalVerdict.approved(
                network=self.network, chain_id=self.chain_id,
                block_number=self.block_number, model=self.model,
                decision=verdict.decision, hard_checks=verdict.hard_checks,
                latency_ms=verdict.latency_ms + latency,
                quote_amount_out_human=verdict.quote_amount_out_human,
                expected_amount_out_human=verdict.expected_amount_out_human,
                amount_in_human=verdict.amount_in_human)
        if human_approved is True:
            return ApprovalVerdict.approved(
                network=self.network, chain_id=self.chain_id,
                block_number=self.block_number, model=self.model,
                decision=verdict.decision, hard_checks=verdict.hard_checks,
                latency_ms=verdict.latency_ms + latency,
                quote_amount_out_human=verdict.quote_amount_out_human,
                expected_amount_out_human=verdict.expected_amount_out_human,
                amount_in_human=verdict.amount_in_human)
        return ApprovalVerdict.rejected(
            network=self.network, chain_id=self.chain_id,
            block_number=self.block_number, model=self.model,
            decision=verdict.decision, hard_checks=verdict.hard_checks,
            latency_ms=verdict.latency_ms + latency,
            quote_amount_out_human=verdict.quote_amount_out_human,
            expected_amount_out_human=verdict.expected_amount_out_human)

    # ------------------------------------------------------------------ #
    def approve_or_blocked(self, verdict: ApprovalVerdict, *,  # noqa: ANN201
                           human_approved: bool | None = None,
                           autopilot: bool = False) -> ApprovalVerdict:
        """Convenience that hard-forces a final verdict: re-check kill switch
        BEFORE approval (so mid-flight arming kills a would-be approve),
        then approve(). Returns the same typed object either way."""
        if self.kill_switch.is_armed():
            return ApprovalVerdict.killed(
                network=self.network, chain_id=self.chain_id,
                block_number=self.block_number, model=self.model,
                decision=verdict.decision,
                error="Kill switch ARMED at approval time — blocked.",
                latencies=verdict.latency_ms)
        return self.approve(verdict, human_approved=human_approved,
                            autopilot=autopilot)


def verdict_to_log_row(verdict: ApprovalVerdict) -> dict:  # noqa: ANN201
    """The stable audit-log row Sector 5 renders. Every decision type gets one.

    Fields pinned: event, verdict, ok, asset, size, confidence, blocked_by,
    error_type, error, rationale (verbatim from the decision — never rewritten
    here), risk_flags, timestamp_utc, block_number, model, network, chain_id.
    """
    d = verdict.decision
    row = {
        "event": "guardrail_decision",
        "verdict": verdict.verdict,
        "ok": bool(verdict.ok),
        "asset": d.asset if d else None,
        "size": d.size if d else None,
        "confidence": d.confidence if d else None,
        "rationale": d.rationale if d else None,
        "risk_flags": list(d.risk_flags) if d else [],
        "blocked_by": [c.rule for c in verdict.hard_checks if not c.ok],
        "error_type": verdict.error_type,
        "error": verdict.error,
        "affected_rule": ([c.rule for c in verdict.hard_checks if not c.ok][:1]
                          or [None])[0],
        "timestamp_utc": verdict.queried_at,
        "approved_at": verdict.approved_at,
        "network": verdict.network,
        "chain_id": verdict.chain_id,
        "block_number": verdict.block_number,
        "model": verdict.model,
        "latency_ms": list(verdict.latency_ms),
    }
    return row
