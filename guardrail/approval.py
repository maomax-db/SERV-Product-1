"""Sector 3 — CLI approval interface (the human-in-the-loop step).

Presents a guarded ``ApprovalVerdict``/decision to the operator, prints every
hard-check pass/fail, and asks y/n. **Default to NOT approved** — a rejected,
blank, or unanswered input is a typed rejection and can never fall through to
approval. The prompt function is injectable so the test never blocks on stdin.
"""

from __future__ import annotations

from typing import Callable, Optional

from guardrail.engine import ApprovalVerdict, GuardedDecision, V_PENDING


def format_decision(verdict: ApprovalVerdict) -> str:  # noqa: ANN201
    """Human-readable proposal card: asset, size, confidence, checks, plaus."""
    d: Optional[GuardedDecision] = verdict.decision
    lines = [
        "------------------------------------------------------------",
        "PROPOSED PROPOSAL (guarded verdict — approval required)",
        "------------------------------------------------------------",
        f"  action     : {d.action if d else '?'}",
        f"  asset      : {d.asset if d else '?'}",
        f"  size       : {d.size if d else '?'}",
        f"  confidence : {d.confidence if d else '?'}",
        f"  rationale  : {(d.rationale if d else '?')[:200]}",
        f"  risk_flags : {list(d.risk_flags) if d else []}",
        "  --- hard checks ---",
    ]
    for c in verdict.hard_checks:
        mark = "PASS" if c.ok else "FAIL"
        lines.append(f"    [{mark}] {c.rule}: {c.detail}")
    lines += [
        f"  verdict    : {verdict.verdict}",
        "------------------------------------------------------------",
    ]
    return "\n".join(lines)


def prompt_approval(verdict: ApprovalVerdict,
                    prompt_fn: Optional[Callable[[str], Optional[bool]]] = None,
                    *, timeout_seconds: float = 30.0) -> Optional[bool]:
    """Ask the operator y/n. Injectable ``prompt_fn`` for tests.

    Returns True on explicit 'y', else False on anything else. Passing None
    instead of asking (a test convenance) means "unanswered" → False.
    """
    print(format_decision(verdict))
    if verdict.verdict != V_PENDING:
        print(f"  NOT presented for approval (verdict={verdict.verdict}).")
        return False
    if prompt_fn is None:
        answer = input(f"  Approve this trade? (y/N, {timeout_seconds:.0f}s) > ").strip().lower()
        return answer == "y"
    answer = prompt_fn(f"Approve {verdict.decision.asset}? (y/N)")
    return True if answer is True else False