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

## Next up (will be appended as it happens)
- Phase 2: buyer agent runtime — expect LLM tool-loop edge cases, context
  budget management, malformed tool-call recovery.
- Phase 3: live Razorpay test-mode wiring — webhook signature verification
  with real events, payment-link completion UX.
