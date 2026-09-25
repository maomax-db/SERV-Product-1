"""Throwaway live smoke — prove Sector 2 end-to-end ONCE before baking the gate.
Uses the REAL Sector 1 API shapes (wallet.get_full_snapshot / market.get_quote),
then hands SERV the normalized dicts and prints the proposal + latency.
"""

import sys, time
from pathlib import Path

GC = Path(__file__).resolve().parent
ROOT = GC.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(GC))
sys.path.insert(0, str(ROOT / "chain"))

from chain import market, wallet  # noqa: E402
from decision import propose_trade  # noqa: E402
from config import OPENSERV_MODEL
from serv import chat as _chat

WALLET = "0x2170105c880B8a5782EDE8ec7B02465f9d3cd981"
TOKENS = [market.TOKEN_TSLA, market.TOKEN_WETH, market.TOKEN_TUSD, market.TOKEN_TRWA]

w3 = wallet.connect(wallet.NETWORKS["testnet"][0])
snap = wallet.get_full_snapshot(w3, WALLET, tokens=TOKENS, network="testnet")
print("=== Sector 1 snapshot ===")
print(f"network={snap.network} chain_id={snap.chain_id} block={snap.block_number}")
for b in snap.balances:
    print(f"  {b.symbol:>5}  {b.human:.6f}  (raw {b.raw})")

q = market.get_quote(w3, market.TOKEN_TUSD, market.TOKEN_TRWA, 100, network="testnet")
print("\n=== Sector 1 live quote ===")
print(q.to_dict())

print("\n--- calling propose_trade (Sector 2, proposal-only) ---")
t0 = time.perf_counter()
res = propose_trade(snap.to_dict(), q.to_dict(), model="gpt-5.4-mini", timeout=120)
dt = time.perf_counter() - t0
print(f"ok={res.ok}  elapsed={dt:.1f}s  model={res.model}")
print(f"error={res.error}")
print(f"error_type={res.error_type}")
print(f"latency_ms={res.latency_ms}")

print("\n=== RAW SERV REPLY (first attempt) ===")
r1 = chat(
    user_prompt=_smoke_user_prompt(snap, q),
    system_prompt=_smoke_system_prompt(),
    model="gpt-5.4-mini",
    timeout=120,
)
print(repr(r1["choices"][0]["message"]["content"])[:2000])
if res.ok and res.proposed:
    print("\n=== PROPOSED DECISION ===")
    for k, v in res.proposed.to_dict().items():
        print(f"  {k:>10}: {v}")
