# Weryon

**Weryon — a guardrail and audit layer for autonomous trading agents operating on Robinhood Chain.** SERV Reasoning acts as the decision-making brain: it looks at a live wallet's holdings on Robinhood Chain (Stock Tokens, USDG stablecoin, ETH) and proposes trades — swaps executed via an on-chain DEX (Uniswap v4, live on Robinhood Chain) — with a plain-English rationale. A guardrail/approval engine — the actual product — sits between SERV's proposal and real execution, enforcing hard risk rules (position limits, max allocation per asset, daily trade caps, approved-token-only), requiring explicit approval above a confidence threshold, and giving the user an instant kill switch. Only approved proposals ever get signed and broadcast as a real transaction. Every decision — proposed, blocked, held, killed, approved, executed — is logged with its rationale, producing a full audit trail rendered by the Weryon dashboard.

**The problem this solves:** an agent with a wallet's private key can, in principle, sign and broadcast any transaction it wants, instantly and irreversibly — there's no built-in human-in-the-loop or audit layer at the chain or wallet level. Robinhood's own Agentic Trading product (the MCP path) has the same underlying gap: an agent instructed to act autonomously can place trades without per-trade confirmation, with the user remaining fully responsible. SERV's own docs describe exactly this class of problem (unbounded reasoning, no traceability, no audit trail) as why most agent products stall in procurement. Weryon is a direct, working answer to that gap — applied at the smart-contract execution layer instead of a brokerage API, which if anything is a higher-stakes surface to prove it on, since on-chain transactions can't be reversed by a broker after the fact.

**What it is NOT:** not a trading strategy/alpha product. The strategy logic can be simple (even dumb) on purpose — the guardrail/audit layer is the star of the demo, not the trading signal.

> **Pivot note:** the original design used Robinhood's brokerage MCP; that path is geo-restricted (US/UK/EU). This build operates **directly on Robinhood Chain via a plain EVM wallet** — permissionless, no account, no login, no KYC. No Robinhood MCP, OAuth, or brokerage-account code exists anywhere in this repo.

Full product context and build plan: [`BUILD_PLAN_1_GUARDRAIL_CHAIN.md`](./BUILD_PLAN_1_GUARDRAIL_CHAIN.md).
Demo video script: [`DEMO_SCRIPT.md`](./DEMO_SCRIPT.md).

---

## Architecture

```
[Wallet: Read Balances/Positions]  →  [SERV Reasoning: Propose Swap]  →  [Guardrail Engine: Approve/Block]  →  [DEX: Execute Swap Tx]
                                                                    ↓
                                                        [Audit Log — every decision]
                                                                    ↓
                                        [Weryon Dashboard — timeline + plain-English rationale]
```

Hard rule baked into the architecture, not just convention: **the execution layer must only accept input that has already passed the guardrail engine, and must be the only code path allowed to sign a transaction.** Enforce this at the interface/type level — the wallet's private key should be reachable only from inside the execution module, nowhere else in the codebase.

This repo is fully self-contained. It talks to Robinhood Chain via public RPC and a DEX (Uniswap v4 PoolManager) — no Robinhood account, login, or MCP OAuth is needed anywhere in this build.

### Live path proven end-to-end (once, on testnet)

SERV proposed; the guardrail ran every hard rule and a human approved; Sector 4 signed and broadcast a real transaction on Robinhood Chain testnet — **sold 1.0 AMD, received 0.176728734657404766 TSLA**. Receipt verified: `0x31098934f4ad34fc8a50a6afe9518897c6a3a16acb28456405435687445c3064`, block `124118170`, status `0x1`, gasUsed `216534`. It is the one `executed` row in the audit log and the dashboard links directly to the explorer.

---

## Stack

Python 3.11 + `requests` + `web3.py`. Reasoning:

- SERV Reasoning is OpenAI Chat Completions wire-compatible: plain HTTP + `Authorization: Bearer` — `requests` is enough.
- Robinhood Chain is fully EVM-compatible (Arbitrum Orbit L2, ETH gas) — standard `web3.py` works as-is for RPC reads and transaction building/signing/broadcast.
- The private key is a local secret (`.env`), and audit logging + the dashboard are file/HTTP oriented — all natural in Python.

```
pip install -r requirements.txt
```

## Repository layout

