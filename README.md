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

1. An AI buyer opens a mission: *"Restock my gym stack, budget ₹4,000."*
2. It calls MCP tools: `search_products` → `get_product` → `quote_order` (exact GST-inclusive total, computed in code — never by the model) → `create_order`.
3. `request_payment` runs the policy engine (per-txn cap, mission budget, daily budget) and returns **PENDING** — a human approval card, not a charge.
4. A human taps **Approve**. The gateway captures (Razorpay test mode / offline mock). The order is paid, GMV ticks up, and the append-only audit ledger holds the entire decision trail.
5. Attack it: poison a product description with a prompt injection, or shift a price between quote and order — the structured-fields-only catalog, the price-drift check, and the caps absorb all of it. **Failure handled gracefully, on camera.**

Run it yourself: `scripts/mcp_smoke_test.py` performs this exact loop (search → quote → order → pending approval → human approval → paid) against the live MCP server.

## Architecture

```
┌────────────────────────── BUYER PLANE (Phase 2) ─────────────────────────┐
│  Buyer agent runtime: mission → plan → MCP tool calls → poll → report    │
│  (hand-rolled tool loop, no agent framework; descriptions = data only)   │
└──────────────────────────────┬────────────────────────────────────────────┘
                               │ MCP (streamable HTTP, :8001)
┌──────────────────────────────▼────────────────────────────────────────────┐
│  MERCHANT PLANE — AgentDukaan store (:8000)                               │
│  llms.txt + /.well-known/agent.json + storefront + merchant dashboard     │
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

# Terminal 1 — storefront + dashboard + approvals (:8000)
python -m agentdukaan.server.http_app

# Terminal 2 — MCP commerce server (:8001), the agent surface
python -m agentdukaan.server.mcp_server

# Terminal 3 — prove the full agent purchase loop
python scripts/mcp_smoke_test.py

# Tests: the trust plane under attack (15 tests)
python -m pytest tests/ -v
```

No keys needed — the offline mock gateway keeps the loop fully demoable. Set
`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` (test mode) in `.env` to switch to real
Razorpay test-mode orders, payment links, and signature-verified webhooks.

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
test_full_purchase_loop              happy path + idempotent payment request
test_create_order_idempotent_replay  same key → same order, stock decremented once
test_audit_ledger_is_append_only     DB triggers reject UPDATE/DELETE
test_price_drift_blocked             catalog price moved after quote → blocked
test_mission_budget_blocked          spend beyond mission budget → blocked
test_per_txn_cap_blocked             > ₹10,000 order → blocked + escalate guidance
test_quote_expiry_blocked            stale quote → re-quote required
test_rejection_recovers_gracefully   reject → retry → paid
test_out_of_stock_blocked_...        stock validated at quote AND order time
test_gst_split_inclusive_math        18% inclusive split sums exactly
```

## Roadmap

- **Phase 2 — buyer agent runtime**: hand-rolled LLM tool loop (no framework) that consumes this MCP server, mission-scoped, with SSE-streamed reasoning trace.
- **Phase 3 — Razorpay live test mode** + injection-firewall demo script (`scripts/poison_product.py` is ready) + dashboard polish.
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
