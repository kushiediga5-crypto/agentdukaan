# AgentDukaan — PROJECT HANDOVER / SESSION CONTEXT

> **Purpose:** Re-establish full context in a future session (with an AI agent or a human).
> Point the assistant at this file at the start of the next session and say
> "read HANDOVER.md and continue."
>
> **Last updated:** 2026-09-02, end of Session 2
> **Deadline:** Razorpay AI Buildathon application — **Sept 5, 2026, 23:59 IST** (~3 days out)

---

## 0. TL;DR for the next session

- **Project:** AgentDukaan — an agent-ready storefront on Razorpay test mode; AI buyers
  transact end-to-end over MCP; every rupee bounded, gated, audited.
- **Event:** Razorpay AI Buildathon 2026 · **Track 1 — AI Growth & Agentic Commerce**.
- **Status:** Phases 1 + 2 **done and verified live**: merchant plane (MCP commerce
  server), trust plane (policy engine, append-only audit), and the **buyer agent
  runtime** with brain abstraction. Full mission verified end-to-end over SSE+MCP
  with a mid-stream human approval. **24/24 tests green.**
- **Repo:** `/home/user/agentdukaan` (git-initialized, 2 commits, not yet pushed).
- **First commands on a fresh sandbox** (installed packages and running servers do NOT persist):
  ```bash
  cd /home/user/agentdukaan
  pip install -r requirements.txt        # + pip install black (optional, formatting)
  python -m agentdukaan.server.http_app     # terminal 1 — storefront + /agent console :8000
  python -m agentdukaan.server.mcp_server   # terminal 2 — MCP commerce server :8001
  python -m pytest tests/ -v                # 24 tests, all green
  # Then open the live preview → /agent → "Run agent mission" → click Approve when the card appears
  ```
- **Blocked on user for Phase 3:** Razorpay test-account keys (live payments),
  optional LLM API key (real-LLM brain), GitHub repo to push.

---

## 1. The mission (Buildathon facts — verified via web research)

- **Program:** Razorpay AI Buildathon — student-only, no resume screening. Judged on a
  working project → public GitHub repo → 5-min pitch video → panel interview.
- **Role:** AI Builder Intern · ₹75,000/month · 6 or 12 months (choice) · in-person Bangalore from Sept.
- **Apply at:** razorpay.com/buildathon · Deadline **Sept 5, 2026**.
- **Evaluation criteria (published):**
  1. Problem taste — real merchant/finance problem, not a toy
  2. Build quality — clean repo, runs reliably, clone-and-run
  3. AI judgment — AI where it helps, **deterministic code where it doesn't**
  4. Failure recovery — "what broke and how you resolved it" is a required form field
- **Track 1's bar:** every money action explainable, bounded, gated; show the audit trail;
  one failure handled gracefully.
- **Application form fields:** name, college, grad year, Bangalore availability (Y/N),
  duration choice (6/12), resume file, track, project name, problem statement, public
  GitHub URL, unlisted 5-min video URL, what-broke-and-how-fixed.

**Why this project wins (the pitch):** ACP (OpenAI+Stripe) and UCP (Google+Shopify) made
AI buyers real in the US in 2025–26 — both ride on MCP. India's rails (Razorpay/UPI) are
absent from every partner list. AgentDukaan is the missing agentic checkout layer for an
Indian merchant, with a trust plane the protocols hand-wave. One-liner:
*"I made a merchant transactable by AI buyers, end-to-end, on Razorpay — every rupee
bounded, gated, and audited."*

---

## 2. Session log

### Session 1 (2026-09-02, earlier)
1. Track discussion → user committed to **Track 1 at "god level"** (from a low-hours
   Track 4 recommendation — user changed the calculus).
2. Researched agentic commerce landscape (ACP/UCP/MCP/AP2) — India gap = the pitch.
3. Built merchant + trust planes: package `agentdukaan` (config/db/audit/catalog/
   quote_engine/service/policy/payments), MCP server (8 tools, **mcp SDK 2.x** after
   migration from the renamed 1.x API), HTTP storefront/dashboard/approvals/llms.txt.
4. 15 tests green; full purchase loop verified over live MCP; git commit `d09aa0d`.

### Session 2 (2026-09-02, later) — Phase 2: buyer agent runtime ✅
1. User reported "server not loading" — root cause: **background processes and pip
   packages don't survive session boundaries**; preview URLs go stale. Fixed by
   reinstall + restart (now the documented ritual in §0).
2. Built `agentdukaan/agent/` — the buyer plane:
   - `base.py` — Action types (ToolCall/Wait/FinalAnswer), Brain protocol, AgentContext
   - `scripted.py` — **ScriptedBrain**: deterministic reactive planner (parses budget,
     exclusions, pincode, intent from the brief; adapts to quotes/policies; escalates
     instead of retrying). **No API key needed — prototypes the whole agent story.**
   - `llm.py` — **LLMBrain** (Anthropic Messages / OpenAI Chat Completions via raw
     httpx, tool-use) + `get_brain()` factory (auto → LLM if key, else scripted)
   - `clients.py` — `MCPToolClient` (real wire, SDK 2.x 2-tuple) + `DirectToolClient`
     (in-process fallback) + `connect_tools()` with honest degradation note
   - `runtime.py` — AgentRuntime: hand-rolled loop, **step budget**, buyer-plane audit
     events, approval surfacing, brain-crash containment
   - `run.py` — CLI mission runner
