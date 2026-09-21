# Product #1 — Guardrail & Audit Layer for Agentic Trading (Robinhood Chain Edition)

**Repo:** separate, standalone repo. **Submission track:** Mainnet & MCP.
**This is the primary submission — treat it as complete and shippable on its own, with no dependency on Product #2.**

> **Pivot note:** This replaces the original Robinhood MCP / brokerage-account version. Robinhood's brokerage and Agentic accounts are restricted to US/UK/EU residents. Robinhood Chain — the underlying blockchain — is permissionless: any EVM wallet can hold, transfer, and trade Stock Tokens on it, no KYC or residency required. The track description explicitly allows either path ("agents that act on Robinhood Chain **or** operate funds via Robinhood MCP"), so this pivot is fully valid for submission, not a workaround.

---

## 1. What We're Building (memorize this)

**A guardrail and audit layer for autonomous trading agents operating on Robinhood Chain.** SERV Reasoning acts as the decision-making brain: it looks at a live wallet's holdings on Robinhood Chain (Stock Tokens, USDG stablecoin, ETH) and proposes trades — swaps executed via an on-chain DEX (Uniswap, 1inch, or similar, all already live on Robinhood Chain) — with a plain-English rationale. A guardrail/approval engine — the actual product — sits between SERV's proposal and real execution, enforcing hard risk rules (position limits, max allocation per asset, daily trade caps, approved-token-only), requiring explicit approval above a confidence threshold, and giving the user an instant kill switch. Only approved proposals ever get signed and broadcast as a real transaction. Every decision — proposed, blocked, approved, executed — is logged with its rationale, producing a full audit trail.

**The problem this solves:** an agent with a wallet's private key can, in principle, sign and broadcast any transaction it wants, instantly and irreversibly — there's no built-in human-in-the-loop or audit layer at the chain or wallet level. Robinhood's own Agentic Trading product (the MCP path) has the same underlying gap: an agent instructed to act autonomously can place trades without per-trade confirmation, with the user remaining fully responsible. SERV's own docs describe exactly this class of problem (unbounded reasoning, no traceability, no audit trail) as why most agent products stall in procurement. This build is a direct, working answer to that gap — applied at the smart-contract execution layer instead of a brokerage API, which if anything is a higher-stakes surface to prove it on, since on-chain transactions can't be reversed by a broker after the fact.

**What it is NOT:** not a trading strategy/alpha product. The strategy logic can be simple (even dumb) on purpose — the guardrail/audit layer is the star of the demo, not the trading signal.

**Judging criteria this targets:**
- *Creativity* → bounded reasoning + enforced guardrails, applied directly at the point of irreversible on-chain execution — a literal enactment of SERV's own pitch, on the highest-stakes surface available
- *User-readiness* → this is the missing piece that makes autonomous wallet-controlling agents safe to actually hand to a non-technical user
- *Revenue potential* → compliance/audit tooling is what unlocks institutional adoption of agentic on-chain trading — the exact "procurement" blocker SERV's docs name, now demonstrated on real DeFi rails

---

## 2. Architecture at a Glance

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

## 3. Key Technical Facts (reference these when prompting the coding agent)

- **Robinhood Chain testnet:** Chain ID `46630`, RPC `https://rpc.testnet.chain.robinhood.com`, explorer at `explorer.testnet.chain.robinhood.com`, faucet available for test funds
- **Robinhood Chain mainnet:** Chain ID `4663`, RPC `https://rpc.mainnet.chain.robinhood.com`, explorer `robinhoodchain.blockscout.com`
- **Fully EVM-compatible** — standard Ethereum tooling (web3.py, ethers.js, ecosystem wallets) works as-is
- **Known mainnet Stock Token contracts** (verify current addresses before use, these can change/expand):
  - TSLA (Tesla): `0x322F0929c4625eD5bAd873c95208D54E1c003b2d`
  - NVDA (NVIDIA): `0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC`
  - AAPL (Apple): `0xaF3D76f1834A1d425780943C99Ea8A608f8a93f9`
  - USDG (Global Dollar stablecoin — likely quote asset for swaps): `0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168`
- **DEXs live on Robinhood Chain:** Uniswap (dedicated AMM), 1inch (aggregator, confirmed supporting the chain since launch), Lighter, Arcus, Rialto — 1inch's API is likely the simplest integration path since it handles routing/calldata for you rather than requiring raw router-contract calls
- **No KYC needed to hold, transfer, or swap** these tokens on-chain. Only *minting or redeeming* a Stock Token against the real underlying share requires going through Robinhood directly, which is geo-gated — this build never needs to touch that path

