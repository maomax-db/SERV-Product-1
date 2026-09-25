"""Probe: show SERV's RAW reply for the exact Sector 2 prompts, and time it.

This is a diagnostic — it goes around propose_trade() on purpose so we can SEE
what SERV actually returns, instead of just a typed parse error.
"""

import sys, time
from pathlib import Path

GC = Path(__file__).resolve().parent
ROOT = GC.parent
for p in (ROOT, GC, ROOT / "chain"):
    sys.path.insert(0, str(p))

from chain import market, wallet            # noqa: E402
from config import OPENSERV_MODEL           # noqa: E402
from serv import OpenServError, chat        # noqa: E402

# Rebuild the exact prompts the way decision.py does (same prompts, no wrapper).
import decision as D                        # noqa: E402

WALLET = "0x2170105c880B8a5782EDE8ec7B02465f9d3cd981"
tokens = [market.TOKEN_TSLA, market.TOKEN_WETH, market.TOKEN_TUSD, market.TOKEN_TRWA]

w3 = wallet.connect(wallet.NETWORKS["testnet"][0])
snap = wallet.get_full_snapshot(w3, WALLET, tokens=tokens, network="testnet")
q = market.get_quote(w3, market.TOKEN_TUSD, market.TOKEN_TRWA, 100, network="testnet")

snap_d = snap.to_dict()
q_d = q.to_dict()

model = OPENSERV_MODEL
print(f"model = {model!r}")

system_prompt = D._system_prompt()
user_prompt = D._user_prompt(snap_d, q_d, dict(D._DEFAULT_STRATEGY))

print("\n----- calling SERV chat (raw, first attempt only) -----")
t0 = time.perf_counter()
try:
    r = chat(user_prompt=user_prompt, system_prompt=system_prompt,
             model=model, timeout=180)
    dt = time.perf_counter() - t0
    content = r["choices"][0]["message"]["content"]
    print(f"elapsed={dt:.1f}s")
    print("RAW CONTENT (repr, first 2500 chars):")
    print(repr(content[:2500]))
except OpenServError as exc:
    print(f"OpenServError: {exc}")

print("\n----- now the full wrapper (propose_trade) -----")
t0 = time.perf_counter()
res = D.propose_trade(snap_d, q_d, model=model, timeout=180)
dt = time.perf_counter() - t0
print(f"elapsed={dt:.1f}s ok={res.ok} model={res.model}")
print(f"error_type={res.error_type}")
print(f"error={res.error}")
if res.ok and res.proposed:
    print("\nPROPOSED:")
    for k, v in res.proposed.to_dict().items():
        print(f"  {k:>10}: {v}")