3. Wired the **Agent Console** (`/agent`): SSE-streamed missions, live trace, in-page
   Approve/Reject cards that hit the same human-gate endpoint.
4. **Four real bugs found & fixed** (all in BUILDLOG): FinalAnswer arg-order swap;
   stale-poll bug (agent never noticed approval — caught only by live wire test);
   invisible rejections (orders now carry truthful `rejected` status, retries need
   fresh approval); SSE-disconnect teardown hardening.
5. **Live-verified full mission:** brief with exclusion + budget → agent picked Dutch
   Cocoa (skipped excluded Mango Lassi), added Creatine within headroom, ₹3,698 quote,
   human approved mid-stream, agent reported completion. 14 MCP tool calls, all audited.
6. 24/24 tests green; repo formatted with black; git commit (2nd).

**User context (matters for planning):** solo student, AI-assisted builder (Cursor/LLM
APIs, not deep stack background), committed to going all-in through the deadline.
Panel interview will grill them on architecture — build *with* them, not *for* them;
keep explaining the "why" behind every decision.

---

## 3. Current state (verified working)

- ✅ Storefront (:8000) — products, GMV, MCP tool docs, llms.txt, /.well-known/agent.json
- ✅ **Agent Console (/agent)** — type a mission, watch the streamed trace, approve/reject
  in-page. Default brain: scripted. `?brain=llm` once a key exists.
- ✅ Dashboard (:8000/dashboard) — human approval gate, orders, audit timeline
  (now includes buyer-plane mission.start/end events)
- ✅ MCP commerce server (:8001) — 8 tools, SDK 2.x, agent instructions
- ✅ Trust plane — caps/budgets/approvals, idempotency, price-drift, append-only ledger
- ✅ Buyer agent runtime — brain-swap (scripted ↔ LLM), MCP wire + fallback, step budget
- ✅ CLI runner: `python -m agentdukaan.agent.run "<mission>"`
- ✅ 24/24 tests incl. agent missions, late-approval regression, looping-brain containment
- ✅ Gateway: MockGateway (offline default) + RazorpayGateway (coded, activates with keys)
- 🟡 LLMBrain: coded to contract, **live provider calls untested** (no key yet) — honest in BUILDLOG
- 🟡 Razorpay live test mode: coded (orders + payment links + signature-verified webhook),
  **integration untested** (no keys yet)
- 🟡 Injection demo: `scripts/poison_product.py` ready; scripted brain ignores prose by
  construction; **on-camera demo + LLM-brain firewall test pending**

**Verified live numbers (Session 2):** mission "Budget ₹4,000, whey isolate, don't repeat
mango" → Dutch Cocoa ₹2,899 + Creatine ₹799 = ₹3,698, order paid
(`pay_MockTest_ef923460720c03e4`), 14 tool calls, exclusion honored, budget respected.

---

## 4. Repository map

```
agentdukaan/                     (root = repo)
├── README.md                    # judge-facing: pitch, architecture, invariants, quickstart
├── BUILDLOG.md                  # REQUIRED submission material — 9 real failure stories
├── HANDOVER.md                  # ← this file (do not push to GitHub; session context)
├── requirements.txt             # fastapi, uvicorn, pydantic, mcp>=2.0, httpx, pytest, razorpay
├── .env.example                 # RAZORPAY keys, LLM keys, trust knobs
├── agentdukaan/
│   ├── config.py                # env-driven Settings (trust knobs, keys, agent URLs)
│   ├── db.py                    # SQLite WAL schema + append-only triggers
│   ├── audit.py                 # ledger log/recent/count
│   ├── catalog.py               # 14 SKUs + deterministic structured search
│   ├── quote_engine.py          # anti-LLM zone: GST/shipping/totals, stock validation
│   ├── service.py               # Commerce orchestration (both surfaces call this)
│   ├── policy/engine.py         # evaluate_payment → named checks → PolicyDecision
│   ├── payments/gateway.py      # MockGateway | RazorpayGateway
│   ├── agent/                   # ★ BUYER PLANE (Session 2)
│   │   ├── base.py              #   Action types, Brain protocol, AgentContext
│   │   ├── scripted.py          #   ScriptedBrain — deterministic planner, no API key
│   │   ├── llm.py               #   LLMBrain (Anthropic/OpenAI) + get_brain factory
│   │   ├── clients.py           #   MCPToolClient / DirectToolClient / connect_tools
│   │   ├── runtime.py           #   AgentRuntime — the loop, step budget, audit
│   │   └── run.py               #   CLI mission runner
│   └── server/
│       ├── mcp_server.py        # MCP agent surface (SDK 2.x), 8 tools
│       └── http_app.py          # storefront + /agent console (SSE) + dashboard +
│                                #   approvals + webhooks + llms.txt
├── scripts/
│   ├── seed.py                  # explicit init+seed
│   ├── poison_product.py        # prompt-injection payload for the live demo
│   ├── heal_product.py          # restores it
│   └── mcp_smoke_test.py        # raw tool loop against live MCP
├── tests/                       # 24 tests (quote math, attacks, agent missions)
└── data/                        # SQLite DB (gitignored, persists in workspace)
```

