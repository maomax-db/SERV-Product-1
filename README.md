# SERV Guardrail

**A guardrail and audit layer for autonomous trading agents operating on Robinhood Chain.** SERV Reasoning acts as the decision-making brain: it looks at a live wallet's holdings on Robinhood Chain (Stock Tokens, USDG stablecoin, ETH) and proposes trades — swaps executed via an on-chain DEX (Uniswap, 1inch, or similar, all already live on Robinhood Chain) — with a plain-English rationale. A guardrail/approval engine — the actual product — sits between SERV's proposal and real execution, enforcing hard risk rules (position limits, max allocation per asset, daily trade caps, approved-token-only), requiring explicit approval above a confidence threshold, and giving the user an instant kill switch. Only approved proposals ever get signed and broadcast as a real transaction. Every decision — proposed, blocked, approved, executed — is logged with its rationale, producing a full audit trail.

**The problem this solves:** an agent with a wallet's private key can, in principle, sign and broadcast any transaction it wants, instantly and irreversibly — there's no built-in human-in-the-loop or audit layer at the chain or wallet level. Robinhood's own Agentic Trading product (the MCP path) has the same underlying gap: an agent instructed to act autonomously can place trades without per-trade confirmation, with the user remaining fully responsible. SERV's own docs describe exactly this class of problem (unbounded reasoning, no traceability, no audit trail) as why most agent products stall in procurement. This build is a direct, working answer to that gap — applied at the smart-contract execution layer instead of a brokerage API, which if anything is a higher-stakes surface to prove it on, since on-chain transactions can't be reversed by a broker after the fact.

**What it is NOT:** not a trading strategy/alpha product. The strategy logic can be simple (even dumb) on purpose — the guardrail/audit layer is the star of the demo, not the trading signal.

> **Pivot note:** the original design used Robinhood's brokerage MCP; that path is geo-restricted (US/UK/EU). This build operates **directly on Robinhood Chain via a plain EVM wallet** — permissionless, no account, no login, no KYC. No Robinhood MCP, OAuth, or brokerage-account code exists anywhere in this repo.

Full product context and build plan: [`BUILD_PLAN_1_GUARDRAIL_CHAIN.md`](./BUILD_PLAN_1_GUARDRAIL_CHAIN.md).

---

## Architecture

```
[Wallet: Read Balances/Positions]  →  [SERV Reasoning: Propose Swap]  →  [Guardrail Engine: Approve/Block]  →  [DEX: Execute Swap Tx]
                                                                                    ↓
                                                                        [Audit Log — every decision]
                                                                                    ↓
                                                                        [Dashboard — timeline + rationale]
```

Hard rule baked into the architecture, not just convention: **the execution layer must only accept input that has already passed the guardrail engine, and must be the only code path allowed to sign a transaction.** Enforce this at the interface/type level — the wallet's private key should be reachable only from inside the execution module, nowhere else in the codebase.

This repo is fully self-contained. It talks to Robinhood Chain via public RPC and a DEX (or DEX aggregator) — no Robinhood account, login, or MCP OAuth is needed anywhere in this build.

---

## Stack

Python 3.11 + `requests` + `web3.py`. Reasoning:

- SERV Reasoning is OpenAI Chat Completions wire-compatible: plain HTTP + `Authorization: Bearer` — `requests` is enough.
- Robinhood Chain is fully EVM-compatible (Arbitrum Orbit L2, ETH gas) — standard `web3.py` works as-is for RPC reads and, later, transaction building/signing/broadcast.
- The private key is a local secret (`.env`), and audit logging + the dashboard are file/HTTP oriented — all natural in Python.

```
pip install -r requirements.txt
```

## Repository layout

| Folder        | Purpose                                            | Status (Sector 1)             |
|---------------|----------------------------------------------------|-------------------------------|
| `agent-core/` | SERV/OpenServ API call logic                       | `serv.py` + config + test     |
| `chain/`      | Robinhood Chain RPC + wallet read wrapper          | `wallet.py`, `rpc.py`, `schema.py`, `market.py`, `test_sector1.py` |
| `execution/`  | DEX swap logic — *will be the only signer* (Sector 4) | empty                      |
| `guardrail/`  | Guardrail & approval engine, kill switch (Sector 3)| empty                         |
| `ui/`         | Audit dashboard (Sector 5)                         | empty                         |
| `logs/`       | Audit log output                                   | empty                         |

---

## Key technical facts

- **Robinhood Chain testnet:** Chain ID `46630`, RPC `https://rpc.testnet.chain.robinhood.com`, explorer `https://explorer.testnet.chain.robinhood.com`, faucet `https://faucet.testnet.chain.robinhood.com`
- **Robinhood Chain mainnet:** Chain ID `4663`, RPC `https://rpc.mainnet.chain.robinhood.com`, explorer `https://robinhoodchain.blockscout.com`
- **Fully EVM-compatible** — standard Ethereum tooling (web3.py, ethers.js, Hardhat, Foundry) works as-is. ETH is the gas token on both networks.
- **DEXs live on-chain:** Uniswap (v4 pool manager verified present on both networks) and 1inch (aggregator). **1inch's APIs support only mainnet chain `4663`** — there is no 1inch quoting/routing on testnet; direct Uniswap-v4-offset calls would be needed there.
- **No KYC/residency required** to hold, transfer, or swap Stock Tokens on-chain. Only minting/redeeming a Stock Token against the real underlying share goes through Robinhood directly (geo-gated) — this build never touches that path.
- **Disclosure (for judges/users):** Stock Tokens give economic exposure only, not legal or beneficial ownership of the underlying shares.

