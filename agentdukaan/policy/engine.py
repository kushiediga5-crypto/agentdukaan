"""Trust plane: the policy engine. Every money action passes through here.

Design invariants:
  1. The LLM/agent NEVER executes a payment. It can only *request* one.
  2. Every check returns a named, explainable result — the audit ledger records
     the full check list for every decision, pass OR fail.
  3. Fail-closed: unknown state => block, never allow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import settings

BLOCK_ORDER_NOT_PAYABLE = "ORDER_NOT_PAYABLE"
BLOCK_PER_TXN_CAP = "PER_TXN_CAP_EXCEEDED"
BLOCK_MISSION_BUDGET = "MISSION_BUDGET_EXCEEDED"
BLOCK_DAILY_BUDGET = "DAILY_BUDGET_EXCEEDED"
BLOCK_UNKNOWN = "POLICY_UNRESOLVABLE"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {"check": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class PolicyDecision:
    approved: bool
    requires_approval: bool
    checks: list[Check] = field(default_factory=list)
    block_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "approved": self.approved,
            "requires_approval": self.requires_approval,
            "block_reason": self.block_reason,
            "checks": [c.as_dict() for c in self.checks],
        }


def evaluate_payment(
    *,
    order_status: str,
    amount_paise: int,
    mission: dict | None,
    buyer: dict | None,
) -> PolicyDecision:
    """Evaluate whether a payment REQUEST may proceed to human approval.

    Note what this does NOT do: it never approves a payment outright when the
    approval threshold demands a human. Approval authority is not ours to give.
    """
    checks: list[Check] = []

    payable = order_status in ("created", "payment_blocked", "rejected")
    checks.append(Check("order_payable", payable, f"order status = {order_status}"))

    cap_ok = amount_paise <= settings.per_txn_cap_paise
    checks.append(
        Check(
            "per_txn_cap",
            cap_ok,
            f"₹{amount_paise/100:,.2f} vs cap ₹{settings.per_txn_cap_paise/100:,.2f}",
        )
    )

    if mission is not None:
        budget = mission.get("budget_paise")
        if budget is not None:
            remaining = budget - mission.get("spent_paise", 0)
            ok = amount_paise <= remaining
            checks.append(
                Check(
                    "mission_budget",
                    ok,
                    f"needs ₹{amount_paise/100:,.2f}, mission has ₹{remaining/100:,.2f} remaining",
                )
            )

    if buyer is not None:
        remaining = buyer["daily_budget_paise"] - buyer.get("spent_today_paise", 0)
        ok = amount_paise <= remaining
        checks.append(
            Check(
                "daily_budget",
                ok,
                f"needs ₹{amount_paise/100:,.2f}, daily headroom ₹{remaining/100:,.2f}",
            )
        )

    approved = all(c.passed for c in checks)
    requires_approval = (
        settings.approval_threshold_paise == 0
        or amount_paise > settings.approval_threshold_paise
    )

    block_reason = None
    if not approved:
        failed = [c for c in checks if not c.passed]
        reason_map = {
            "order_payable": BLOCK_ORDER_NOT_PAYABLE,
            "per_txn_cap": BLOCK_PER_TXN_CAP,
            "mission_budget": BLOCK_MISSION_BUDGET,
            "daily_budget": BLOCK_DAILY_BUDGET,
        }
        block_reason = reason_map.get(failed[0].name, BLOCK_UNKNOWN)

    return PolicyDecision(
        approved=approved,
        requires_approval=requires_approval and approved,
        checks=checks,
        block_reason=block_reason,
    )
