"""The trust plane under attack: every guard must block, and every block must
land in the audit ledger with a reason. These tests ARE the track's
"bounded and gated" evidence."""
from agentdukaan import catalog, db
from agentdukaan.service import Commerce


def test_price_drift_blocked():
    """Quote at a fair price, catalog price changes before order => blocked."""
    c = Commerce()
    quote = c.quote_order([{"product_id": "prd_eaa_watermelon", "qty": 1}], "600001")
    assert quote["ok"]
    with db.conn() as c2:
        c2.execute("UPDATE products SET unit_price_paise = 1 WHERE product_id = 'prd_eaa_watermelon'")
    result = c.create_order(quote_id=quote["quote_id"], idempotency_key="drift-1")
    assert not result["ok"] and result["error"] == "PRICE_DRIFT"
    with db.conn() as c2:  # restore
        c2.execute("UPDATE products SET unit_price_paise = 109_900 WHERE product_id = 'prd_eaa_watermelon'")


def test_mission_budget_blocked():
    c = Commerce()
    mission = c.open_mission(brief="buy a rope", budget_rupees=200)
    assert mission["ok"]
    quote = c.quote_order([{"product_id": "prd_rope", "qty": 1}], "600001")  # ₹299 + ship
    order = c.create_order(quote_id=quote["quote_id"], idempotency_key="budget-1")
    pay = c.request_payment(order_id=order["order"]["order_id"], mission_id=mission["mission_id"])
    assert not pay["ok"]
    assert pay["block_reason"] == "MISSION_BUDGET_EXCEEDED"
    failed = [x for x in pay["checks"] if not x["passed"]]
    assert any(x["check"] == "mission_budget" for x in failed)


def test_per_txn_cap_blocked():
    c = Commerce()
    # 4 × ₹2,899 = ₹11,596 > ₹10,000 cap
    quote = c.quote_order([{"product_id": "prd_whey_iso_cocoa", "qty": 4}], "600001")
    assert quote["totals"]["total_paise"] == 1_159_600
    order = c.create_order(quote_id=quote["quote_id"], idempotency_key="cap-1")
    pay = c.request_payment(order_id=order["order"]["order_id"])
    assert not pay["ok"] and pay["block_reason"] == "PER_TXN_CAP_EXCEEDED"
    assert pay["next_step"]  # the agent is told to escalate, not retry


def test_quote_expiry_blocked():
    c = Commerce()
    quote = c.quote_order([{"product_id": "prd_rope", "qty": 1}], "600001")
    with db.conn() as c2:
        c2.execute(
            "UPDATE quotes SET expires_at = '2000-01-01T00:00:00+00:00' WHERE quote_id = ?",
            (quote["quote_id"],),
        )
    result = c.create_order(quote_id=quote["quote_id"], idempotency_key="expiry-1")
    assert not result["ok"] and result["error"] == "QUOTE_EXPIRED"


def test_rejection_recovers_gracefully():
    """Human rejects => order returns to 'created' => agent may retry => paid."""
    c = Commerce()
    quote = c.quote_order([{"product_id": "prd_omega3", "qty": 1}], "600001")
    order = c.create_order(quote_id=quote["quote_id"], idempotency_key="reject-1")
    oid = order["order"]["order_id"]
    pay = c.request_payment(order_id=oid)
    reject = c.decide_approval(approval_id=pay["approval_id"], approved=False, approver="test-human")
    assert reject["ok"] and reject["status"] == "rejected"
    assert c.get_order(oid)["order"]["status"] == "created"

    pay2 = c.request_payment(order_id=oid)
    approve = c.decide_approval(approval_id=pay2["approval_id"], approved=True, approver="test-human")
    assert approve["ok"] and approve["status"] == "paid"


def test_out_of_stock_blocked_and_stock_is_atomic():
    c = Commerce()
    with db.conn() as c2:
        c2.execute("UPDATE products SET stock = 1 WHERE product_id = 'prd_rope'")

    # Quote asks for more than exists => refused at quote time.
    over = c.quote_order([{"product_id": "prd_rope", "qty": 2}], "600001")
    assert not over["ok"]

    # The last unit orders fine, and stock hits exactly zero.
    quote = c.quote_order([{"product_id": "prd_rope", "qty": 1}], "600001")
    order = c.create_order(quote_id=quote["quote_id"], idempotency_key="stock-1")
    assert order["ok"]
    assert catalog.get_raw("prd_rope")["stock"] == 0

    # And now the SKU is unbuyable.
    drained = c.quote_order([{"product_id": "prd_rope", "qty": 1}], "600001")
    assert not drained["ok"]

    with db.conn() as c2:  # restore for other tests
        c2.execute("UPDATE products SET stock = 90 WHERE product_id = 'prd_rope'")


def test_blocked_actions_reach_audit_ledger():
    from agentdukaan import audit as audit_mod

    events = audit_mod.recent(200)
    blocked = [e for e in events if e["decision"] == "blocked"]
    assert blocked, "every block in this file must be auditable"
    assert all(e["detail_json"] for e in blocked)  # with reasons
