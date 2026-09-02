"""ScriptedBrain — a deterministic, reactive mission planner. No API key needed.

This is not a canned walkthrough: it PARSES the brief (budget, exclusions,
pincode, product intent), REACTS to live tool results (search hits, quote
totals, policy blocks, price drift, stock), and ADAPTS (drops items, re-quotes,
escalates instead of retrying). Same contract as the LLM brain — swap freely.

Why it exists: the entire loop — MCP tool use, bounded budgets, the human
gate, graceful degradation — must be demoable, testable, and CI-verifiable
without any external API. The LLMBrain (llm.py) activates when a key exists.
"""

from __future__ import annotations

import re

from .base import Action, AgentContext, FinalAnswer, ToolCall, Wait

DEFAULT_PINCODE = "600001"  # Chennai HQ 🌴

FILLER = {
    "last",
    "month",
    "months",
    "month's",
    "this",
    "the",
    "a",
    "an",
    "my",
    "flavour",
    "flavor",
    "one",
    "same",
    "please",
}
STOPWORDS = FILLER | {
    "restock",
    "buy",
    "get",
    "order",
    "need",
    "want",
    "and",
    "for",
    "with",
    "to",
    "of",
    "in",
    "on",
    "stack",
    "gym",
    "supplies",
    "kit",
    "budget",
    "rupees",
    "rs",
    "pincode",
    "under",
    "within",
    "upto",
    "max",
    "maximum",
    "add",
    "also",
    "some",
    "ship",
    "deliver",
    "give",
    "me",
    "you",
}

_EXCLUSION_RE = re.compile(
    r"(?:don'?t|do\s+not|dont|no|not|avoid|skip|exclude)\s+"
    r"(?:repeat(?:ing)?\s+)?(?:last\s+month'?s?\s+|this\s+month'?s?\s+)?"
    r"([a-z][a-z\- ]{1,25})"
)
_BUDGET_RE = re.compile(
    r"(?:budget|under|upto|up\s+to|within|limit(?:ed)?\s+to|max(?:imum)?(?:\s+of)?)"
    r"\s*(?:of\s+|:)?\s*₹?\s*([\d,]+)"
    r"|₹\s*([\d,]+)",
    re.IGNORECASE,
)
_PINCODE_RE = re.compile(r"\b([1-9][0-9]{5})\b")
_CLAUSE_STRIP_RE = re.compile(
    r"(?:don'?t|do\s+not|dont|no|not|avoid|skip|exclude)\s+[^.,;!?]*", re.IGNORECASE
)


def parse_budget(brief: str) -> int | None:
    m = _BUDGET_RE.search(brief)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    return int(raw.replace(",", ""))


def parse_pincode(brief: str) -> str | None:
    m = _PINCODE_RE.search(brief)
    return m.group(1) if m else None


def parse_exclusions(brief: str) -> list[str]:
    out: list[str] = []
    for m in _EXCLUSION_RE.finditer(brief.lower()):
        words = [w for w in m.group(1).split() if w not in FILLER]
        if words:
            out.append(words[0])
    return out


def parse_query(brief: str) -> str:
    stripped = _CLAUSE_STRIP_RE.sub(" ", brief)  # drop exclusion clauses
    cleaned = re.sub(r"[₹0-9,:\.\-—!?;/\"]", " ", stripped.lower())
    words = [
        w for w in cleaned.split() if w.isalpha() and len(w) > 2 and w not in STOPWORDS
    ]
    return " ".join(words) or "protein"


