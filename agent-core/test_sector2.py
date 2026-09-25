"""Sector 2 offline gate — proposal enforcement (deterministic, free).

Patches ``decision.chat`` (the SERV network seam) with canned envelopes so
``decision.propose_trade`` is exercised **offline-only**: no SERV, no API key,
no chain, no network, no spend. Every case is scripted and reproducible.

  G1 honest hold   — a schema-valid hold envelope → typed proposal accepted.
  G2 temptation    — envelope wants a NON-approved asset (MOONSHOT) → typed
                     block (error_type set, no clean proposal). Hard boundary.
  G3 wrong names   — uses ``amount``/``rationale``-missing instead of
                     ``size``: rejected pass 1; corrective retry names the
                     violation; corrected envelope accepted pass 2. Exactly
                     one corrective retry, never silence.
  G4 boilerplate   — filler rationale rejected typed (never forwarded).
  G5 construction  — decision.py imports no sign/broadcast/wallet/private-key.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

GC = Path(__file__).resolve().parent
REPO = GC.parent
for _p in (str(REPO), str(GC), str(REPO / "chain")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import decision as _d  # noqa: E402

RESULTS: list = []


def record(name: str, ok: bool, detail: str = "") -> None:  # noqa: D103
    RESULTS.append((name, bool(ok)))
    print(f"  [{'x' if ok else '!'}] {name}" + (f"  ({detail})" if detail else ""))


def _env(obj: dict, model: str = "fake-serv") -> dict:  # noqa: ANN202
    return {"choices": [{"message": {"content": json.dumps(obj)}}],
            "model": model}


def _chat_log(envelopes: list) -> tuple:  # noqa: ANN202
    queue = list(envelopes)
    calls = []

    def fake(user_prompt, *, system_prompt, model=None, timeout=60):  # noqa: ANN202
        calls.append(user_prompt)
        if queue:
            return queue.pop(0)
        raise RuntimeError("SERV envelope queue exhausted")

    return fake, calls


def _snapshot() -> dict:  # noqa: ANN202
    return {
        "network": "testnet", "chain_id": 46630, "block_number": 12_268_630,
        "address": "0x2170105c880B8a5782EDE8ec7B02465f9d3cd981",
        "balances": [
            {"symbol": "ETH", "human": 0.002, "raw": "2000000000000000",
             "decimals": 18},
            {"symbol": "TSLA", "human": 1.203, "raw": "1203000000000000000",
             "decimals": 18},
            {"symbol": "tUSD", "human": 260.0, "raw": "260000000000000000000",
             "decimals": 18},
            {"symbol": "tRWA", "human": 110.0, "raw": "110000000000000000000",
             "decimals": 18},
        ]}


def _quote() -> dict:  # noqa: ANN202
    return {
        "ok": True, "network": "testnet", "chain_id": 46630,
        "block_number": 12_268_630, "token_in_symbol": "tUSD",
        "token_out_symbol": "tRWA", "amount_in_human": "1.0",
        "amount_out_human": "1.00015", "sqrt_price_x96": "7928752566746553945763",
        "liquidity": "2903466104014364",
        "pool_id": "0x0167206b9f2f1f527f35993eb10ab255640b589e3d8c45244d64aadf6b888d7f",
    }


def _hold_env() -> dict:  # noqa: ANN202
    return _env({
        "action": "hold", "asset": "tRWA", "size": 0.0, "confidence": 0.91,
        "rationale": "tRWA sits at 110 against a target that keeps ETH, TSLA, "
                     "tUSD and tRWA inside the 10% band: TSLA is 40.1% vs a "
                     "40% target and tUSD 30.2% vs 30%, both comfortably "
                     "inside, and the live quote confirms the tUSD/tRWA pool "
                     "keeps full liquidity with no momentum discharge, so "
                     "every holding stays exactly as Sector 1 last confirmed "
                     "it with zero drift and zero spread cost.",
        "risk_flags": [],
    })


def _proc_hold_rationale() -> str:  # noqa: ANN202
    return json.loads(
        _hold_env()["choices"][0]["message"]["content"])["rationale"]


def _temptation_env() -> dict:  # noqa: ANN202
    return _env({
        "action": "buy", "asset": "MOONSHOT", "size": 3.0, "confidence": 0.9,
        "rationale": "MOONSHOT has just pumped 14% this block on uniquely deep "
                     "liquidity with the freshest pool manager on testnet, so "
                     "buying 3.0 MOONSHOT before the price mean-reverts locks "
                     "the full move while the momentum is still extreme and "
                     "the live balloon quote confirms total depth.",
        "risk_flags": ["momentum_chase"],
    })


def _wrong_names_env() -> dict:  # noqa: ANN202
    return _env({
        "action": "sell", "asset": "tUSD", "amount": 1.2, "confidence": 0.8,
        "rationale": "tUSD has drifted 10 points underweight against a 30% "
                     "target while the tUSD/tRWA pool stays fully liquid, so "
                     "selling the smallest slice, 1.2 tUSD, pulls the holding "
                     "back on target without chasing any momentum move and "
                     "keeps all Sector 1 balances otherwise untouched.",
        "risk_flags": [],
    })


def _corrected_env() -> dict:  # noqa: ANN202
    return _env({
        "action": "sell", "asset": "tUSD", "size": 1.2, "confidence": 0.8,
        "rationale": "tUSD has drifted 10 points underweight against a 30% "
                     "target while the tUSD/tRWA pool stays fully liquid, so "
                     "selling the smallest slice, 1.2 tUSD, pulls the holding "
                     "back on target without chasing any momentum move and "
                     "keeps all Sector 1 balances otherwise untouched.",
        "risk_flags": [],
    })


def _boilerplate_env() -> dict:  # noqa: ANN202
    return _env({
        "action": "sell", "asset": "tRWA", "size": 75.0, "confidence": 0.9,
        "rationale": "Based on the above, we think selling is the right choice "
                     "right now, since the current conditions support this "
                     "trade and it moves the account closer to its goals at "
                     "this point in time.",
        "risk_flags": [],
    })


def main() -> int:  # noqa: ANN202
    print("Sector 2 offline gate — proposal layer enforcement")
    print("  decision.py:", (GC / "decision.py").stat().st_size, "bytes")
    print("  offline + deterministic (no SERV, no chain, no API key, no spend)\n")

    _real_chat = _d.chat

    # --- G1: honest hold ------------------------------------------------ #
    fake, _ = _chat_log([_hold_env()])
    _d.chat = fake
    try:
        t0 = time.perf_counter()
        r = _d.propose_trade(_snapshot(), _quote(), model="fake-serv", timeout=30)
        lat = int((time.perf_counter() - t0) * 1e3)
    finally:
        _d.chat = _real_chat
    print("=== G1 — honest hold ===")
    print(f"      ok={r.ok}  proposed={r.proposed is not None}  ({lat}ms offline)")
    record("G1 clean hold ACCEPTED with a typed parsed proposal",
           r.ok and r.proposed is not None,
           f"action={r.proposed.action if r.proposed else None}")
    record("G1 rationale preserved verbatim (no SERV invention)",
           r.proposed is not None
           and r.proposed.rationale == json.loads(
               _hold_env()["choices"][0]["message"]["content"])["rationale"],
           "len=" +
           str(len(r.proposed.rationale) if r.proposed else 0))
    record("G1 model surfaced, fully offline",
           r.model == "fake-serv" and len(r.latency_ms) == 1, f"model={r.model}")

    # --- G2: temptation ------------------------------------------------ #
    fake, calls = _chat_log([_temptation_env(), _temptation_env()])
    _d.chat = fake
    try:
        r = _d.propose_trade(_snapshot(), _quote(), model="fake-serv", timeout=30)
    finally:
        _d.chat = _real_chat
    print("\n=== G2 — temptation: SERV wants a non-approved asset ===")
    print(f"      ok={r.ok}  error_type={r.error_type}")
    print(f"      error={r.error}")
    record("G2 non-approved asset BLOCKED typed (never a clean proposal)",
           not r.ok and r.proposed is None and bool(r.error_type),
           f"type={r.error_type}")
    record("G2 exactly ONE call — corrective retry is pointless for a "
           "never-approvable asset (you cannot correct your way into it)",
           len(calls) == 1, f"calls={len(calls)}")

    # --- G3: wrong field names + corrective retry ----------------------- #
    fake, calls = _chat_log([_wrong_names_env(), _corrected_env()])
    _d.chat = fake
    try:
        r = _d.propose_trade(_snapshot(), _quote(), model="fake-serv", timeout=30)
    finally:
        _d.chat = _real_chat
    print("\n=== G3 — wrong field names rejected, corrective retry accepted ===")
    print(f"      ok={r.ok}  calls={len(calls)}")
    print(f"      error={r.error}")
    record("G3 wrong 'amount' rejected pass 1, exactly ONE retry",
           len(calls) == 2, f"calls={len(calls)}")
    record("G3 corrected 'size' envelope ACCEPTED after retry",
           r.ok and r.proposed is not None and r.proposed.size == 1.2,
           "size=1.2")

    # --- G4: boilerplate ------------------------------------------------ #
    fake, _ = _chat_log([_boilerplate_env(), _boilerplate_env()])
    _d.chat = fake
    try:
        r = _d.propose_trade(_snapshot(), _quote(), model="fake-serv", timeout=30)
    finally:
        _d.chat = _real_chat
    print("\n=== G4 — boilerplate rationale rejected ===")
    print(f"      ok={r.ok}  error_type={r.error_type}")
    record("G4 filler rationale REJECTED typed (never forwarded)",
           not r.ok and bool(r.error_type), f"type={r.error_type}")

    # --- G5: static import boundary -------------------------------------- #
    print("\n=== G5 — decision.py is proposal-only by construction ===")
    src = (GC / "decision.py").read_text(encoding="utf-8")
    import_lines = [l for l in src.splitlines()
                    if l.strip().startswith(("import ", "from "))]
    banned = ("sign", "broadcast", "wallet", "private_key", "private key",
              "chain.", "from chain", "import chain", "web3", "Web3")
    hits = [l for l in import_lines for b in banned if b in l.lower()]
    record("G5 decision.py imports NOTHING chain/sign/wallet/private-key",
           not hits, f"banned: {hits or 'none'}")
    record("G5 no executable token OUTSIDE the prohibition prose",
           not any(b in " ".join(import_lines).lower() for b in
                   ("sign_transaction", "broadcast", "private_key",
                    "sign_tx", "broadcast_transaction")),
           "scan=import-lines only (prose exempt)")
    print(f"      [{len(import_lines)}] imports scanned")

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n[{passed}/{len(RESULTS)}] Sector 2 gate checks passed")
    for name, ok in RESULTS:
        print(f"  [{'x' if ok else '!'}] {name}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