**Open question to resolve early in Sector 0:** confirm whether the *testnet* actually has live Stock Token contracts and DEX liquidity, or whether these only exist on mainnet. If testnet is empty/synthetic, the practical build path may be: build and test all guardrail logic against testnet plumbing, then do final small-amount demos directly on mainnet (gas is cheap ETH L2 fees, no KYC blocker either way).

---

## 4. Sector-by-Sector Build Plan

Each sector has a **Test Gate**. Do not proceed until every box is checked.

---

### Sector 0 — Foundation & Access
**Day 1 (half day)**

Tasks:
- [ ] Generate a new wallet (private key + address) dedicated to this project — never reuse a personal wallet
- [ ] Add Robinhood Chain testnet to a wallet tool (MetaMask, or purely programmatic via web3.py) using the chain ID/RPC above
- [ ] Get testnet funds from the faucet
- [ ] Confirm OpenServ API access still works (same as before — no change here)
- [ ] Investigate: does testnet have real Stock Token contracts + DEX liquidity, or only mainnet? Check `explorer.testnet.chain.robinhood.com` directly and/or Robinhood Chain docs
- [ ] Scaffold repo folders: `/agent-core` (SERV calls), `/chain` (RPC + wallet read/write wrapper), `/execution` (DEX swap wrapper), `/guardrail`, `/ui`, `/logs`
- [ ] README with the one-paragraph product description from Section 1, plus the key technical facts from Section 3

**Test Gate:**
- [ ] OpenServ API call returns a valid response for a trivial prompt
- [ ] Wallet successfully connects to Robinhood Chain testnet RPC and reads its own (likely zero/faucet) balance
- [ ] Faucet funds received and visible in wallet balance
- [ ] Clear answer documented in README on the testnet-liquidity open question above
- [ ] Repo skeleton committed with README

🛑 **Do not proceed if this fails.** If testnet turns out to have no usable Stock Token liquidity, decide now whether to build against mainnet directly with minimal funds, and document that decision before Sector 1.

---

### Sector 1 — Read-Only Wallet & Market Data Layer
**Day 1–2**

Tasks:
- [ ] Wrapper functions: get wallet ETH/USDG/Stock Token balances, get current swap price/quote for a given pair (via 1inch API or direct DEX pool query), get recent transaction history for the wallet
- [ ] Normalize all responses into one internal schema
- [ ] No SERV reasoning yet — pure data plumbing

**Test Gate:**
- [ ] Full wallet snapshot (all token balances) matches what the block explorer shows for the same address
- [ ] Can fetch a live quote for at least one pair (e.g. USDG → TSLA) and the numbers look sane (compare against a manual check on the explorer or 1inch UI if available)
- [ ] Snapshot/quote functions return in a few seconds, not longer
- [ ] Malformed/empty RPC or API response caught and logged, does not crash

---

### Sector 2 — SERV Reasoning Decision Layer (core differentiator #1)
**Day 2–3**

Tasks:
- [ ] Define decision schema, e.g.:
  ```json
  {
    "action": "buy | sell | hold",
    "asset": "string (token symbol or contract address)",
    "size": "number",
    "confidence": "0-1",
    "rationale": "plain-English string",
    "risk_flags": ["string"]
  }
  ```
- [ ] Prompt SERV/BRAID with: wallet snapshot + strategy rule-set (start simple: rebalance-to-target or basic momentum) + hard constraints (approved-token-list only, no leverage/perps — spot swaps only, max slippage tolerance)
- [ ] Force structured JSON output — no free-form prose
- [ ] Rationale field must be genuinely explanatory (this becomes the audit trail)

**Test Gate:**
- [ ] 5 synthetic wallet states → SERV returns valid, schema-conformant JSON every time, zero parse failures
- [ ] At least 1 test case where the "obvious" move violates a known constraint (e.g. proposes a token not on the approved list) → SERV correctly flags/blocks it rather than proposing it cleanly
- [ ] Rationale reads as real explanation, not boilerplate filler
- [ ] Confirm **no code path exists yet from this sector to execution** — literally cannot sign or broadcast a transaction at this point

**Fallback if behind schedule:** hardcode a simple rule-based strategy, use SERV only for the risk-flagging + rationale pass. Still legitimately "leverages SERV Reasoning," much faster to make reliable.

---

### Sector 3 — Guardrail & Approval Engine (THE PRODUCT — protect this sector above all else)
**Day 3–5**

