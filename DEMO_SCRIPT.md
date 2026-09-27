# Weryon — Demo Video Script (2–3 minutes)

The demo you actually see must match one thing: **real data**. Everything below
is currently true of this repo, run locally. Watch the yours-to-run notes; if a
live step needs something you haven't done (funded wallet, API key), you keep the
script and swap the live step for the offline proof that already exists in the
gates.

---

## Order of the video (do NOT reorder)

### a. The one-sentence problem (≈20s)

> "An agent that holds a wallet's private key can sign and broadcast any
> transaction instantly and irreversibly — there is no human-in-the-loop and no
> audit trail anywhere at the chain or wallet level."

Say it while showing the repo root or a terminal at rest. Keep it to one breath.
No product name yet, no features — just the problem.

### b. Show a BLOCKED trade first — the most important screen (≈50s)

Open the dashboard: `python -m http.server 8899` → `http://localhost:8899/ui/`.
The timeline is chronological (oldest → newest), so find the **red** entries and
zoom on one:

- **What you show:** `BUY MOONSHOT, size 10, conf 0.98` → verdict **BLOCKED**,
  rule `approved_list`.
- **Say:** "SERV wants to buy a token that is not on the approved list. The
  guardrail stops it with a typed verdict — before any contract, before any
  signature, before any gas is spent."
- **Why this first:** a guardrail demo that opens with an approval looks like a
  demo of a broker. Opening with a block proves the layer refuses, instantly and
  for a stated reason. This is the perceivable value.

Scroll to the second red/blocked card and say one line so it doesn't feel
single-case: "Same refusal for an oversized 90%-of-wallet trade
(`rule_position_size`) and a trade that hits the daily cap (`rule_daily_count`).
And the plain-English rationale for every decision is right there in the card —
never a mystery, always reviewable."

### c. Show the APPROVED + EXECUTED trade, then click through to the real tx (≈60s)

Find the **green approved** card and then the **purple executed** card (the
one dated 2026-09-25 — it carries the real on-chain timestamp). Point to the
plan-English rationale on the approved trade, then:

- **Show:** the `approve` verdict for a real trade (e.g. `BUY TSLA` size 400),
  then the purple **executed** card.
- **Click the explorer link** (`Explorer tx — 0x31098934…c3064`). It goes to
  `https://explorer.testnet.chain.robinhood.com/tx/0x31098934f4ad34fc8a50a6afe9518897c6a3a16acb28456405435687445c3064`.
- **Say:** "This executed row is a real transaction. Clicking it opens the
  explorer for tx `0x31098934…c3064` on Robinhood Chain testnet, block
  124,118,170, mined successfully — the wallet sold 1.0 AMD and received
  0.1768 TSLA. Approved once by the guardrail rules + a human, and only then did
  anything reach the chain."
- Close the loop: "Proposed → guarded → approved by a human → executed → logged
  with the real on-chain hash. The audit trail is not a summary of what MIGHT
  have happened; it points at what DID."

> Yours-to-run note: if you haven't run `python ui/record_real_logs.py`, `logs/`
> is empty and the dashboard shows no executed row. Run it first (it is included
> in the repo and uses the real engine + the real tx). If you would rather show
> your own live swap, follow Sector 4's live branch (funded wallet + human `yes`)
> and the executed row will appear with your tx hash.

### d. Close with the SERV Reasoning sentence (≈20s)

> "Every decision here was proposed by SERV Reasoning, the decision-maker, and
> passed to a guardrail that reasons about risk the same way it reasons about
> reward — with a typed reason for every yes and every refusal, in plain
> English, on-chain-verifiable."

Freeze on the dashboard → end card: **Weryon — Guardrail & Audit Layer for
Agentic Trading.**

---

## Checklist before you record

- [ ] `python ui/record_real_logs.py` ran and printed `Done: N rows ...` (N ≥ 15)
- [ ] `python -m http.server 8899` serving, dashboard loads at `/ui/`
- [ ] The blocked MOONSHOT / oversized / daily-cap cards show `rule_*` reasons
- [ ] The approved card shows a rationale in plain English
- [ ] The executed card shows the explorer link — clicking it opens the real tx
- [ ] README claims match what's on screen (don't oversell; sparse data is fine)

## Timing budget (2m45s total)

| Segment | Time | Move |
|---|---|---|
| a. Problem | 0:00–0:20 | repo / terminal at rest |
| b. BLOCKED trades | 0:20–1:10 | dashboard, red cards |
| c. APPROVED → EXECUTED → explorer | 1:10–2:25 | green/purple cards, click-through |
| d. SERV Reasoning close | 2:25–2:45 | freeze on dashboard, end card |