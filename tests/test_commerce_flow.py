"""End-to-end happy path + idempotency guarantees, via the service layer.

This is the same code path the MCP tools expose — proving the agent surface
cannot create a double-charge even under replayed calls.
"""

from agentdukaan.service import Commerce


def test_full_purchase_loop():
    c = Commerce()

    manifest = c.store_manifest()
    assert manifest["currency"] == "INR"

    found = c.search_products(query="whey isolate")
    assert found["ok"] and found["count"] >= 2
    pid = found["results"][0]["product_id"]

    quote = c.quote_order(
        [{"product_id": pid, "qty": 1}, {"product_id": "prd_shaker", "qty": 1}],
        "600001",
    )
    assert quote["ok"], quote
    assert quote["totals"]["total_paise"] == (
        quote["totals"]["subtotal_paise"] + quote["totals"]["shipping_paise"]
    )

    order = c.create_order(quote_id=quote["quote_id"], idempotency_key="happy-1")
    assert order["ok"] and order["order"]["status"] == "created"
    oid = order["order"]["order_id"]

    pay = c.request_payment(order_id=oid)
    assert pay["ok"] and pay["status"] == "pending_approval"
    approval_id = pay["approval_id"]

    # Idempotent re-request: same pending approval, never a second one.
    pay2 = c.request_payment(order_id=oid)
    assert pay2["status"] == "pending_approval"
    assert pay2["approval_id"] == approval_id

    decision = c.decide_approval(
        approval_id=approval_id, approved=True, approver="test-human"
    )
    assert decision["ok"] and decision["status"] == "paid"
    assert decision["order"]["razorpay_payment_id"]

    final = c.get_order(oid)
    assert final["order"]["status"] == "paid"
    # The timeline must tell the full story: quote → order → payment → approval.
    actions = {e["action"] for e in final["order"]["timeline"]}
    assert {
        "quote_order",
        "create_order",
        "request_payment",
        "approval.decision",
    } <= actions


def test_create_order_idempotent_replay():
    c = Commerce()
    quote = c.quote_order([{"product_id": "prd_rope", "qty": 1}], "560001")
    first = c.create_order(quote_id=quote["quote_id"], idempotency_key="replay-x")
    assert first["ok"]
    second = c.create_order(quote_id=quote["quote_id"], idempotency_key="replay-x")
    assert second["ok"] and second["idempotent_replay"] is True
    assert second["order"]["order_id"] == first["order"]["order_id"]

    # And stock was decremented exactly once.
    from agentdukaan import catalog

    assert catalog.get_raw("prd_rope")["stock"] == 89  # 90 seeded - 1


def test_audit_ledger_is_append_only():
    import sqlite3

    import pytest

    from agentdukaan import db

    with pytest.raises(sqlite3.DatabaseError):  # trigger fires on DELETE
        with db.conn() as c:
            c.execute("DELETE FROM audit_log")
    with pytest.raises(sqlite3.DatabaseError):  # trigger fires on UPDATE
        with db.conn() as c:
            c.execute("UPDATE audit_log SET decision = 'ok'")
