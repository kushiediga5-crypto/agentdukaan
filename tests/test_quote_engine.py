"""Deterministic pricing engine — GST math, shipping zones, totals."""
import pytest

from agentdukaan import quote_engine as qe


def test_gst_split_inclusive_math():
    # ₹2,899 inclusive @ 18% => taxable ₹2,456.78, GST ₹442.22 (sums exactly)
    taxable, gst = qe._gst_split(289_900, 1800)
    assert taxable + gst == 289_900
    assert taxable == 245_678
    assert gst == 44_222


def test_shipping_free_above_threshold():
    assert qe.shipping_fee(99_900, "600001") == 0


def test_shipping_metro_vs_rest():
    assert qe.shipping_fee(50_000, "600001") == qe.SHIPPING_METRO_PAISE      # Chennai
    assert qe.shipping_fee(50_000, "627001") == qe.SHIPPING_REST_PAISE       # Tirunelveli


def test_pincode_validation():
    assert qe.validate_pincode("600001") == "600001"
    for bad in ("", "60001", "6000010", "ABC123", "060001"):
        with pytest.raises(qe.QuoteError):
            qe.validate_pincode(bad)


def test_totals_sum_exactly():
    lines = [
        qe.QuoteLine("a", "A", 2, 289_900, 579_800, 491_356, 88_444, 1800),
        qe.QuoteLine("b", "B", 1, 39_900, 39_900, 33_814, 6_086, 1800),
    ]
    t = qe.totals(lines, "600001")
    assert t["subtotal_paise"] == 619_700
    assert t["total_paise"] == t["subtotal_paise"] + t["shipping_paise"]
    assert t["gst_paise"] == sum(l.gst_paise for l in lines)
    assert t["zone"] == "metro"
