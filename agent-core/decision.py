"""Sector 2 — SERV reasoning: the trade PROPOSAL layer.

**What this is.** The bridge between the read-only data layer (Sector 0/1:
wallet snapshot + live market quote, both already fetched) and the guardrail
layer (Sector 3). We hand SERV a *normalized* wallet snapshot, a *live* market
quote, and a small strategy ruleset, and SERV reasons over them to produce
*ONE structured decision* matching this module's fixed schema.

**What this is NOT — the Sector 2 security boundary.** This module:

* receives the snapshot and quote **as pre-fetched data**, never touching the
  chain itself (no Web3, no RPC, no network call beyond the SERV API);
* never signs, never simulates swaps, never broadcasts, never references a
  private key or a signer module;
* is purely a *proposal generator* — it produces JSON that says
  "proposed: buy/sell/hold X". Nothing it returns can move funds.

The Sector 3 guardrail is the enforcement layer; nothing in Sector 2 is
enforced, only proposed. If SERV fails to produce schema-valid JSON we say so
loudly (typed error) — we never silently turn a failed SERV call into
"proposed: hold" as if it were a real reasoning result.

**Decision schema (the fixed contract SERV must output):**

    {
      "action":      "buy" | "sell" | "hold",
      "asset":       "string — token symbol or contract address",
      "size":        "number — token units in human terms",
      "confidence":  "number 0.0–1.0",
      "rationale":   "plain-English string — genuinely explains the why",
      "risk_flags":  ["string", ...]        # optional, defaults to []
    }
"""

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config import OPENSERV_MODEL
from serv import OpenServError, chat

# --------------------------------------------------------------------------- #
# Sector 2 decision schema — the ONE shape SERV is allowed to produce
# --------------------------------------------------------------------------- #
DECISION_KEYS = {"action", "asset", "size", "confidence", "rationale", "risk_flags"}
ALLOWED_ACTIONS = ("buy", "sell", "hold")

# Words that are NEVER acceptable in any rationale for this product: anything
# implying leverage, margin, perps, shorts, or "fast money" bait. If SERV
# "reasons" its way toward one of these, the decision is rejected outright.
BANNED_TERMS = (
    "leverage", "leveraged", "margin", "perpetual", "perp", "perpetuals",
    "futures", "short", "shorting", "10x", "20x", "100x",
)

# Risk flags we recognise and surface. SERV may append others, but this is the
# common vocabulary the guardrail (Sector 3) knows how to react to.
KNOWN_RISK_FLAGS = {
    "momentum_chase", "rebalance_overshoot", "concentration", "stale_price",
    "pool_uncertainty", "low_confidence", "size_above_limit", "banned_asset",
    "disallowed_asset", "hold_pending_data",
}

_DEFAULT_STRATEGY = {
    # simple target-weighted rebalance strategy; small drift allowed before act.
    "targets": {"TSLA": 0.40, "tUSD": 0.30, "tRWA": 0.20, "WETH": 0.10},
    "rebalance_band": 0.10,        # allowed deviation (fraction) before rebal
    "momentum_guard": 0.25,        # if a token moved >25% recently, don't chase
    "max_proposal_size": 5.0,      # human units; never propose above this
    "approved": ["TSLA", "tUSD", "tRWA", "WETH"],
}

ERR_SERV = "serv_error"
ERR_PARSE = "decision_parse_error"
ERR_SCHEMA = "decision_schema_violation"
ERR_CONSTRAINT = "constraint_violation"
ERR_BAD_INPUT = "bad_input"


@dataclass
class ProposedDecision:
    action: str
    asset: str
    size: float
    confidence: float
    rationale: str
    risk_flags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DecisionResult:
    ok: bool
    network: str
    chain_id: int
    block_number: int
    model: str
    proposed: Optional[ProposedDecision] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    latency_ms: list = field(default_factory=list)
    validation: list = field(default_factory=list)
    queried_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def error_result(cls, network: str, chain_id: int, block_number: int,
                     model: str, error: str, error_type: str) -> "DecisionResult":
        return cls(ok=False, network=network, chain_id=chain_id,
                   block_number=block_number, model=model,
                   error=error, error_type=error_type)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["proposed"] = self.proposed.to_dict() if self.proposed else None
        return d