class ScriptedBrain:
    """Deterministic planner. Phases advance on tool RESULTS, not on a script."""

    name = "scripted-planner"

    def __init__(self, poll_seconds: float = 2.0, max_polls: int = 75):
        self.poll_seconds = poll_seconds
        self.max_polls = max_polls
        self.phase = "start"
        self.budget: int | None = None  # rupees
        self.pincode = DEFAULT_PINCODE
        self.exclusions: list[str] = []
        self.query = ""
        self.mission_id: str | None = None
        self.primary: dict | None = None
        self.primary_results: list[dict] = []
        self.cart: list[dict] = []
        self.quote: dict | None = None
        self.order_id: str | None = None
        self.polls = 0
        self._seen_status_calls = 0
        self.requote_attempted = False
        self.secondary_done = False
        self.fallback_search_done = False

    # ------------------------------------------------------------ helpers
    def _is_excluded(self, product: dict) -> bool:
        name = product.get("name", "").lower()
        return any(ex in name for ex in self.exclusions)

    def _eligible(self, results: list[dict]) -> list[dict]:
        return [r for r in results if r.get("in_stock") and not self._is_excluded(r)]

    def _budget_paise(self) -> int | None:
        return int(self.budget * 100) if self.budget else None

    def _quote_args(self) -> dict:
        return {"items": self.cart, "pincode": self.pincode}

    def _quote_action(self, commentary: str) -> ToolCall:
        return ToolCall("quote_order", self._quote_args(), commentary)

    # ------------------------------------------------------- the planner
    async def next_action(self, ctx: AgentContext) -> Action:
        p = self.phase

        if p == "start":
            self.budget = parse_budget(ctx.mission)
            self.pincode = parse_pincode(ctx.mission) or DEFAULT_PINCODE
            self.exclusions = parse_exclusions(ctx.mission)
            self.query = parse_query(ctx.mission)
            self.phase = "open_mission"
            args: dict = {"brief": ctx.mission}
            if self.budget:
                args["budget_rupees"] = self.budget
            return ToolCall(
                "open_mission",
                args,
                commentary=(
                    f"Parsed the brief — intent: {self.query!r}, budget ₹{self.budget}, "
                    f"exclusions: {self.exclusions or 'none'}, ship to {self.pincode}. "
                    "Registering a bounded mission with the trust plane first."
                ),
            )

        if p == "open_mission":
            r = ctx.last("open_mission") or {}
            self.mission_id = r.get("mission_id")
            self.phase = "manifest"
            return ToolCall(
                "get_store_manifest",
                {},
                commentary="Reading the store's trust limits before planning any spend.",
            )

        if p == "manifest":
            self.phase = "search_primary"
            return ToolCall(
                "search_products",
                {"query": self.query},
                commentary=f"Searching the structured catalog for {self.query!r} "
                "(ranked deterministically; prose never included).",
            )

        if p == "search_primary":
            r = ctx.last("search_products") or {}
            results = r.get("results", [])
            if not results and not self.fallback_search_done:
                self.fallback_search_done = True
                self.query = "protein"
                return ToolCall(
                    "search_products",
                    {"query": "protein"},
                    commentary="No structured matches — broadening the search to 'protein'.",
                )
            cands = self._eligible(results)
            if not cands:
                return FinalAnswer(
                    f"Nothing in stock matches {self.query!r}"
                    + (
                        f" (excluding {', '.join(self.exclusions)})"
                        if self.exclusions
                        else ""
                    )
                    + ". Mission closed without spending a rupee.",
                    False,
                )
            budget_paise = self._budget_paise()
            if budget_paise is not None:
                affordable = [
                    c for c in cands if c["unit_price_paise"] <= budget_paise - 8_000
                ]
                if not affordable:
                    cheapest = min(cands, key=lambda c: c["unit_price_paise"])
                    return FinalAnswer(
                        f"Everything matching {self.query!r} is above the ₹{self.budget:,} budget "
                        f"(cheapest: {cheapest['name']} at ₹{cheapest['unit_price_rupees']:,.0f}). "
                        "Not spending the human's money — escalating instead.",
                        False,
                    )
                pool = affordable
            else:
                pool = cands
            primary = max(pool, key=lambda c: c["rating"])
            self.primary = primary
            self.primary_results = results
            self.cart = [{"product_id": primary["product_id"], "qty": 1}]
            headroom = (
                budget_paise - primary["unit_price_paise"] if budget_paise else 10**12
            )
            if headroom >= 30_000 and not self.secondary_done:
                self.secondary_done = True
                self.phase = "search_secondary"
                return ToolCall(
                    "search_products",
                    {"category": "performance"},
                    commentary=f"Primary pick: {primary['name']} (★{primary['rating']}) at "
                    f"₹{primary['unit_price_rupees']:,.0f}. Budget headroom "
                    f"₹{headroom/100:,.0f} — checking complementary performance items.",
                )
            self.phase = "quote"
            return self._quote_action(
                "Requesting an exact quote — totals are computed by "
                "the store's deterministic engine, never by me."
            )

        if p == "search_secondary":
            r = ctx.last("search_products") or {}
            budget_paise = self._budget_paise()
            headroom = (
                budget_paise - self.primary["unit_price_paise"]
                if budget_paise
                else 10**12
            )
            fits = [
                x
                for x in r.get("results", [])
                if x["unit_price_paise"] <= headroom and not self._is_excluded(x)
            ]
            if fits:
                best = max(fits, key=lambda x: x["rating"])
                self.cart.append({"product_id": best["product_id"], "qty": 1})
                extra = f" Adding {best['name']} (★{best['rating']}) — best rated within headroom."
            else:
                extra = " Nothing complementary fits the remaining budget."
            self.phase = "quote"
            return self._quote_action("Cart assembled." + extra + " Quoting exactly.")

        if p == "quote":
            r = ctx.last("quote_order") or {}
            if not r.get("ok"):
                if len(self.cart) > 1:
                    self.cart.pop()
                    return self._quote_action(
                        f"Quote refused ({r.get('error')}) — dropping the secondary item "
                        "and re-quoting."
                    )
                return FinalAnswer(
                    f"Could not quote the cart: {r.get('error')}. "
                    "Closing gracefully; no money moved.",
                    False,
                )
            self.quote = r
            total = r["totals"]["total_paise"]
            budget_paise = self._budget_paise()
            if budget_paise is not None and total > budget_paise and len(self.cart) > 1:
                self.cart.pop()
                return self._quote_action(
                    f"Quote ₹{total/100:,.0f} exceeds the mission budget — dropping a "
                    "secondary item and re-quoting."
                )
            self.phase = "create_order"
            names = "; ".join(f"{l['name']} ×{l['qty']}" for l in r["lines"])
            return ToolCall(
                "create_order",
                {
                    "quote_id": r["quote_id"],
                    "idempotency_key": f"agent-{self.mission_id}-1",
                    "mission_id": self.mission_id,
                },
                commentary=f"Quote accepted: {names} → ₹{r['totals']['total_rupees']:,.2f} "
                f"(GST incl., {r['totals']['zone']} zone). Creating the order — "
                "idempotently, so retries can't double-order.",
            )

        if p == "create_order":
            r = ctx.last("create_order") or {}
            if not r.get("ok"):
                err = r.get("error")
                if err == "PRICE_DRIFT" and not self.requote_attempted:
                    self.requote_attempted = True
                    self.phase = "quote"
                    return self._quote_action(
                        "Prices moved between quote and order (price-drift guard fired) — "
                        "re-quoting against live prices before deciding."
                    )
                return FinalAnswer(
                    f"Order refused: {err}. No money moved; "
                    "escalating to the human.",
                    False,
                )
            self.order_id = r["order"]["order_id"]
            self.phase = "request_payment"
            return ToolCall(
                "request_payment",
                {"order_id": self.order_id, "mission_id": self.mission_id},
                commentary="Requesting payment. The policy engine checks it; the human "
                "gate decides it. I can wait.",
            )

        if p == "request_payment":
            r = ctx.last("request_payment") or {}
            if not r.get("ok"):
                failed = [c["check"] for c in r.get("checks", []) if not c["passed"]]
                return FinalAnswer(
                    f"Payment request BLOCKED by the trust plane: {r.get('block_reason')} "
                    f"(failed checks: {', '.join(failed) or 'n/a'}). Per policy I escalate "
                    "instead of retrying. No money moved.",
                    False,
                )
            self.phase = "poll"
            self.polls = 0
            return Wait(
                self.poll_seconds, r.get("note", "Waiting for the human's decision…")
            )

        if p == "poll":
            # Only evaluate a status result we haven't seen yet — otherwise we'd
            # sit on a stale read forever while the human decides. (This exact
            # bug shipped in the first version: the agent waited eternally on
            # its first poll. See BUILDLOG.)
            status_calls = sum(
                1 for h in ctx.history if h["tool"] == "get_order_status"
            )
            r = ctx.last("get_order_status")
            if r and r.get("ok") and status_calls > self._seen_status_calls:
                self._seen_status_calls = status_calls
                if r["order"]["order_id"] == self.order_id:
                    status = r["order"]["status"]
                    if status == "paid":
                        ref = r["order"].get("razorpay_payment_id") or "gateway-ref"
                        names = "; ".join(
                            f"{l['name']} ×{l['qty']}"
                            for l in (self.quote or {}).get("lines", [])
                        )
                        return FinalAnswer(
                            f"Mission complete ✅ — {names} for "
                            f"₹{r['order']['total_rupees']:,.2f} (order {self.order_id}, "
                            f"payment {ref}). Every step is in the audit ledger.",
                            True,
                        )
                    if status == "rejected":
                        return FinalAnswer(
                            "The human rejected this payment. Nothing was charged. "
                            "I will not retry without new instructions.",
                            False,
                        )
                    self.polls += 1
                    if self.polls > self.max_polls:
                        return FinalAnswer(
                            "Approval is still pending after the wait window — pausing the "
                            "mission. The human can decide on the dashboard; no money has moved.",
                            None,
                        )
                    return Wait(
                        self.poll_seconds, "Still waiting on the human's decision…"
                    )
            return ToolCall(
                "get_order_status",
                {"order_id": self.order_id},
                commentary=f"Polling order {self.order_id} (poll {self.polls + 1}).",
            )

        return FinalAnswer(
            f"planner reached unknown phase {p!r} — stopping safely",
            False,
        )
