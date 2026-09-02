"""Payment gateway layer.

Two implementations behind one interface:
  - MockGateway    : fully offline, deterministic. Default (no keys needed).
  - RazorpayGateway: Razorpay TEST MODE via the official SDK. Activates
                     automatically when RAZORPAY_KEY_ID/SECRET are set.

The gateway is only ever invoked AFTER a human approval decision. The agent
has no code path that reaches a gateway directly.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from .. import audit, db
from ..config import settings


@dataclass
class GatewayResult:
    provider: str
    ref: str                 # provider payment reference
    status: str              # "paid" | "pending_payment"
    checkout_url: str | None # human completes payment here (test checkout)


class MockGateway:
    """Deterministic offline gateway. Simulates an instant capture so the whole
    loop is demoable and testable without any external service."""

    provider = "mock-test-mode"

    def create_payment(self, order: dict) -> GatewayResult:
        ref = f"pay_MockTest_{secrets.token_hex(8)}"
        audit.log(
            actor="system", plane="trust", action="gateway.capture",
            payload={"order_id": order["order_id"], "provider": self.provider, "ref": ref},
            decision="ok", detail={"mode": "mock", "note": "instant capture simulation"},
        )
        return GatewayResult(provider=self.provider, ref=ref, status="paid", checkout_url=None)


class RazorpayGateway:
    """Real Razorpay TEST MODE integration. Creates a Razorpay order + payment
    link after approval; a human completes the test checkout to capture."""

    provider = "razorpay-test-mode"

    def __init__(self) -> None:
        import razorpay  # official SDK, only imported when keys exist

        self.client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))

    def create_payment(self, order: dict) -> GatewayResult:
        rp_order = self.client.order.create(
            {
                "amount": order["total_paise"],  # paise, integer — never floats
                "currency": "INR",
                "receipt": order["order_id"],
                "notes": {"mission_id": order.get("mission_id") or "", "source": "agentdukaan-mcp"},
            }
        )
        link = self.client.payment_link.create(
            {
                "amount": order["total_paise"],
                "currency": "INR",
                "accept_partial": False,
                "reference_id": order["order_id"],
                "description": f"AgentDukaan order {order['order_id']}",
                "notes": {"mission_id": order.get("mission_id") or ""},
            }
        )
        audit.log(
            actor="system", plane="trust", action="gateway.create",
            payload={"order_id": order["order_id"], "razorpay_order_id": rp_order["id"]},
            decision="ok", detail={"provider": self.provider},
        )
        return GatewayResult(
            provider=self.provider,
            ref=rp_order["id"],
            status="pending_payment",
            checkout_url=link.get("short_url"),
        )

    def sync_order(self, order: dict) -> GatewayResult | None:
        """Poll Razorpay for the latest payment state on an order."""
        try:
            payments = self.client.order.payments(order["razorpay_order_id"])
        except Exception as exc:  # network/API hiccup — fail closed, keep polling later
            audit.log(
                actor="system", plane="trust", action="gateway.sync_error",
                payload={"order_id": order["order_id"]}, decision="error", detail={"error": str(exc)},
            )
            return None
        for p in payments.get("items", []):
            if p.get("status") in ("captured", "authorized"):
                return GatewayResult(
                    provider=self.provider, ref=p["id"], status="paid", checkout_url=None
                )
        return None


def get_gateway() -> MockGateway | RazorpayGateway:
    if settings.gateway_is_live:
        return RazorpayGateway()
    return MockGateway()