### Known token contracts — verified today (2026-09-21)

| Token | Status | Address |
|---|---|---|
| TSLA (testnet) | ✅ Verified live on-chain (code present, 224k holders) | `0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E` |
| WETH (testnet) | ✅ Verified (172k holders) | `0x33e4191705c386532ba27cBF171Db86919200B94` |
| TSLA (mainnet, plan-listed) | ⚠️ No code on testnet — mainnet-only, re-verify at Sector 1 | `0x322F0929c4625eD5bAd873c95208D54E1c003b2d` |
| NVDA / AAPL / USDG (plan-listed) | ⚠️ Same — plan's addresses are mainnet; **all must be re-resolved per network** | (see plan §3) |
| USDG mainnet | ❌ `0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168` has **no code on testnet** | mainnet-only |
| tUSD / tRWA (testnet) | ✅ live, actively-traded v4 pool (only liquid pool found) | `0x43d412f25B2792895A5311689aB07E0E56fCb033` / `0x57f637b5b92ea47598fE2C5e0734E98800D0Cdda` |

Mainnet addresses are not hardcoded anywhere in code yet; Sector 1 resolves the per-network registry from the official docs/explorer before any integration.

---

## Setup for a new developer

### 1. Get an OpenServ API key

Register at <https://console.openserv.ai>, create an API key, then:

```powershell
Copy-Item .env.example .env
# edit .env → OPENSERV_API_KEY=sk-...
```

| Variable            | Default                                       | Purpose                     |
|---------------------|-----------------------------------------------|-----------------------------|
| `OPENSERV_API_KEY`  | *(none)*                                      | Required — SERV auth        |
| `OPENSERV_BASE_URL` | `https://inference-api.openserv.ai/v1`        | Inference API base          |
| `OPENSERV_MODEL`    | `gpt-5.4-mini`                                | Default model               |

### 2. Create a project wallet

A fresh, project-dedicated wallet (never reuse a personal one):

```powershell
python -c "from web3 import Web3; acct=Web3().eth.account.create(); print('ADDRESS =', acct.address); print('PRIVATE KEY =', acct.key.hex())"
```

Copy the private key into `.env` as `PRIVATE_KEY=0x...` (git-ignored). The address is derived from the key, so you never configure it separately.

> **Key security is the whole game:** the private key exists only in `.env`, is never committed, never logged, and Sector 4's execution module will be the *only* code allowed to sign. `chain/wallet.py` reads the key but only ever prints the derived address and balances.

### 3. Get testnet funds from the faucet

Open <https://faucet.testnet.chain.robinhood.com> and paste your wallet address (or ENS). **Each claim sends 0.01 testnet ETH plus 5 of each Stock Token** (TSLA, AMZN, …) — once per 24 hours. The faucet is a human/browser step and is *not* called automatically by any code in this repo.

```powershell
python chain/wallet.py            # testnet (default): chain-id 46630 check + ETH balance
python chain/wallet.py --network mainnet   # read-only mainnet check
```

### 4. Verify SERV connectivity

```powershell
python agent-core/test_openserv.py
```

Prints the raw response JSON; a clean run ends `OK — OpenServ API returned a valid response.`, exit code 0.

---

## Testnet-liquidity status (Sector 0 finding, sourced)

**Question:** does Robinhood Chain *testnet* have live Stock Token contracts and DEX liquidity, or is that mainnet-only?

