# AgentDukaan 🛍️

**An agent-ready storefront on Razorpay test mode — transactable by AI buyers end-to-end, where every rupee is bounded, gated, and audited.**

> Built for the Razorpay AI Buildathon · Track 1 — AI Growth & Agentic Commerce

---

## The problem

2025–26 made AI buyers real: OpenAI + Stripe's **ACP** powers Instant Checkout inside ChatGPT, Google + Shopify's **UCP** powers checkout in Google AI Mode, and both are designed to ride on **MCP**. Merchants in the US are becoming "agent-transactable."

**India's rails aren't.** Scan the ACP/UCP partner lists — Visa, Mastercard, PayPal, Stripe, Shopify, Walmart — and you won't find Razorpay or UPI. An Indian merchant has no agentic checkout layer, and the protocol literature hand-waves the part that matters most in payments: *what stops the agent?*

AgentDukaan is that layer, built small and honest:

- a storefront any AI buyer can **discover** (`llms.txt`, `/.well-known/agent.json`) and **transact** with (an MCP commerce server), and
- a **trust plane** where every money action is bounded, gated, and audited — the agent can *request* a payment, only a human can *execute* it.

## What it does (the 60-second demo)

1. Open the **Agent Console** (`/agent`) and type a mission: *"Restock my gym stack, budget ₹4,000, whey isolate — don't repeat last month's mango flavour."*
2. The buyer agent streams its reasoning live: parses the brief, opens a **bounded mission** with the trust plane, searches the structured catalog over MCP, skips the excluded product, adds the best-rated complementary item that fits the headroom, and requests an exact quote (GST computed in code — never by the model).
3. It creates the order (idempotently) and **requests** payment. The policy engine runs; the agent gets a PENDING approval — and waits.
4. The approval card appears in your stream. You tap **Approve**. The gateway captures (Razorpay test mode / offline mock), the agent notices on its next poll and files its mission report.
5. Attack it: poison a product description with a prompt injection, or shift a price between quote and order — the structured-fields-only catalog, the price-drift check, and the caps absorb all of it. **Failure handled gracefully, on camera.**

The agent's brain is **swappable**: a deterministic scripted planner (default — no API key, fully testable in CI) or a real LLM brain (Anthropic/OpenAI — activates the moment a key is in `.env`). The runtime, tools, and trust plane never change.

Run it yourself: `scripts/mcp_smoke_test.py` performs the raw tool loop; the `/agent` console runs the full mission with live streaming.

## Architecture

```
┌────────────────────────── BUYER PLANE — agent/ ───────────────────────────┐
│  AgentRuntime (hand-rolled loop, no framework): step budget, audits,      │
│  approval surfacing. Brains only DECIDE:                                  │
│    ScriptedBrain (deterministic, no API key — default, CI-testable)      │
│    LLMBrain (Anthropic/OpenAI — activates with a key)                     │
│  Tool clients: MCPToolClient (real MCP wire) ⇄ DirectToolClient (fallback)│
└──────────────────────────────┬────────────────────────────────────────────┘
                               │ MCP (streamable HTTP, :8001)
┌──────────────────────────────▼────────────────────────────────────────────┐
│  MERCHANT PLANE — AgentDukaan store (:8000)                               │
│  llms.txt + /.well-known/agent.json + storefront + AGENT CONSOLE (/agent, │
│  SSE-streamed missions + in-page approval cards) + merchant dashboard     │
│  MCP tools: get_store_manifest · search_products · get_product ·          │
│    quote_order · create_order · request_payment · get_order_status ·     │
│    open_mission                                                           │
└──────────────┬─────────────────────────────────────────────────┬──────────┘
               │                                                 │
┌──────────────▼───────────── TRUST PLANE ────────────▼─────────────────────┐
│  Deterministic quote engine (GST, shipping zones — zero LLM)              │
│  Policy engine: per-txn cap · mission budget · daily budget · approval    │
│  Idempotency: UNIQUE idempotency_key (DB-enforced) · one approval/order   │
│  APPEND-ONLY audit ledger (SQLite triggers reject UPDATE/DELETE)          │
│  Human gate: request_payment → PENDING → decide_approval → gateway        │
└──────────────────────────────┬────────────────────────────────────────────┘
                               │
                ┌──────────────▼──────────────┐
                │  Gateway: mock (offline) or │
                │  Razorpay TEST MODE (keys)  │
                │  + signature-verified       │
                │    webhooks                 │
                └─────────────────────────────┘
```

### The trust invariants

1. **The LLM never touches money math.** Totals, GST, and shipping are computed by `quote_engine` — pure functions, integer paise, no floats.
2. **The agent never executes a payment.** `request_payment` can at most yield a *pending approval*. `decide_approval` is human-only (HTTP dashboard/API; deliberately **not** an MCP tool).
3. **Every money action is re-validated at execution time.** `create_order` recomputes the quote from live catalog prices — any drift blocks the order (`PRICE_DRIFT`). Stock decrements atomically (`BEGIN IMMEDIATE`).
4. **Replays can't double-charge.** Orders are keyed by `idempotency_key` (UNIQUE index — the database itself rejects doubles); payment requests reuse the same pending approval.
5. **Failures degrade gracefully.** Human rejects → order returns to `created`, retryable. Quote expires → agent told to re-quote. Blocked payment → agent told to escalate, with the full check list.
6. **Everything is audited.** Append-only ledger, enforced by SQLite triggers — `UPDATE`/`DELETE` physically raise. Every block carries its reason.