Tasks:
- [ ] Hard rule checks: max position size, max % of wallet per trade, max daily trade count, approved-token-list enforcement, max acceptable slippage
- [ ] Confidence threshold: below X → auto-hold + flag for review; above X → still requires explicit approval unless a pre-declared "autopilot" mode is on
- [ ] Approval interface: CLI confirmation is fine for MVP ("Approve this trade? y/n"); a simple web button is a nice-to-have
- [ ] Kill switch: one command halts all trading immediately, regardless of in-flight state — and must actually prevent a signature/broadcast, not just set a flag the execution layer might ignore

**Test Gate:**
- [ ] 10 synthetic SERV proposals fed in, including 3 deliberately bad ones (oversized trade, non-approved token, exceeds daily cap) → all 3 blocked, all 7 good ones allowed
- [ ] Kill switch tested live mid-flow → confirm nothing executes afterward even if already marked "approved"
- [ ] Every approved/blocked decision writes a log entry
- [ ] Latency check: proposal → approval-request should be near-instant

🎯 **Sectors 2 + 3 together are already a complete, demoable submission.** Everything below is enhancement, not requirement.

---

### Sector 4 — Execution Layer (DEX Swap)
**Day 5–6**

Tasks:
- [ ] Execution function only accepts input that has passed Sector 3 — enforced structurally, not by convention
- [ ] Build/sign/broadcast the swap transaction (via 1inch API for quote + calldata, or a direct Uniswap-style router call) — test with the smallest possible amount first
- [ ] Post-trade: pull the transaction receipt, confirm it landed on-chain, reconcile against the proposal (amount received vs. expected, within slippage tolerance)

**Test Gate:**
- [ ] One real, tiny swap executed end-to-end: SERV proposes → guardrail approves → transaction signed and broadcast → confirmed on-chain (verify on the block explorer, not just your own logs)
- [ ] One deliberately-blocked trade confirmed to never reach the point of transaction construction/signing at all
- [ ] Manually feed a fake bad fill into reconciliation and confirm the mismatch is actually caught

---

### Sector 5 — Audit Dashboard & Demo Packaging
**Day 6–7**

Tasks:
- [ ] Dashboard: timeline of proposals → approvals/blocks → executed swaps, each showing the plain-English rationale, with a link to the on-chain transaction/explorer page for executed ones
- [ ] Record 2–3 min demo video showing both a blocked trade and an approved+executed trade (the block is the more interesting moment — lead with it; showing the executed trade live on the block explorer is a strong, credible visual)
- [ ] Write X submission post (name, concept, images, GitHub/demo links, tag @openservai)
- [ ] Fill the official Typeform submission form — one submission for this repo, targeting the **Mainnet & MCP** track

**Test Gate:**
- [ ] A person unfamiliar with the project understands the problem being solved within 30 seconds of watching the demo
- [ ] Dashboard shows at least one real blocked trade from actual Sector 3 testing, not a cherry-picked scripted outcome
- [ ] At least one executed trade is verifiably visible on the public Robinhood Chain block explorer

---

## 5. Time Budget Discipline

| Day | Checkpoint |
|---|---|
| 1 | Sector 0 + 1 done |
| 2–3 | Sector 2 done |
| 3–5 | Sector 3 done (protect this above all else) |
| 5–6 | Sector 4 done |
| 6–7 | Sector 5, submit |

**If it's Day 5 and Sector 3 isn't clean yet:** this repo is the required, primary submission — protect it. Cut nothing from here; if anything, this is where extra time should be pulled toward, not away from.

---

## 6. Known Platform Constraints (design around these, don't fight them)

- No KYC/residency requirement for on-chain wallet activity — **but** minting/redeeming a Stock Token against the real underlying share is geo-gated and goes through Robinhood directly; this build never needs that path
- Stock Tokens give economic exposure only, not legal/beneficial ownership of the underlying shares — worth a one-line disclosure in the demo/README so judges see it's understood
- DEX liquidity for some Stock Tokens may be thin, especially on testnet — design the guardrail's slippage/size limits with this in mind rather than assuming deep liquidity
- All trades are spot swaps — no shorting, no leverage, no perps in this build (keep it simple and defensible)
- The wallet's private key is the entire security boundary — treat key handling with real care even in a hackathon build (never commit it, never log it, store only in `.env`/local secrets)

---

## 7. Optional Cross-Repo Hook (do not build until this repo's Sector 5 is done)

If time remains after this repo is fully complete and submitted, this repo can optionally expose a simple read endpoint or log export (e.g. `GET /surplus-cash`) that Product #2 can poll. This is a one-way, optional integration — Product #2's build plan handles its own side of this. Do not spend time on this hook before Section 5's checkpoints are all green.
