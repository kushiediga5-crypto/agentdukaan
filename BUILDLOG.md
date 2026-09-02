# BUILDLOG — what broke, and how it got fixed

Honest engineering log. This is a required part of the Buildathon submission
("what broke and how you resolved it") — every entry below is real.

---

## Day 1 — Foundation + merchant plane + trust plane

### 1. `mcp` SDK 2.x broke the server on first boot (import-time crash)
**Broke:** `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` — pip
resolved `mcp>=1.9` to **2.x**, where `FastMCP` was renamed `MCPServer` and the
client API changed too.

**Fix:** Migrated instead of pinning back:
- server: `from mcp.server.mcpserver import MCPServer`; constructor no longer
  takes `host`/`port` — passed at `run(transport="streamable-http", host=…, port=…)`.
- client: `streamablehttp_client` → `streamable_http_client`, and it now
  yields a **2-tuple** `(read, write)` instead of 3 (the session-id getter is
  gone). The smoke test crashed on `expected 3, got 2` — inspected the
  installed source (`inspect.getsource`), confirmed the yield, adjusted.

**Lesson:** SDK majors break at exactly two places: imports and context-manager
signatures. Read the installed source, not the blog posts.

### 2. A bad edit de-indented a loop body — tests caught it instantly
**Broke:** While adding the stock check to `quote_engine.build_lines`, an edit
de-indented `lines: list[QuoteLine] = []` into the wrong block. File parsed
fine; at runtime every quote died with `UnboundLocalError: lines`.

**Fix:** Test suite went 9-red in 0.16s and pointed at the exact line.
Rewrote the whole module cleanly rather than fuzzing another patch on top.

**Lesson:** The 0.1s test run is the cheapest debugger in the repo.

### 3. Tests exposed a real design gap: quotes ignored stock
**Broke:** `test_out_of_stock…` failed because `quote_order` happily quoted
items that didn't exist in stock — the check only happened at `create_order`.

**Fix:** Stock validation added at quote time (`insufficient stock for X: N
available`) *and* kept the atomic decrement at order time (`BEGIN IMMEDIATE`)
for race safety between quote and order.

### 4. SQLite trigger errors surfaced as `IntegrityError`, not `OperationalError`
**Broke:** The append-only-ledger test expected `OperationalError` from
`RAISE(ABORT, …)` triggers; SQLite raises `IntegrityError`.

**Fix:** Assert the common parent `sqlite3.DatabaseError` — the contract is
"the ledger cannot be mutated," not a specific exception class.

### 5. Order timelines were missing their own quotes
**Broke:** `get_order_status` filtered audit events by `order_id`, but the
`quote_order` event predates the order — timelines started mid-story.

**Fix:** Timeline now matches on `order_id` OR `quote_id`, so every order's
ledger view starts at the quote that created it.

---

## Day 1 (evening) — Phase 2: the buyer agent runtime

### 6. The agent worked perfectly and reported the wrong thing (argument-order bug)
**Broke:** The scripted brain completed a full mission flawlessly — right
products, right budget, payment paid — but its final report failed the tests:
`FinalAnswer(True, "Mission complete…")` vs a dataclass defined as
`FinalAnswer(text, success)`. Success and text were silently swapped, so every
result event carried `success="Mission complete ✅…"`. The test failure output
*was* the correct mission report — the bug was in the reporting, not the agent.

**Fix:** Swapped all 9 call sites (with a small parser script that respects
string-literal parens), then `black` for formatting. Lesson: read the failure
text before the failure — the agent's own report told us it had succeeded.

### 7. LIVE bug the unit tests couldn't see: the agent never noticed the approval
**Broke:** End-to-end SSE test against the real MCP wire: agent shopped,
quoted ₹3,698, requested payment, human approved (gateway paid!) — and the
agent kept printing "Still waiting on the human's decision…" forever. Root
cause: the poll phase evaluated `ctx.last("get_order_status")` on every
invocation, but only *waited* after the first read — it never issued a second
status call, so it sat on a stale `awaiting_approval` result while the order
was already paid. The instant-approval unit tests approved before the first
poll, so the first (and only) read already said `paid` — hiding the bug.

**Fix:** The brain now versions what it has seen: it only evaluates a status
result that is newer than the last one it consumed, and otherwise issues a
fresh poll call. Added a regression test where the human approves only after
3+ polls have happened (`test_mission_completes_when_human_decides_late`).
Lesson: unit tests verify components; only a live wire test verifies the
*loop*.

### 8. Design gap: rejections were invisible to the agent
**Broke:** When a human rejected a payment, the order silently returned to
status `created` (to allow retry) — indistinguishable from "nobody decided
yet". The agent couldn't tell a "no" from a "not yet".

**Fix:** Rejected orders now carry a truthful `rejected` status; the policy
engine allows re-requests from `rejected` orders, and every retry requires a
fresh human approval — the human stays in the loop while the agent gets an
honest signal.

### 9. SSE disconnects exploded the MCP session teardown
**Broke:** Closing a browser tab mid-mission (or curl timing out) cancelled
the SSE generator, and the MCP client's `close()` blew up through anyio's
cancel scope with `RuntimeError: Attempted to exit a cancel scope that isn't
the current task's current cancel scope`.

**Fix:** Teardown is best-effort and cancellation-safe in `MCPToolClient.close()`.
An abandoned mission simply ends; the ledger keeps everything up to the last
audited step.

---

## Next up (will be appended as it happens)
- Phase 3: live Razorpay test-mode wiring — webhook signature verification
  with real events, payment-link completion UX.
- Phase 3: LLMBrain live verification once an API key exists (the scripted
  brain holds the contract in CI until then).