### AI judgment (where the model is *not* used)

Deterministic on purpose: search ranking, pricing/GST/shipping, budget checks, idempotency, order state machine. The LLM's job (Phase 2 buyer agent) is query understanding, product selection, and reporting — bounded by structured tool I/O. Product descriptions are treated as **untrusted content**: `search_products` never returns prose, `get_product` flags it, and the server's MCP `instructions` tell agents to treat it as data.

## Quickstart

```bash
pip install -r requirements.txt

# Terminal 1 — storefront + dashboard + AGENT CONSOLE (:8000)
python -m agentdukaan.server.http_app

# Terminal 2 — MCP commerce server (:8001), the agent surface
python -m agentdukaan.server.mcp_server

# Terminal 3 — run a buyer-agent mission in the CLI (streams the trace)
python -m agentdukaan.agent.run "Restock my gym stack. Budget ₹4,000. Whey isolate, don't repeat last month's mango. Pincode 600001"

# Or open http://localhost:8000/agent in a browser and watch it live,
# approval card included. Also: the raw tool loop —
python scripts/mcp_smoke_test.py

# Tests: the trust plane under attack + full agent missions (24 tests)
python -m pytest tests/ -v
```

No keys needed — the offline mock gateway and the scripted brain keep everything
demoable and CI-verifiable. Add `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` to `.env`
and the same console runs on a real LLM brain. Set `RAZORPAY_KEY_ID`/
`RAZORPAY_KEY_SECRET` (test mode) for real Razorpay orders, payment links, and
signature-verified webhooks.

## MCP tools (the agent surface)

| Tool | What it does | Guard |
|---|---|---|
| `get_store_manifest()` | Discover store, limits, policies | — |
| `search_products(query, category?, max_price?)` | Deterministic structured search | prose never included |
| `get_product(id)` | Full product detail | description flagged untrusted |
| `quote_order(items, pincode)` | Exact GST-inclusive quote | computed in code only |
| `create_order(quote_id, idempotency_key)` | Quote → order | price-drift + stock + idempotency |
| `request_payment(order_id)` | Ask for money | policy engine → PENDING only |
| `get_order_status(order_id)` | State + audited timeline | — |
| `open_mission(brief, budget_rupees)` | Declare intent + hard budget | enforced on every payment |

## Tests — the "bounded & gated" evidence

```
test_full_purchase_loop               happy path + idempotent payment request
test_create_order_idempotent_replay   same key → same order, stock decremented once
test_audit_ledger_is_append_only      DB triggers reject UPDATE/DELETE
test_price_drift_blocked              catalog price moved after quote → blocked
test_mission_budget_blocked           spend beyond mission budget → blocked
test_per_txn_cap_blocked              > ₹10,000 order → blocked + escalate guidance
test_quote_expiry_blocked             stale quote → re-quote required
test_rejection_recovers_gracefully    reject → truthful status → re-request → paid
test_out_of_stock_blocked_…           stock validated at quote AND order time
test_gst_split_inclusive_math         18% inclusive split sums exactly
--- buyer agent ---
test_mission_full_loop_respects_…     agent skips excluded product, stays in budget, paid
test_mission_budget_too_small_…       nothing affordable → refuses to spend, no order
test_mission_human_rejection_…        rejection → agent reports, never auto-retries
test_mission_completes_when_…_late    regression: agent notices a decision made after polling
test_runtime_step_budget_stops_…      a broken/looping brain cannot spin or reach money
test_events_are_json_serializable     every SSE event is wire-safe
```

## Roadmap

- ~~**Phase 2 — buyer agent runtime**~~ ✅ shipped: brain abstraction (scripted/LLM),
  runtime with step budget, MCP tool client with fallback, SSE Agent Console
  with in-page approval cards, CLI runner, 7 new tests.
- **Phase 3 — Razorpay live test mode** + injection-firewall demo on camera
  (`scripts/poison_product.py` is ready) + LLM brain live verification.
- **Phase 4 — evals, pitch video, architecture walkthrough.**

See `BUILDLOG.md` for what broke and how it got fixed — it's part of the submission.

## Repo layout

```
agentdukaan/
├── agentdukaan/
│   ├── config.py            # env-driven settings (all trust knobs)
│   ├── db.py                # SQLite WAL schema; append-only audit triggers
│   ├── audit.py             # audit ledger API
│   ├── catalog.py           # seed data + deterministic search
│   ├── quote_engine.py      # GST/shipping/totals — the anti-LLM zone
│   ├── service.py           # orchestration: quote→order→gate→pay, all audited
│   ├── policy/engine.py     # caps, budgets, approval threshold, checks
│   ├── payments/gateway.py  # mock + Razorpay test mode
│   └── server/
│       ├── mcp_server.py    # MCP agent surface (mcp SDK 2.x)
│       └── http_app.py      # storefront + dashboard + approvals + webhooks
├── scripts/                 # seed · poison/heal (demo) · mcp_smoke_test
└── tests/                   # 15 tests incl. the attack scenarios
```