# --------------------------------------------------------------------------- #
# Schema validation (pure, no I/O)
# --------------------------------------------------------------------------- #
def validate_decision_schema(raw: dict) -> tuple:
    """Validate a raw SERV decision object against the fixed Sector 2 schema.

    Returns ``(ok, decision, errors)``. On success ``decision`` is a
    :class:`ProposedDecision`; on failure it is ``None`` and ``errors`` lists
    the schema violations as strings (never a terse one-liner).
    """
    errors = []
    if not isinstance(raw, dict):
        return False, None, [f"expected a JSON object, got {type(raw).__name__}"]

    unknown = [k for k in raw if k not in DECISION_KEYS]
    if unknown:
        errors.append(f"unknown field(s): {', '.join(unknown)}")

    action = raw.get("action")
    if action not in ALLOWED_ACTIONS:
        errors.append(f"action must be one of {ALLOWED_ACTIONS}, got {action!r}")

    asset = raw.get("asset")
    if not isinstance(asset, str) or not asset.strip():
        errors.append("asset must be a non-empty string")

    size = raw.get("size")
    if isinstance(size, bool) or not isinstance(size, (int, float)):
        errors.append("size must be a number")
    elif size < 0:
        errors.append("size cannot be negative")

    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        errors.append("confidence must be a number")
    elif not 0.0 <= confidence <= 1.0:
        errors.append(f"confidence must be in [0.0, 1.0], got {confidence}")

    rationale = raw.get("rationale")
    if not isinstance(rationale, str) or len(rationale.strip()) < 20:
        errors.append("rationale must be a string of >= 20 chars")
    rationale = (rationale or "").strip()

    risk_flags = raw.get("risk_flags")
    if risk_flags is None:
        risk_flags = []
    if not isinstance(risk_flags, list) or not all(
        isinstance(f, str) for f in risk_flags
    ):
        errors.append("risk_flags must be a list of strings")

    if errors:
        return False, None, errors

    decision = ProposedDecision(
        action=action, asset=(asset or "").strip(), size=float(size),
        confidence=float(confidence), rationale=rationale,
        risk_flags=[str(f) for f in risk_flags],
    )
    return True, decision, []


def _rationale_is_boilerplate(rationale: str) -> Optional[str]:
    """Heuristic: flag a rationale that reads like filler, not genuine thought."""
    low = rationale.lower()
    tokens = re.findall(r"[a-z]+", low)
    generic = {
        "and", "the", "this", "that", "based", "market", "data", "current",
        "asset", "size", "portfolio", "since", "should", "would", "value",
        "because", "therefore", "there", "given", "please", "about", "not",
    }
    meaningful = [t for t in tokens if t not in generic and len(t) > 3]
    if len(meaningful) < 8:
        return "rationale reads like boilerplate (few genuine, specific terms)"
    for word in BANNED_TERMS:
        if re.search(rf"\b{re.escape(word)}\b", low):
            return f"rationale mentions a banned trading concept: {word!r}"
    return None