| Folder        | Purpose                                            | Status                    |
|---------------|----------------------------------------------------|---------------------------|
| `agent-core/` | SERV/OpenServ proposal layer (Sector 2)            | `decision.py`, `serv.py`, `config.py`, `test_sector2.py` |
| `chain/`      | Robinhood Chain RPC + wallet read wrapper (Sector 1)| `wallet.py`, `rpc.py`, `schema.py`, `market.py`, `test_sector1.py` |
| `execution/`  | DEX swap logic — the only signer (Sector 4)        | `executor.py` + `test_sector4.py` |
| `guardrail/`  | Guardrail & approval engine, kill switch (Sector 3)| `engine.py`, `rules.py`, `approval.py`, `killswitch.py`, `dailytrades.py`, `logbook.py`, `test_sector3.py` |
| `ui/`         | Weryon audit dashboard (Sector 5)                  | `index.html` + `record_real_logs.py` |
| `logs/`       | Audit log output (runtime state)                   | `sector3_audit.jsonl`, `killswitch.json`, `daily_count_*.jsonl` |

---

## Key technical facts

- **Robinhood Chain testnet:** Chain ID `46630`, RPC `https://rpc.testnet.chain.robinhood.com`, explorer `https://explorer.testnet.chain.robinhood.com`, faucet `https://faucet.testnet.chain.robinhood.com`
- **Robinhood Chain mainnet:** Chain ID `4663`, RPC `https://rpc.mainnet.chain.robinhood.com`, explorer `https://robinhoodchain.blockscout.com`
- **Fully EVM-compatible** — standard Ethereum tooling (web3.py, ethers.js, Hardhat, Foundry) works as-is. ETH is the gas token on both networks.
- **DEXs live on-chain:** Uniswap v4 PoolManager verified present on both networks. **1inch's APIs support only mainnet chain `4663`** — there is no 1inch quoting/routing on testnet; the testnet path is a direct Uniswap-v4-offset call as built here.
- **No KYC/residency required** to hold, transfer, or swap Stock Tokens on-chain. Only minting/redeeming a Stock Token against the real underlying share goes through Robinhood directly (geo-gated) — this build never touches that path.
- **Disclosure (for judges/users):** Stock Tokens give economic exposure only, not legal or beneficial ownership of the underlying shares.

### Known token contracts — verified on-chain

| Token | Status | Address |
|---|---|---|
| TSLA (testnet) | ✅ Verified live on-chain (code present, 224k holders) | `0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E` |
| WETH (testnet) | ✅ Verified (172k holders) | `0x33e4191705c386532ba27cBF171Db86919200B94` |
| tUSD / tRWA (testnet) | ✅ live, actively-traded v4 pool (only liquid pool found) | `0x43d412f25B2792895A5311689aB07E0E56fCb033` / `0x57f637b5b92ea47598fE2C5e0734E98800D0Cdda` |
| SwapCallback (testnet, used by the live swap) | ✅ live | `0xEB4D00fA6Cbd60E8606dDE504257c66271Ad1a0E` |
| PoolManager (testnet & mainnet) | ✅ live | `0x8366a39CC670B4001A1121B8F6A443A643e40951` |

Mainnet addresses are not hardcoded anywhere in code yet; Sector 1 resolves the per-network registry from the official docs/explorer before any integration.

---

## How to run this yourself

### 1. Gates first (all offline, deterministic, free — no SERV, no chain, no spend)

```powershell
python agent-core/test_sector2.py    # proposal layer (10/10)
python guardrail/test_sector3.py     # the product's proof (8/8)
python execution/test_sector4.py     # execution layer gate (25/25)
python chain/test_sector1.py         # read-only wallet + quotes (needs RPC only)
```

### 2. Populate the audit log with REAL engine output

The gates write to temp dirs on purpose (so a real kill never leaks into repo state). To see a real audit trail in `logs/`, record a run:

```powershell
python ui/record_real_logs.py
```

This runs the **actual Guardrail engine** on the Sector 3 proposal set (7 good, a low-confidence hold, 3 deliberately bad, plus a kill-switch mid-flow) and writes every verdict to `logs/sector3_audit.jsonl` — plus the one real on-chain swap (verified receipt, `0x31098934…c3064`, block 124118170). Re-running it appends new rows; nothing is ever mocked or overwritten.

Verify the store: `python ui/record_real_logs.py --check`.

### 3. Open the Weryon dashboard

Single HTML page, no framework, reads the real `logs/` files at runtime. Serve the repo root so the JSONL fetch works:

```powershell
python -m http.server 8000
# open http://localhost:8000/ui/
```

The dashboard shows the chronological timeline (proposed → approved / blocked / held / killed → executed), per-decision asset, size, confidence, the full plain-English rationale (verbatim from SERV), which rule blocked each trade, the kill-switch state, and an explorer link on the executed swap.

---

## Sector status

