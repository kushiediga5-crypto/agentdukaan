"""AgentRuntime — owns the loop; brains only decide.

Safety properties live HERE, not in the brain:
  - hard step budget (a looping brain cannot spin forever)
  - every tool call is audited to the append-only ledger (buyer plane)
  - approval events are surfaced so a human can decide mid-mission
  - transport errors degrade to readable events, never crashes

Emits JSON-serialisable event dicts — consumed identically by the SSE
endpoint, the CLI runner, and tests.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from .. import audit
from .base import Action, AgentContext, FinalAnswer, ToolCall, Wait, action_summary


def _summarize(tool: str, result: dict) -> dict:
    """Compact view of a tool result for live traces."""
    if not isinstance(result, dict):
        return {"note": str(result)[:200]}
    if tool == "search_products":
        return {
            "count": result.get("count"),
            "top": [r["name"] for r in result.get("results", [])[:3]],
        }
    if tool == "quote_order":
        return (
            {
                "total": (result.get("totals") or {}).get("total_rupees"),
                "quote_id": result.get("quote_id"),
            }
            if result.get("ok")
            else {"error": result.get("error")}
        )
    if tool == "create_order":
        return (
            {
                "order_id": (result.get("order") or {}).get("order_id"),
                "status": (result.get("order") or {}).get("status"),
            }
            if result.get("ok")
            else {"error": result.get("error")}
        )
    if tool == "request_payment":
        return {
            k: result.get(k)
            for k in ("status", "approval_id", "block_reason")
            if k in result
        }
    if tool == "get_order_status":
        return (
            {"status": (result.get("order") or {}).get("status")}
            if result.get("ok")
            else {"error": result.get("error")}
        )
    if tool == "open_mission":
        return (
            {"mission_id": result.get("mission_id")}
            if result.get("ok")
            else {"error": result.get("error")}
        )
    if tool == "get_store_manifest":
        return {"store": result.get("store"), "trust": result.get("trust")}
    if tool == "get_product":
        p = result.get("product") or {}
        return (
            {
                "name": p.get("name"),
                "price": p.get("unit_price_rupees"),
                "stock": p.get("stock"),
            }
            if result.get("ok")
            else {"error": result.get("error")}
        )
    return {"ok": result.get("ok", True)}


class AgentRuntime:
    def __init__(
        self, brain, tools, max_steps: int = 60, max_wait_seconds: float = 5.0
    ):
        self.brain = brain
        self.tools = tools
        self.max_steps = max_steps
        self.max_wait_seconds = max_wait_seconds

    async def run(self, mission: str) -> AsyncIterator[dict]:
        ctx = AgentContext(mission=mission)
        started = time.time()
        transport = getattr(self.tools, "transport", "unknown")
        audit.log(
            actor="agent",
            plane="buyer",
            action="mission.start",
            payload={
                "mission": mission,
                "brain": self.brain.name,
                "transport": transport,
            },
            decision="ok",
        )
        yield {
            "type": "mission",
            "mission": mission,
            "brain": self.brain.name,
            "transport": transport,
        }

        while ctx.steps < self.max_steps:
            try:
                action = await self.brain.next_action(ctx)
            except Exception as exc:  # a broken brain must not take money with it
                audit.log(
                    actor="agent",
                    plane="buyer",
                    action="brain.error",
                    payload={"error": repr(exc)},
                    decision="error",
                )
                yield {
                    "type": "result",
                    "success": False,
                    "text": f"agent brain error: {exc!r} — mission aborted safely",
                }
                return
            ctx.steps += 1

            if isinstance(action, Wait):
                yield {"type": "thought", "text": action.reason}
                await asyncio.sleep(min(action.seconds, self.max_wait_seconds))
                continue

            if isinstance(action, FinalAnswer):
                audit.log(
                    actor="agent",
                    plane="buyer",
                    action="mission.end",
                    payload={
                        "mission": mission,
                        "success": action.success,
                        "elapsed_s": round(time.time() - started, 1),
                    },
                    decision="ok" if action.success else "blocked",
                    detail={"report": action.text},
                )
                yield {"type": "result", "success": action.success, "text": action.text}
                return

            # --- ToolCall -------------------------------------------------
            if action.commentary:
                yield {"type": "thought", "text": action.commentary}
            yield {"type": "tool_call", "tool": action.name, "args": action.args}
            try:
                result = await self.tools.call(action.name, action.args)
            except Exception as exc:
                result = {"ok": False, "error": f"tool_transport_error: {exc}"}
            ctx.history.append(
                {"tool": action.name, "args": action.args, "result": result}
            )
            yield {
                "type": "tool_result",
                "tool": action.name,
                "ok": bool(result.get("ok", True)),
                "summary": _summarize(action.name, result),
            }

            # Surface the human gate the moment it appears.
            if action.name == "request_payment" and result.get("approval_id"):
                order = result.get("order") or {}
                yield {
                    "type": "approval_pending",
                    "approval_id": result["approval_id"],
                    "order_id": order.get("order_id") or result.get("order_id"),
                    "amount_rupees": result.get("amount_rupees"),
                    "expires_at": result.get("expires_at"),
                }

        audit.log(
            actor="agent",
            plane="buyer",
            action="mission.step_budget_exhausted",
            payload={"mission": mission, "steps": ctx.steps},
            decision="blocked",
        )
        yield {
            "type": "result",
            "success": False,
            "text": f"step budget ({self.max_steps}) exhausted — stopped for safety",
        }
