"""CommerceService — the single orchestration point for all commerce actions.

Both the MCP server (agent surface) and the HTTP app (human surface) call into
this class. Rules that hold everywhere:

  * Money is paise (int). Totals are computed ONLY by quote_engine.
  * create_order validates the quote against LIVE catalog prices (price-drift
    check) and is idempotent on idempotency_key (enforced by a UNIQUE index).
  * request_payment runs the policy engine. The agent can at most obtain a
    PENDING approval. Only a human decision (decide_approval) can trigger the
    gateway.
  * Everything writes to the append-only audit ledger, with reasons.
"""
from __future__ import annotations

import json
import secrets

from . import audit, catalog, db, quote_engine
from .config import settings
from .payments import get_gateway
from .policy.engine import evaluate_payment

_BASE = settings.approval_base_url


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class Commerce:
    # ------------------------------------------------------------------ meta
    def store_manifest(self) -> dict:
        return {
            "store": "AgentDukaan",
            "tagline": "Transactable by AI buyers. Razorpay test mode.",
            "currency": "INR",
            "test_mode": True,
            "categories": ["protein", "performance", "health", "gear", "snacks"],
            "trust": {
                "approval_threshold_rupees": settings.approval_threshold_paise / 100,
                "per_txn_cap_rupees": settings.per_txn_cap_paise / 100,
                "daily_budget_rupees": settings.daily_budget_paise / 100,
                "quote_ttl_seconds": settings.quote_ttl_seconds,
                "returns": "7-day returns on unopened items",
                "gst_invoice": True,
            },
            "note_for_agents": (
                "Call search_products / get_product to explore, quote_order for an "
                "exact price (never compute totals yourself), then create_order + "
                "request_payment. Payments require a human approval; poll "
                "get_order_status until decided."
            ),
        }

    # --------------------------------------------------------------- catalog
    def search_products(
        self,
        query: str = "",
        category: str | None = None,
        max_unit_price_rupees: int | None = None,
        in_stock_only: bool = True,
    ) -> dict:
        results = catalog.search(query, category, max_unit_price_rupees, in_stock_only)
        seq = audit.log(
            actor="agent", plane="merchant", action="search_products",
            payload={"query": query, "category": category, "max_price": max_unit_price_rupees},
            decision="ok", detail={"matches": len(results)},
        )
        return {"ok": True, "audit_seq": seq, "count": len(results), "results": results}

    def get_product(self, product_id: str) -> dict:
        product = catalog.get(product_id)
        if product is None:
            seq = audit.log(
                actor="agent", plane="merchant", action="get_product",
                payload={"product_id": product_id}, decision="error",
                detail={"reason": "not_found"},
            )
            return {"ok": False, "audit_seq": seq, "error": "product_not_found"}
        seq = audit.log(
            actor="agent", plane="merchant", action="get_product",
            payload={"product_id": product_id}, decision="ok",
        )
        return {"ok": True, "audit_seq": seq, "product": product}

    # ----------------------------------------------------------------- quote
    def quote_order(self, items: list[dict], pincode: str) -> dict:
        try:
            pin = quote_engine.validate_pincode(pincode)
            lines = quote_engine.build_lines(items, catalog.get_raw)
            t = quote_engine.totals(lines, pin)
        except quote_engine.QuoteError as exc:
            seq = audit.log(
                actor="agent", plane="merchant", action="quote_order",
                payload={"items": items, "pincode": pincode}, decision="error",
                detail={"reason": str(exc)},
            )
            return {"ok": False, "audit_seq": seq, "error": str(exc)}

        quote_id = _new_id("q")
        now = db.utcnow()
        from datetime import datetime, timedelta, timezone

        expires = (
            datetime.now(timezone.utc) + timedelta(seconds=settings.quote_ttl_seconds)
        ).isoformat(timespec="seconds")
        items_json = json.dumps(
            [{"product_id": l.product_id, "qty": l.qty} for l in lines]
        )
        with db.conn() as c:
            c.execute(
                "INSERT INTO quotes (quote_id, pincode, items_json, subtotal_paise, gst_paise,"
                " shipping_paise, total_paise, status, created_at, expires_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (quote_id, pin, items_json, t["subtotal_paise"], t["gst_paise"],
                 t["shipping_paise"], t["total_paise"], "open", now, expires),
            )
        seq = audit.log(
            actor="agent", plane="merchant", action="quote_order",
            payload={"quote_id": quote_id, "items": items, "pincode": pin},
            decision="ok",
            detail={"total_paise": t["total_paise"], "zone": t["zone"]},
        )
        return {
            "ok": True,
            "audit_seq": seq,
            "quote_id": quote_id,
            "expires_at": expires,
            "lines": quote_engine.line_dicts(lines),
            "totals": {
                # Exact integers (paise) — agents and auditors get precise math.
                "subtotal_paise": t["subtotal_paise"],
                "gst_paise": t["gst_paise"],
                "shipping_paise": t["shipping_paise"],
                "total_paise": t["total_paise"],
                # Human-readable rupees for display.
                "subtotal_rupees": round(t["subtotal_paise"] / 100, 2),
                "gst_rupees": round(t["gst_paise"] / 100, 2),
                "shipping_rupees": round(t["shipping_paise"] / 100, 2),
                "total_rupees": round(t["total_paise"] / 100, 2),
                "zone": t["zone"],
            },
        }

    # ----------------------------------------------------------------- order
    def create_order(self, quote_id: str, idempotency_key: str, mission_id: str | None = None) -> dict:
        if not idempotency_key or len(idempotency_key) > 128:
            return {"ok": False, "error": "idempotency_key required (1..128 chars)"}

        # Idempotent replay: same key => same order, no double-create. The
        # UNIQUE index on idempotency_key is the physical guarantee.
        with db.conn() as c:
            existing = c.execute(
                "SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        if existing:
            seq = audit.log(
                actor="agent", plane="merchant", action="create_order",
                payload={"idempotency_key": idempotency_key}, decision="ok",
                detail={"idempotent_replay": True, "order_id": existing["order_id"]},
            )
            return {"ok": True, "audit_seq": seq, "order": self._order_dict(existing),
                    "idempotent_replay": True}

        with db.conn() as c:
            quote = c.execute("SELECT * FROM quotes WHERE quote_id = ?", (quote_id,)).fetchone()
        if quote is None:
            return {"ok": False, "error": "quote_not_found"}

        # --- Guard 1: quote expiry -----------------------------------------
        now = db.utcnow()
        if quote["status"] != "open" or now >= quote["expires_at"]:
            seq = audit.log(
                actor="policy", plane="trust", action="create_order.expiry_check",
                payload={"quote_id": quote_id}, decision="blocked",
                detail={"reason": "QUOTE_EXPIRED", "now": now, "expires_at": quote["expires_at"]},
            )
            return {"ok": False, "audit_seq": seq, "error": "QUOTE_EXPIRED",
                    "detail": "quote is no longer open — request a fresh quote"}

        # --- Guard 2: price drift ------------------------------------------
        # Recompute the quote from LIVE catalog prices and diff against the
        # stored quote. Any mismatch => block. This kills "quote a low price,
        # order at a higher one" and poisoned-catalog attacks.
        items = json.loads(quote["items_json"])
        try:
            lines = quote_engine.build_lines(items, catalog.get_raw)
            t = quote_engine.totals(lines, quote["pincode"])
        except quote_engine.QuoteError as exc:
            seq = audit.log(
                actor="policy", plane="trust", action="create_order.drift_check",
                payload={"quote_id": quote_id}, decision="blocked",
                detail={"reason": "CATALOG_CHANGED", "error": str(exc)},
            )
            return {"ok": False, "audit_seq": seq, "error": "CATALOG_CHANGED", "detail": str(exc)}

        drift = abs(t["total_paise"] - quote["total_paise"])
        if drift > settings.price_drift_tolerance_paise:
            seq = audit.log(
                actor="policy", plane="trust", action="create_order.drift_check",
                payload={"quote_id": quote_id}, decision="blocked",
                detail={"reason": "PRICE_DRIFT", "quoted_total_paise": quote["total_paise"],
                        "live_total_paise": t["total_paise"], "drift_paise": drift},
            )
            return {"ok": False, "audit_seq": seq, "error": "PRICE_DRIFT",
                    "detail": {"quoted_total_paise": quote["total_paise"],
                               "live_total_paise": t["total_paise"]}}

        # --- Guard 3: stock, atomically ------------------------------------
        order_id = _new_id("ord")
        try:
            with db.conn() as c:
                c.execute("BEGIN IMMEDIATE")
                for l in lines:
                    cur = c.execute(
                        "UPDATE products SET stock = stock - ? WHERE product_id = ?"
                        " AND stock >= ?",
                        (l.qty, l.product_id, l.qty),
                    )
                    if cur.rowcount != 1:
                        raise quote_engine.QuoteError(f"insufficient stock for {l.product_id}")
                c.execute(
                    "INSERT INTO orders (order_id, quote_id, items_json, total_paise, status,"
                    " idempotency_key, mission_id, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (order_id, quote_id, quote["items_json"], t["total_paise"], "created",
                     idempotency_key, mission_id, now, now),
                )
                c.execute("UPDATE quotes SET status = 'consumed' WHERE quote_id = ?", (quote_id,))
                c.execute("COMMIT")
        except quote_engine.QuoteError as exc:
            seq = audit.log(
                actor="policy", plane="trust", action="create_order.stock_check",
                payload={"quote_id": quote_id}, decision="blocked",
                detail={"reason": "OUT_OF_STOCK", "error": str(exc)},
            )
            return {"ok": False, "audit_seq": seq, "error": "OUT_OF_STOCK", "detail": str(exc)}

        seq = audit.log(
            actor="agent", plane="merchant", action="create_order",
            payload={"order_id": order_id, "quote_id": quote_id, "mission_id": mission_id},
            decision="ok", detail={"total_paise": t["total_paise"]},
        )
        with db.conn() as c:
            order = c.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return {"ok": True, "audit_seq": seq, "order": self._order_dict(order)}

    # --------------------------------------------------------------- payment
    def request_payment(self, order_id: str, mission_id: str | None = None) -> dict:
        with db.conn() as c:
            order = c.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if order is None:
            return {"ok": False, "error": "order_not_found"}

        # Idempotency: one live approval per order.
        if order["status"] == "awaiting_approval":
            with db.conn() as c:
                apr = c.execute(
                    "SELECT * FROM approvals WHERE order_id = ? AND status = 'pending'"
                    " ORDER BY requested_at DESC LIMIT 1", (order_id,),
                ).fetchone()
            if apr:
                seq = audit.log(
                    actor="agent", plane="trust", action="request_payment",
                    payload={"order_id": order_id}, decision="ok",
                    detail={"idempotent_replay": True, "approval_id": apr["approval_id"]},
                )
                return {"ok": True, "audit_seq": seq, "status": "pending_approval",
                        "approval_id": apr["approval_id"],
                        "approval_url": f"{_BASE}/dashboard",
                        "idempotent_replay": True}
        if order["status"] == "paid":
            return {"ok": True, "status": "already_paid", "order": self._order_dict(order)}

        mission, buyer = None, None
        with db.conn() as c:
            if mission_id:
                mission = c.execute(
                    "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
                ).fetchone()
                mission = dict(mission) if mission else None
            buyer = c.execute(
                "SELECT * FROM buyers WHERE buyer_id = ?", ("buyer_demo",)
            ).fetchone()
            buyer = dict(buyer) if buyer else None

        decision = evaluate_payment(
            order_status=order["status"],
            amount_paise=order["total_paise"],
            mission=mission,
            buyer=buyer,
        )

        if not decision.approved:
            seq = audit.log(
                actor="policy", plane="trust", action="request_payment",
                payload={"order_id": order_id, "amount_paise": order["total_paise"]},
                decision="blocked",
                detail={
                    "block_reason": decision.block_reason,
                    "checks": [c.as_dict() for c in decision.checks],
                },
            )
            return {"ok": False, "audit_seq": seq, "status": "blocked",
                    "block_reason": decision.block_reason,
                    "checks": [c.as_dict() for c in decision.checks],
                    "next_step": "escalate to the human; do not retry without new instructions"}

        # Approved to SEEK human approval (never to take money).
        approval_id = _new_id("apr")
        now = db.utcnow()
        from datetime import datetime, timedelta, timezone

        expires = (
            datetime.now(timezone.utc) + timedelta(seconds=settings.approval_ttl_seconds)
        ).isoformat(timespec="seconds")
        with db.conn() as c:
            c.execute(
                "INSERT INTO approvals (approval_id, order_id, amount_paise, status, reason,"
                " requested_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                (approval_id, order_id, order["total_paise"], "pending",
                 "payment request from agent", now, expires),
            )
            c.execute(
                "UPDATE orders SET status = 'awaiting_approval', updated_at = ?"
                " WHERE order_id = ?", (now, order_id),
            )
        seq = audit.log(
            actor="agent", plane="trust", action="request_payment",
            payload={"order_id": order_id, "amount_paise": order["total_paise"],
                     "mission_id": mission_id},
            decision="pending",
            detail={"approval_id": approval_id, "expires_at": expires,
                    "checks": [c.as_dict() for c in decision.checks]},
        )
        return {
            "ok": True,
            "audit_seq": seq,
            "status": "pending_approval",
            "approval_id": approval_id,
            "approval_url": f"{_BASE}/dashboard",
            "expires_at": expires,
            "note": "A human must approve. Poll get_order_status until decided.",
        }

    def decide_approval(self, approval_id: str, approved: bool, approver: str = "human") -> dict:
        """HUMAN-ONLY operation. Not exposed as an MCP tool — the agent cannot
        call this. Surfaced via the HTTP dashboard / API for a real person."""
        with db.conn() as c:
            apr = c.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if apr is None:
                return {"ok": False, "error": "approval_not_found"}
            if apr["status"] != "pending":
                return {"ok": False, "error": f"approval_already_{apr['status']}"}
            if db.utcnow() >= apr["expires_at"]:
                c.execute("UPDATE approvals SET status = 'expired' WHERE approval_id = ?",
                          (approval_id,))
                c.execute("UPDATE orders SET status = 'created', updated_at = ? WHERE order_id = ?",
                          (db.utcnow(), apr["order_id"]))
                audit.log(
                    actor="system", plane="trust", action="approval.expired",
                    payload={"approval_id": approval_id}, decision="blocked",
                    detail={"reason": "TTL elapsed"},
                )
                return {"ok": False, "error": "approval_expired"}

            order = c.execute("SELECT * FROM orders WHERE order_id = ?", (apr["order_id"],)).fetchone()

        if not approved:
            now = db.utcnow()
            with db.conn() as c:
                c.execute(
                    "UPDATE approvals SET status = 'rejected', decided_at = ?, decided_by = ?"
                    " WHERE approval_id = ?", (now, approver, approval_id),
                )
                # Graceful degradation: order returns to 'created' — the agent
                # may re-request with changed parameters, nothing is lost.
                c.execute(
                    "UPDATE orders SET status = 'created', updated_at = ? WHERE order_id = ?",
                    (now, apr["order_id"]),
                )
            audit.log(
                actor="human", plane="trust", action="approval.decision",
                payload={"approval_id": approval_id, "order_id": apr["order_id"],
                         "amount_paise": apr["amount_paise"]},
                decision="blocked", detail={"decision": "rejected", "by": approver},
            )
            return {"ok": True, "status": "rejected",
                    "note": "order returned to 'created'; agent may re-request or amend"}

        # Approved by a human — NOW (and only now) do we touch the gateway.
        result = get_gateway().create_payment(dict(order))
        now = db.utcnow()
        with db.conn() as c:
            c.execute(
                "UPDATE approvals SET status = 'approved', decided_at = ?, decided_by = ?"
                " WHERE approval_id = ?", (now, approver, approval_id),
            )
            c.execute(
                "UPDATE orders SET status = ?, updated_at = ?, razorpay_order_id = ?,"
                " razorpay_payment_id = ? WHERE order_id = ?",
                (result.status, now, result.ref,
                 result.ref if result.status == "paid" else None, apr["order_id"]),
            )
            if result.status == "paid":
                if order["mission_id"]:
                    c.execute(
                        "UPDATE missions SET spent_paise = spent_paise + ? WHERE mission_id = ?",
                        (apr["amount_paise"], order["mission_id"]),
                    )
                c.execute(
                    "UPDATE buyers SET spent_today_paise = spent_today_paise + ?"
                    " WHERE buyer_id = 'buyer_demo'", (apr["amount_paise"],),
                )
        audit.log(
            actor="human", plane="trust", action="approval.decision",
            payload={"approval_id": approval_id, "order_id": apr["order_id"],
                     "amount_paise": apr["amount_paise"]},
            decision="ok",
            detail={"decision": "approved", "by": approver, "provider": result.provider,
                    "ref": result.ref},
        )
        return {
            "ok": True,
            "status": result.status,  # "paid" (mock) or "pending_payment" (razorpay test)
            "order": self.get_order(apr["order_id"])["order"],
            "checkout_url": result.checkout_url,
        }

    # ---------------------------------------------------------------- status
    def get_order(self, order_id: str) -> dict:
        with db.conn() as c:
            order = c.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            events = []
            if order:
                # Timeline = every audited event for this order OR the quote it
                # came from (the quote predates the order id).
                events = c.execute(
                    "SELECT * FROM audit_log WHERE payload_json LIKE ? OR payload_json LIKE ?"
                    " ORDER BY seq",
                    (f"%{order_id}%", f"%{order['quote_id']}%"),
                ).fetchall()
        if order is None:
            return {"ok": False, "error": "order_not_found"}
        d = self._order_dict(order)
        d["timeline"] = [
            {"seq": e["seq"], "ts": e["ts"], "actor": e["actor"], "plane": e["plane"],
             "action": e["action"], "decision": e["decision"]}
            for e in events
        ]
        return {"ok": True, "order": d}

    # --------------------------------------------------------------- mission
    def open_mission(self, brief: str, budget_rupees: int | None = None,
                     buyer_id: str = "buyer_demo") -> dict:
        if not brief or len(brief) > 500:
            return {"ok": False, "error": "brief required (1..500 chars)"}
        mission_id = _new_id("msn")
        budget_paise = int(budget_rupees * 100) if budget_rupees else None
        with db.conn() as c:
            c.execute(
                "INSERT INTO missions (mission_id, buyer_id, brief, budget_paise, status, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (mission_id, buyer_id, brief, budget_paise, "active", db.utcnow()),
            )
        seq = audit.log(
            actor="agent", plane="trust", action="open_mission",
            payload={"mission_id": mission_id, "brief": brief},
            decision="ok", detail={"budget_paise": budget_paise},
        )
        return {"ok": True, "audit_seq": seq, "mission_id": mission_id,
                "budget_paise": budget_paise}

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict:
        with db.conn() as c:
            products = c.execute("SELECT COUNT(*) AS n FROM products WHERE active=1").fetchone()["n"]
            orders = c.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
            paid = c.execute("SELECT COUNT(*) AS n FROM orders WHERE status='paid'").fetchone()["n"]
            gmv = c.execute(
                "SELECT COALESCE(SUM(total_paise),0) AS s FROM orders WHERE status='paid'"
            ).fetchone()["s"]
            blocked = c.execute(
                "SELECT COUNT(*) AS n FROM audit_log WHERE decision='blocked'").fetchone()["n"]
        return {
            "products": products, "orders": orders, "paid_orders": paid,
            "gmv_paise": gmv, "gmv_rupees": round(gmv / 100, 2),
            "blocked_actions": blocked, "audit_events": audit.count(),
        }

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _order_dict(row) -> dict:
        return {
            "order_id": row["order_id"],
            "status": row["status"],
            "total_paise": row["total_paise"],
            "total_rupees": round(row["total_paise"] / 100, 2),
            "items": json.loads(row["items_json"]),
            "mission_id": row["mission_id"],
            "razorpay_order_id": row["razorpay_order_id"],
            "razorpay_payment_id": row["razorpay_payment_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
