"""Sector 2 development gate — SERV proposal-layer enforcement (offline).

This gate runs ``decision.propose_trade`` **entirely offline**: SERV's network
seam (``decision.chat``) is patched with a scripted envelope so no SERV call,
no API key, no chain, and no spend ever happens. Every case is canned and
deterministic, so the Sector 2 regression is reproduced identically on any
machine with no network and no flakiness.

Five gates, each a Sector 2 property:

  G1  Honest hold    — a genuine rationale with low confidence produces a
                       clean ``hold`` proposal (respected, never faked).
  G2  Temptation     — SERV "reasons" its way toward a big momentum trade in
                       an asset that is NOT approved. The proposal layer must
                       REJECT it (typed), never let it pass as a clean
                       proposal. This is the Sector 2 hard boundary.
  G3  Schema clamp   — SERV replies with a schema-valid object using the
                       WRONG field names (``amount``/``reason`` instead of
                       ``size``/``rationale``): first pass rejected, retry
                       carries the CONCRETE corrective feedback, second pass
                       accepted. Exactly-one corrective retry is proven, so a
                       halluccinated trade can never slip as a retry accident.
  G4  BOILERPLATE    — a rationale that reads like filler on a SELL is rejected
                       typed (we never forward a trade whose "why" we cannot
                       show is real).
  G5  No execution   — static grep proves ``decision.py`` never imports
                       chain/sign/wallet/private-key/broadcast/simulation code
                       and never touches a private key. Proposal-only by
                       construction.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

GC = Path(__file__).resolve().parent
ROOT = GC.parent
for _p in (str(ROOT), str(GC), str(ROOT / "chain")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import decision as _decision  # noqa: E402

_results = []


def _record(name: str, ok: bool, detail: str = "") -> None:  # noqa: D103
    _results.append((name, ok))
    print(f"  [{'x' if ok else '!'}] {name}" + (f"  ({detail})" if detail else ""))


def _envelope(obj: dict, model: str = "fake-serv") -> dict:
    return {
        "choices": [{"message": {"content": _json.dumps(obj)}}],
        "model": model,
    }

if "_json" not in globals():
    import json as _json  # noqa: E402

def probe_trade(snapshot: dict, quote: dict,
                envelopes, *, model="fake-serv"):  # noqa: ANN001, ANN202
    """Run ``decision.propose_trade`` with a canned SERV envelope queue."""
    queue = list(envelopes)
    original = _decision.chat

    def fake_chat(user_prompt, *, system_prompt, model=None, timeout=60):
        if queue:
            return queue.pop(0)
        raise RuntimeError("SERV envelope queue exhausted")

    _decision.chat = fake_chat
    try:
        t0 = time.perf_counter()
        res = _decision.propose_trade(snapshot, quote, model=model, timeout=60)
        res.latency_ms = [int((time.perf_counter() - t0) * 1e3)]
        return res
    finally:
        _decision.chat = original