**Key entities:** products, quotes (TTL 600s), orders (`created → awaiting_approval →
pending_payment|paid|rejected`), approvals (TTL 300s, human-decided), buyers
(buyer_demo ₹25k/day), missions (brief + hard budget), audit_log (append-only).

---

## 5. Key design decisions (the "why" — for panel defense)

1. **LLM proposes, deterministic engine disposes.** The model never computes totals
   (quote_engine does), never executes payments (only requests → PENDING), never ranks
   search (deterministic sort).
2. **Human approval gate at threshold ₹0** — every payment needs a human decision.
   The agent console, dashboard, and API all hit the same gate.
3. **`decide_approval` is NOT an MCP tool** — the agent surface physically cannot reach
   the gateway. Architecture enforces the boundary, not prompts.
4. **Brain/transport abstraction** — the runtime never changes when you swap
   scripted → LLM brain or MCP → direct tools. Prototype without keys; production
   with keys; tests in CI. This is the "future-dependent" design: any new provider or
   protocol surface plugs into the same two seams.
5. **Descriptions are untrusted content** — search never returns prose; get_product
   flags it; the MCP server's `instructions` and the LLMBrain system prompt both
   forbid following it. The scripted brain ignores prose by construction.
6. **Idempotency at the DB level** (UNIQUE index); **append-only ledger at the DB
   level** (triggers); **money = integer paise** everywhere.
7. **Truthful order states** — `rejected` is visible to the agent; retries are allowed
   but always re-gated by a human.
8. **Mock-first gateway** — entire system demoable/testable offline; Razorpay test
   mode activates with env keys. Honest about tested vs pending (BUILDLOG).
9. **MCP as the commerce surface** — aligned with how ACP/UCP are actually implemented.

---

## 6. Roadmap (Phase 3 next)

### Phase 3 — Razorpay live + injection demo (NEXT)
- User creates free Razorpay test account → keys in `.env` → verify RazorpayGateway:
  order creation, payment link, test-card capture (4111 1111 1111 1111), webhook
  signature, status sync. Update BUILDLOG with the real integration story.
- On-camera injection demo: run `scripts/poison_product.py` → run an LLM-brain mission
  → show the firewall + policy layers absorbing it → `heal_product.py`.
- Optional LLM brain live test once a key exists (scripted brain holds the contract in CI).
- Dashboard polish if time permits (receipts page).

### Phase 4 — Close (Sept 4–5)
- README final pass; architecture diagram (mermaid/draw.io) for the video.
- 5-min pitch video script (agreed flow): cold open ("this store earned ₹X today, no
  human shopped") → mission in Agent Console → live trace → approval card → paid →
  dashboard/ledger walkthrough → poison attack blocked → metrics → what-broke story.
- Push repo public, fill the application form (12 fields — see §1), submit.

---

## 7. Blocked-on-user checklist

- [ ] Razorpay test account keys → `.env` (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`) — Phase 3
- [ ] Optional: LLM API key (Anthropic or OpenAI) → `.env` — flips the console to a real brain
- [ ] GitHub repo created + push (remote may need re-adding after sandbox snapshots:
      `git remote add origin <url>`)
- [ ] Resume + college/grad-year/Bangalore-availability/duration (6 vs 12) for the form
- [ ] Project name confirmation ("AgentDukaan" is the working name)

## 8. Gotchas learned (do not re-learn these)

- **Sessions kill processes + pip packages.** Reinstall and restart (§0). Preview URLs
  are per-session — if the browser "loads nothing," restart servers and use the fresh URL.
- `pip install black` if formatting (repo is black-formatted; keep it that way).
- mcp SDK is **2.x**: `MCPServer` (not FastMCP); client `streamable_http_client` yields
  `(read, write)` — 2 values, not 3.
- SQLite trigger violations raise `IntegrityError` (subclass of `DatabaseError`).
- Money is paise (int). ₹10,000 cap = 1_000_000 paise. Watch the underscores.
- Run servers from repo root (`cd /home/user/agentdukaan`) so `data/` resolves.
- Live wire tests catch what unit tests can't (the stale-poll bug shipped green in CI) —
  always run one live mission after touching the runtime.
- The agent console auto-falls-back to in-process tools if :8001 is down (an honest
  note appears in the trace) — but restart the MCP server for the real demo.