### Sector 0 — foundations
Repo skeleton, project-dedicated wallet (`0x2170105c880B8a5782EDE8ec7B02465f9d3cd981`, key only in `.env`, git-ignored), OpenServ connectivity proof, faucet for testnet funds.

### Sector 1 — read-only wallet & market-data layer
`chain/wallet.py` (snapshot + tx history) and `chain/market.py` (live quote) build on `chain/schema.py`. Quotes come from the live Uniswap v4.1 PoolManager storage (pool id = `keccak256(abi.encode(PoolKey))`, `_pools` slot 6, `sqrtPriceX96` = low 128 bits, `liquidity` slot +3) — **verified exact against real Swap events**. Gate: 9/9 pass. Only liquid testnet pair: tUSD → tRWA.

### Sector 2 — proposal layer (`agent-core/decision.py`)
`DecisionResult` / `ProposedDecision`: typed, proposal-only contract. SERV rationale preserved **verbatim**; schema violations are typed (`decision_schema_violation`, `constraint_violation`, `bad_input`, `decision_parse_error`); corrective retries never go silent. Gate: 10/10, fully offline.

### Sector 3 — guardrail & approval engine (THE PRODUCT, `guardrail/`)
`Guardrail.guarded()` (kill-first → hard rules → confidence fence → `V_PENDING`) then `approve_or_blocked()` (human `y` → `V_APPROVED`, anything else → `V_REJECTED`). Fail-closed, typed verdicts, never silently approved.

| Rule | Policy key | Default | Meaning |
|---|---|---|---|
| R1 approved-token list | `approved` | tUSD, tRWA, TSLA, WETH | nothing else is ever approval-eligible |
| R2 max position size | `max_position_pct` (0.40) | ≤40% of wallet per asset | the `9000 tUSD` case (90% of a 10k wallet) is blocked |
| R3 max % per single trade | `max_trade_pct_of_wallet` (0.20) | ≤20% of wallet per trade | |
| R4 max daily trade count | `max_daily_trades` (8) | per UTC day, persistent | |
| R5 max slippage | `max_slippage_bps` (100) | 1.00% worst-case vs live quote | |

Kill switch: file-based (`logs/killswitch.json`), checked before *any* rule and re-checked at approval; armed ⇒ every proposal is `V_KILLED`, including a flawless one. Audit log: every decision writes one JSONL row with `{event, verdict, ok, asset, size, confidence, blocked_by, error_type, error, affected_rule, rationale, risk_flags, timestamp_utc, approved_at, network, chain_id, block_number, model, latency_ms}`. Gate: 8/8.

### Sector 4 — execution layer (THE ONLY SIGNER, `execution/`)
Type-gated: `execute_trade()` accepts **only** an already-approved `ApprovalVerdict`. A raw dict, a raw `DecisionResult`, or any non-approved verdict is refused with typed `unauthorized_type` / `not_approved` / `kill_switch` / `slippage_abort` errors **before any RPC connection is attempted or anything is signed** (proven offline by `[SECTOR4-SIGNING]` marker assertions in the gate). Encoder verified byte-for-byte against 20/20 live dealer swaps; receipts reconciled from real Transfer logs. Gate: **25/25**.

> The Sector 4 gate is now materially stronger than before: it proves the exact rejection site for a raw `DecisionResult`, proves Sector 3's three deliberately-bad proposals are refused before signing, proves both kill-switch and slippage aborts never reach signing, and asserts the signing marker prints exactly once only when a real approved verdict is executed (via stubbed chain, broadcast intercepted).

### Sector 5 — Weryon dashboard (`ui/`)
Single-page, no framework, reads the real `logs/` files at runtime. Timeline per decision; per-entry asset/size/confidence/rationale/blocking rule; kill-switch state; explorer link on the executed swap. Feed it with `record_real_logs.py` (or your own engine run) and serve the repo root.

---

## What's deliberately NOT here

- No user accounts, no real-time push updates, no filtering/search UI (by design — lean demo).
- No private keys outside `.env`, ever. `execution/executor.py:660-661` forbids hardcoded signers; AST-scanned by the Sector 3/4 gates.
- No Robinhood brokerage/MCP/OAuth code anywhere.

---

## Notes & status board (honest)

- Testnet has no 1inch routing and mostly dormant stock pools; the liquid pair is tUSD/tRWA. The live swap sailed through the guardrail + human approval path as the product's end-to-end proof.
- The dashboard's data is exactly as sparse/dense as the log store. If `logs/` is empty, the page says so — it never invents entries.
- A fresh testnet faucet claim mints 0.01 ETH + 5 of each Stock Token, once per 24h, which funds the live branch of Sector 4 (`yes` + funds required; skipped otherwise, exit 0).