**Finding — stock tokens: YES on testnet.** Canonical testnet TSLA verified live on-chain (address above, 18 decimals, 224,135 holders). The official faucet was observed minting it in real time today ("sendTokensAndEther", 5 × 1e18 TSLA per claim). [faucet.testnet.chain.robinhood.com](https://faucet.testnet.chain.robinhood.com)

**Finding — DEX infrastructure: YES on testnet.** Uniswap v4 Positions-NFT contracts are live on the testnet explorer (`0x58daec…`, 966 holders; `0x00EB69…`, 60 holders), i.e. real LP positions exist. V3 position-NFT contracts are also listed, though hood.dev's notes claim only V4 is usable on testnet ("The testnet is not a dry run. Robinhood testnet (chain 46630) has no Uniswap V3 — only V4"). [hood.dev docs](https://hood.dev/docs/chain)

**Finding — routing layer: testnet ≠ mainnet.** 1inch's developer portal lists only mainnet chain `4663` for all swap/orderbook APIs — **no 1inch quotes or calldata on testnet**. The practical testnet swap path is a direct Uniswap v4 contract call. [1inch supported chains](https://business.1inch.com/portal/documentation/overview/supported-chains)

**Addresses differ per network.** All addresses in the build plan's §3 (TSLA/NVDA/AAPL/USDG) return **no deployed code on testnet** (verified via `eth_getCode` for WETH `0x0Bd7…` and USDG `0x5fc5…`) — they are mainnet addresses. They remain mainnet-only until re-verified at Sector 1.

**Working verdict (for you to confirm):** develop and test all plumbing + guardrail logic on testnet (tokens exist, DEX live, faucet-funded cheap); keep 1inch/Uniswap routing and the final small-amount swap demo for **mainnet**, where 1inch + Uniswap pairs are confirmed live. No decision made here — you decide; README documents both options.

---

## Sector 0 status & what is assumed

Done this session: repo skeleton (+`/chain`), project-dedicated wallet generated (address `0x2170105c880B8a5782EDE8ec7B02465f9d3cd981`) with key only in `.env`, `chain/wallet.py` RPC + chain-ID check + balance read (exit 0, balance 0), OpenServ test re-confirmed (raw `pong` response, exit 0), README + `.env.example` updated for the pivot.

**Still on the human (you):** get testnet funds from the faucet; confirm the testnet-vs-mainnet demo decision above.

**Nothing built assumes funded balances** — Sector 1 (read-only wallet/market-data layer) starts only after the Sector 0 gate (above) is confirmed.

---

## Sector 1 status — read-only wallet & market-data layer

Done this session. Run the gate test:

```powershell
python chain/test_sector1.py
```

**What it provides (all read-only, signs nothing):**

| Function | Where | Returns |
|---|---|---|
| `get_full_snapshot(w3, address, tokens)` | `chain/wallet.py` | `chain.schema.Snapshot` — native + per-token balances (raw + human) |
| `get_transaction_history(w3, address, tokens)` | `chain/wallet.py` | `list[chain.schema.TransferItem]` — ERC-20 Transfer events via RPC-native `eth_getLogs` |
| `get_quote(w3, token_in, token_out, amount_in)` | `chain/market.py` | `chain.schema.Quote` — live v4 quote from direct pool-state storage reads |

**How the quote is sourced (verified against real Swap events, probe 1–22):**
Robinhood Chain (testnet & mainnet) hosts a Uniswap v4.1 PoolManager at
`0x8366a39CC670B4001A1121B8F6A443A643e40951`. The quote is computed from the
pool's **live storage**, not a simulation or a third-party pricing API:

- pool id = `keccak256(abi.encode(PoolKey))` — full 32 bytes (verified)
- `_pools` mapping slot = **6** (verified)
- `sqrtPriceX96` = low 128 bits of `_pools[id]+0` (verified: matches Swap events)
- `liquidity` = low 128 bits of `_pools[id]+3` (verified: exact match)

For the only liquid pair found (tUSD → tRWA, pool
`0x0167206b9f2f…b888d7f`), the storage read at block *N* equals the pool's Swap
event at block *N* — liquidity exactly, sqrt within natural drift. Quote math is
constant-product exact-in over the current state (fee tiers > 1e6 are dynamic
encodings, handled as no LP fee).

**Findings that matter for the demo (honest, on-chain verified):**
- **No TSLA/WETH or TSLA/USDG pool exists on testnet.** The config grid
  (fee × tick-spacing × hooks) was searched on the active PoolManager; only
  meme↔meme pools trade (tUSD/tRWA is the notable liquid one, ~2.9e15 units of
  liquidity, swaps every few hundred blocks). TSLA/meme LP positions exist at
  ~121,24x,xxx blocks but are dormant/collected.
- Therefore the Sector 1 **live-quote demo uses tUSD → tRWA** (a real, actively
  traded pool), and the test also validates the graceful "no pool" path for
  stock pairs. If you want the quote demo on a TSLA pair, we would have to wait
  for a TSLA pool to appear, or use a TSLA↔meme pair if one gets liquidity.
- Timings on testnet RPC: snapshot ≈ 16 s, quote ≈ 8 s, history ≈ 18 s (public
  gateway throttles ~1.2 s/call → keep call count low). Malformed input returns
  a well-formed `Quote.error` — never raises.
- The faucet claim tx also carries a second TSLA `Transfer` from zero-address
  with a huge amount (faucet seeds its own ecosystem pool); genuine on-chain
  data, surfaced as-is.

**Gate result: 9/9 checks PASS** (snapshot matches explorer values ETH=0.01 /
TSLA=5.0; quote sane; timing; graceful malformed/RPC failure; history shows the
faucet claim). Cross-checkable live: explorer address link above, pool manager
`0x8366a39CC670B4001A1121B8F6A443A643e40951`.

> Still decided by you (Sector 2 input): which pair should be the *demo target* —
> tUSD/tRWA (liquid today) or a TSLA pair (needs liquidity to appear)? Sector 2
> builds on top of `chain.schema`, so the choice is a config change, not a redesign.