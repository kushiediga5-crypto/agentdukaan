"""Buyer agent runtime — end-to-end missions with the scripted brain.

The scripted planner must behave like a trustworthy agent:
  - respect exclusions parsed from the brief ("don't repeat mango")
  - stay inside the budget (and refuse to spend when nothing fits)
  - complete the full gated loop: mission → search → quote → order →
    pending approval → (human decides) → paid → final report
  - degrade gracefully on rejection
No LLM, no network. This is the CI-verifiable agent.
"""

import asyncio
import json

from agentdukaan.agent.base import FinalAnswer, ToolCall, Wait
from agentdukaan.agent.clients import DirectToolClient
from agentdukaan.agent.runtime import AgentRuntime
from agentdukaan.agent.scripted import (
    ScriptedBrain,
    parse_budget,
    parse_exclusions,
    parse_query,
)
from agentdukaan.service import Commerce


def _run_mission(
    mission: str, decision: bool | None = True, poll: float = 0.01
) -> list[dict]:
    """decision: True = human approves, False = human rejects, None = nobody decides."""

    async def drive() -> list[dict]:
        events = []
        brain = ScriptedBrain(poll_seconds=poll, max_polls=10)
        runtime = AgentRuntime(brain, DirectToolClient())
        async for ev in runtime.run(mission):
            events.append(ev)
            if ev["type"] == "approval_pending" and decision is not None:
                # The test plays the human — the ONLY path to money.
                Commerce().decide_approval(ev["approval_id"], decision, "test-human")
        return events

    return asyncio.run(drive())


# --------------------------------------------------------------- brief parsing
def test_parse_budget_variants():
    assert parse_budget("Budget ₹4,000 for supplements") == 4000
    assert parse_budget("under 2500 rupees") == 2500
    assert parse_budget("max of 999") == 999
    assert parse_budget("no money mentioned") is None


def test_parse_exclusions_and_query():
    brief = (
        "Restock my gym stack. Budget ₹4,000. Whey isolate — and don't repeat "
        "last month's mango flavour. Pincode 600001"
    )
    assert "mango" in parse_exclusions(brief)
    # "mango" must NOT leak into the search query (it's an exclusion, not intent)
    q = parse_query(brief)
    assert "mango" not in q.split()
    assert "whey" in q and "isolate" in q


# ------------------------------------------------------------------- missions
def test_mission_full_loop_respects_exclusion_and_budget():
    events = _run_mission(
        "Restock my gym stack. Budget ₹4,000. Whey isolate — and don't repeat "
        "last month's mango flavour. Pincode 600001"
    )
    result = [e for e in events if e["type"] == "result"][-1]
    assert result["success"] is True, result["text"]

    # The agent never touched the excluded product.
    calls = [e for e in events if e["type"] == "tool_call"]
    quoted = [e for e in calls if e["tool"] == "quote_order"][-1]
    ids = [i["product_id"] for i in quoted["args"]["items"]]
    assert "prd_whey_iso_mango" not in ids
    assert "prd_whey_iso_cocoa" in ids  # next-best rated isolate

    # Budget respected on the paid order.
    order_events = [
        e for e in events if e["type"] == "tool_result" and e["tool"] == "create_order"
    ]
    assert order_events[-1]["summary"]["order_id"]
    order = Commerce().get_order(order_events[-1]["summary"]["order_id"])["order"]
    assert order["status"] == "paid"
    assert order["total_paise"] <= 400_000

    # The mission was registered with the trust plane.
    missions = [e for e in calls if e["tool"] == "open_mission"]
    assert missions and missions[0]["args"].get("budget_rupees") == 4000


def test_mission_budget_too_small_refuses_to_spend():
    events = _run_mission("Buy whey isolate. Budget ₹500. Pincode 600001")
    result = [e for e in events if e["type"] == "result"][-1]
    assert result["success"] is False
    assert "budget" in result["text"].lower()
    # And critically: no order was ever created.
    assert not [
        e for e in events if e["type"] == "tool_call" and e["tool"] == "create_order"
    ]


def test_mission_human_rejection_degrades_gracefully():
    events = _run_mission("Get me creatine. Budget ₹1,500.", decision=False)
    result = [e for e in events if e["type"] == "result"][-1]
    assert result["success"] is False
    assert "reject" in result["text"].lower()
    assert (
        "not retry" in result["text"].lower()
        or "will not retry" in result["text"].lower()
    )


def test_mission_completes_when_human_decides_late():
    """Regression: the agent must notice a decision made AFTER it started
    polling — the first version cached one status read and waited forever."""

    async def drive() -> list[dict]:
        events: list[dict] = []
        brain = ScriptedBrain(poll_seconds=0.01, max_polls=25)
        runtime = AgentRuntime(brain, DirectToolClient())
        approval_id = None
        decided = False
        async for ev in runtime.run("Get me creatine. Budget ₹1,500."):
            events.append(ev)
            if ev["type"] == "approval_pending":
                approval_id = ev["approval_id"]
            polls_done = sum(
                1
                for e in events
                if e["type"] == "tool_result" and e["tool"] == "get_order_status"
            )
            # The human takes their time: approve only after 3 polls happened.
            if approval_id and not decided and polls_done >= 3:
                Commerce().decide_approval(approval_id, True, "slow-human")
                decided = True
        return events

    events = asyncio.run(drive())
    result = [e for e in events if e["type"] == "result"][-1]
    assert result["success"] is True, result["text"]
    polls = sum(
        1
        for e in events
        if e["type"] == "tool_call" and e["tool"] == "get_order_status"
    )
    assert polls >= 3  # it genuinely polled while waiting


def test_runtime_step_budget_stops_a_looping_brain():
    """A broken brain must not spin forever or reach money."""

    class LoopingBrain:
        name = "broken"

        async def next_action(self, ctx):
            return ToolCall("get_store_manifest", {})

    async def drive():
        events = []
        rt = AgentRuntime(LoopingBrain(), DirectToolClient(), max_steps=5)
        async for ev in rt.run("loop forever"):
            events.append(ev)
        return events

    events = asyncio.run(drive())
    result = [e for e in events if e["type"] == "result"][-1]
    assert result["success"] is False and "step budget" in result["text"]


def test_brain_factory_defaults_to_scripted_without_keys():
    from agentdukaan.agent.llm import get_brain

    brain = get_brain("auto")
    assert brain.name == "scripted-planner"


def test_events_are_json_serializable():
    events = _run_mission("Buy a shaker. Budget ₹500. Pincode 560001")
    for ev in events:
        json.dumps(ev)  # must never raise — these go straight to SSE