# --------------------------------------------------------------------------- #
# Robust JSON extraction from SERV's reply (survives markdown fences)
# --------------------------------------------------------------------------- #
def _extract_json(text: str):
    """Best-effort JSON object extraction from SERV's raw reply."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else s
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _validate_and_reason(text: str):
    """Parse + schema-validate SERV's reply.

    Returns a tuple ``(decision, validation_checks)`` on success, or ``None``
    when the reply is not a schema-valid decision. Boilerplate rationales on an
    actual proposed trade are treated as a real failure (we will not forward a
    trade we cannot explain genuinely).
    """
    parsed = _extract_json(text)
    if parsed is None:
        return None

    ok, decision, _errors = validate_decision_schema(parsed)
    if not ok:
        return None

    flagged = _rationale_is_boilerplate(decision.rationale)
    checks = [{
        "check": "rationale_quality",
        "ok": flagged is None,
        "detail": "genuine rationale" if flagged is None else f"SPOT-CHECK: {flagged}",
    }]
    if flagged and decision.action in ("buy", "sell"):
        # We refuse to propose a trade whose 'why' we cannot show is real.
        return None
    return decision, checks


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #
def _fmt_amount(b: dict) -> str:
    symbol = (b.get("symbol") or "?").upper()
    decimals = b.get("decimals") or 0
    raw = b.get("raw", "?")
    human_raw = b.get("human")
    try:
        if human_raw is None:
            human_raw = int(raw or 0) / (10 ** int(decimals))
        human = float(human_raw)
    except Exception:  # noqa: BLE001
        human = 0.0
    return f"  - {symbol:>6}  {human:.6f}  (raw {raw})"


def _fmt_quote(q: dict) -> str:
    if not isinstance(q, dict):
        return f"  - (malformed quote: {q!r})"
    if q.get("ok"):
        return (
            f"  - {q.get('token_in_symbol')} -> {q.get('token_out_symbol')}: "
            f"{q.get('amount_in_human')} in -> {q.get('amount_out_human')} out "
            f"(pool 0x{(q.get('pool_id') or '')[:12]}..., sqrt "
            f"{q.get('sqrt_price_x96')}, liq {q.get('liquidity')})"
        )
    return (
        f"  - {q.get('token_in_symbol')} -> {q.get('token_out_symbol')}: "
        f"unavailable ({q.get('error_type')}: {q.get('error')})"
    )


def _system_prompt() -> str:
    return (
        "You are SERV, the reasoning engine of a read-only trade PROPOSAL layer on "
        "Robinhood Chain (testnet). Your ONLY job: look at the wallet snapshot and "
        "the live market quote you are given, apply the trading strategy ruleset, "
        "and propose ONE decision for the next step. You never execute anything. "
        "You never simulate anything. Nothing you say can move funds.\n\n"
        "HARD RULES:\n"
        "1. Only propose assets on the APPROVED list. If an asset isn't approved, "
        "you cannot propose any trade in it — say so and set action to hold.\n"
        "2. SPOT ONLY. Never propose leverage, margin, perpetuals, futures, or "
        "shorts. Never mention them as an option.\n"
        "3. Propose at most ONE action. If nothing is worth doing, propose hold.\n"
        "4. Be honest about uncertainty. If data is missing or confidence is low, "
        "set confidence low and action to hold.\n"
        "5. You are a PROPOSAL engine. You cannot and must not sign, broadcast, "
        "or execute anything.\n\n"
        "OUTPUT FORMAT: reply with a single JSON object, no markdown fences, no "
        "prose before or after. It must have EXACTLY these keys:\n"
        '  {"action":"buy|sell|hold","asset":"string","size":number,'
        '"confidence":number,"rationale":"plain English",'
        '"risk_flags":["..."]}\n'
        "If you must decline, still return action=\"hold\"."
    )


def _fmt_quotes(market_quote) -> str:
    if isinstance(market_quote, dict):
        market_quote = [market_quote]
    lines = [_fmt_quote(q) for q in (market_quote or [])]
    return "\n".join(lines) or "  - (no quotes)"


def _user_prompt(snapshot: dict, market_quote, strategy: dict) -> str:
    cfg = dict(_DEFAULT_STRATEGY)
    cfg.update({k: v for k, v in strategy.items() if k in _DEFAULT_STRATEGY})

    bal_lines = "\n".join(
        _fmt_amount(b) for b in (snapshot.get("balances") or []) if isinstance(b, dict)
    )
    target_text = ", ".join(
        f"{k}={v:.0%}" for k, v in sorted(cfg["targets"].items(), key=lambda kv: -kv[1])
    )
    approved_text = ", ".join(cfg["approved"])

    return (
        "WALLET SNAPSHOT (Sector 1, read-only):\n"
        f"{bal_lines}\n\n"
        "LIVE MARKET QUOTE (Sector 1, read-only):\n"
        f"{_fmt_quotes(market_quote)}\n\n"
        "STRATEGY (Sector 2 ruleset):\n"
        f"  - targets: {target_text}\n"
        f"  - rebalance_band: {cfg['rebalance_band']:.0%} drift allowed before acting\n"
        f"  - momentum_guard: don't chase moves >{cfg['momentum_guard']:.0%}\n"
        f"  - max_proposal_size: {cfg['max_proposal_size']} human units\n"
        f"  - approved assets: {approved_text}\n\n"
        "Decide ONE action for the next step. Reply with the single JSON object "
        "described in the system prompt. No extra text."
    )


# --------------------------------------------------------------------------- #
# Public entry point — the proposal layer
# --------------------------------------------------------------------------- #
def propose_trade(snapshot: dict, market_quote: dict,
                  strategy: Optional[dict] = None,
                  *, model: Optional[str] = None, timeout: int = 120) -> DecisionResult:
    """Ask SERV for ONE proposal from a wallet snapshot + live market quote.

    Args:
        snapshot: a Sector 1 wallet snapshot (from ``Snapshot.to_dict()``).
        market_quote: a Sector 1 market quote (from ``Quote.to_dict()``), or a
            list of such quotes.
        strategy: optional override of the default rule-set (targets, band,
            momentum guard, approved list, max size).
        model: SERV model override (defaults to ``OPENSERV_MODEL``).
        timeout: per-attempt API timeout in seconds.

    Returns a :class:`DecisionResult`. On any failure the result carries
    ``ok=False`` with a typed ``error_type`` and never raises.
    """
    chain_id = int(snapshot.get("chain_id") or market_quote.get("chain_id") or 0)
    block_number = int(snapshot.get("block_number") or market_quote.get("block_number") or 0)
    network = snapshot.get("network") or "testnet"
    model_name = model or OPENSERV_MODEL

    def fail(error, error_type):  # noqa: ANN202
        return DecisionResult.error_result(
            network=network, chain_id=chain_id, block_number=block_number,
            model=model_name, error=error, error_type=error_type)

    if not isinstance(snapshot, dict) or not isinstance(market_quote, dict):
        return fail("snapshot and market_quote must be dicts", ERR_BAD_INPUT)

    cfg = dict(_DEFAULT_STRATEGY)
    if isinstance(strategy, dict):
        cfg.update({k: v for k, v in strategy.items() if k in _DEFAULT_STRATEGY})

    user_prompt = _user_prompt(snapshot, market_quote, cfg)
    system_prompt = _system_prompt()

    latencies = []
    for attempt in (1, 2):
        t0 = time.perf_counter()
        try:
            response = chat(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                model=model_name,
                timeout=timeout,
            )
        except OpenServError as exc:
            latencies.append(int((time.perf_counter() - t0) * 1000))
            return fail(f"SERV API unreachable: {exc}", ERR_SERV)

        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            latencies.append(int((time.perf_counter() - t0) * 1000))
            return fail(f"malformed SERV envelope: {exc}", ERR_PARSE)
        latencies.append(int((time.perf_counter() - t0) * 1000))

        validated = _validate_and_reason(content)
        if validated is None:
            if attempt == 1:
                # Give SERV the CONCRETE corrective signal it actually needs:
                # name the schema violations precisely (SERV replied in valid
                # JSON but with wrong/renamed fields — that is the common real
                # failure, and a vague "not valid JSON" doesn't say that).
                parsed = _extract_json(content)
                _, _, schema_errors = validate_decision_schema(
                    parsed) if isinstance(parsed, dict) else (False, None, [
                        "reply did not contain a JSON object at all"])
                user_prompt += (
                    "\n\n[SYSTEM] Your previous reply was not schema-valid. "
                    "Concrete problem(s) to fix:\n"
                    + "\n".join(f"  - {e}" for e in schema_errors)
                    + "\nReply with ONLY the single corrected JSON object "
                      "matching the required schema — no fences, no prose."
                )
                continue
            return fail(
                "SERV returned nothing schema-valid after retry", ERR_PARSE)

        decision, checks = validated

        # ---- hard constraint enforcement (beyond SERV's own judgment) -------
        approved = [str(a).lower() for a in cfg["approved"]]
        if decision.action in ("buy", "sell"):
            asset_l = decision.asset.lower()
            if not any(
                asset_l == a or asset_l.endswith(a) or a.endswith(asset_l)
                for a in approved
            ):
                return fail(
                    f"SERV proposed {decision.asset!r}, which is NOT on the "
                    f"approved list ({', '.join(cfg['approved'])}). Blocked at "
                    f"the proposal layer (Sector 2) before it can reach Sector 3.",
                    ERR_CONSTRAINT,
                )
            if decision.size > float(cfg["max_proposal_size"]):
                return fail(
                    f"proposed size {decision.size} exceeds max_proposal_size "
                    f"({cfg['max_proposal_size']}). Blocked at the proposal layer.",
                    ERR_CONSTRAINT,
                )

        return DecisionResult(
            ok=True, network=network, chain_id=chain_id, block_number=block_number,
            model=model_name, proposed=decision, latency_ms=latencies,
            validation=checks,
        )

    return fail("unreachable", ERR_SERV)  # pragma: no cover
