"""Deterministic quote engine — pricing, GST, shipping.

This module is the anti-LLM zone. No model calls, no heuristics that can drift.
The agent can *ask* for a quote; it can never *compute* a total itself.

GST math (India, GST-inclusive unit prices):
    line_total   = unit_price_paise * qty
    line_taxable = round(line_total * 10000 / (10000 + gst_rate_bps))
    line_gst     = line_total - line_taxable
"""

from __future__ import annotations

import re
from dataclasses import dataclass

METRO_PREFIXES = {
    "110",
    "400",
    "560",
    "600",
    "700",
    "500",
}  # Delhi, Mumbai, Blr, Chennai, Kolkata, Hyd
FREE_SHIPPING_THRESHOLD_PAISE = 99_900  # ₹999
SHIPPING_METRO_PAISE = 4_900  # ₹49
SHIPPING_REST_PAISE = 7_900  # ₹79
MAX_QTY_PER_LINE = 10

_PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")


class QuoteError(ValueError):
    """Raised for invalid quote inputs. Always safe to show to the agent."""


@dataclass
class QuoteLine:
    product_id: str
    name: str
    qty: int
    unit_price_paise: int
    line_total_paise: int
    taxable_paise: int
    gst_paise: int
    gst_rate_bps: int


def validate_pincode(pincode: str) -> str:
    pincode = (pincode or "").strip()
    if not _PINCODE_RE.match(pincode):
        raise QuoteError(f"Invalid Indian pincode: {pincode!r} (expected 6 digits)")
    return pincode


def shipping_zone(pincode: str) -> str:
    return "metro" if pincode[:3] in METRO_PREFIXES else "rest"


def shipping_fee(subtotal_paise: int, pincode: str) -> int:
    if subtotal_paise >= FREE_SHIPPING_THRESHOLD_PAISE:
        return 0
    return (
        SHIPPING_METRO_PAISE
        if shipping_zone(pincode) == "metro"
        else SHIPPING_REST_PAISE
    )


def _gst_split(line_total_paise: int, gst_rate_bps: int) -> tuple[int, int]:
    taxable = round(line_total_paise * 10_000 / (10_000 + gst_rate_bps))
    return taxable, line_total_paise - taxable


def build_lines(items: list[dict], catalog_lookup) -> list[QuoteLine]:
    """catalog_lookup: callable(product_id) -> raw product dict (or None)."""
    if not items:
        raise QuoteError("items must not be empty")
    if len(items) > 20:
        raise QuoteError("too many line items (max 20)")

    lines: list[QuoteLine] = []
    seen: set[str] = set()
    for item in items:
        pid = str(item.get("product_id", "")).strip()
        qty = item.get("qty", 1)
        if not pid:
            raise QuoteError("each item needs a product_id")
        if (
            not isinstance(qty, int)
            or isinstance(qty, bool)
            or qty < 1
            or qty > MAX_QTY_PER_LINE
        ):
            raise QuoteError(f"qty for {pid} must be an integer 1..{MAX_QTY_PER_LINE}")
        if pid in seen:
            raise QuoteError(f"duplicate line item for {pid}; merge quantities instead")
        seen.add(pid)
        product = catalog_lookup(pid)
        if product is None:
            raise QuoteError(f"unknown product_id: {pid}")
        if qty > product["stock"]:
            raise QuoteError(
                f"insufficient stock for {pid}: {product['stock']} available, {qty} requested"
            )
        line_total = product["unit_price_paise"] * qty
        taxable, gst = _gst_split(line_total, product["gst_rate_bps"])
        lines.append(
            QuoteLine(
                product_id=pid,
                name=product["name"],
                qty=qty,
                unit_price_paise=product["unit_price_paise"],
                line_total_paise=line_total,
                taxable_paise=taxable,
                gst_paise=gst,
                gst_rate_bps=product["gst_rate_bps"],
            )
        )
    return lines


def totals(lines: list[QuoteLine], pincode: str) -> dict:
    subtotal = sum(l.line_total_paise for l in lines)
    taxable = sum(l.taxable_paise for l in lines)
    gst = sum(l.gst_paise for l in lines)
    ship = shipping_fee(subtotal, pincode)
    return {
        "subtotal_paise": subtotal,
        "taxable_paise": taxable,
        "gst_paise": gst,
        "shipping_paise": ship,
        "total_paise": subtotal + ship,
        "zone": shipping_zone(pincode),
    }


def line_dicts(lines: list[QuoteLine]) -> list[dict]:
    return [
        {
            "product_id": l.product_id,
            "name": l.name,
            "qty": l.qty,
            "unit_price_paise": l.unit_price_paise,
            "unit_price_rupees": round(l.unit_price_paise / 100, 2),
            "line_total_paise": l.line_total_paise,
            "line_total_rupees": round(l.line_total_paise / 100, 2),
            "gst_rate_percent": l.gst_rate_bps / 100,
            "gst_paise": l.gst_paise,
        }
        for l in lines
    ]
