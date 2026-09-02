"""Run a buyer-agent mission from the terminal.

Usage:
    python -m agentdukaan.agent.run "Restock my gym stack. Budget ₹4,000. Whey isolate, don't repeat last month's mango. Pincode 600001"
    python -m agentdukaan.agent.run "..." --brain scripted

Payment completes when a human approves — open the dashboard (/dashboard)
when the approval event appears.
"""

from __future__ import annotations

import argparse
import asyncio

from .clients import connect_tools
from .runtime import AgentRuntime


async def _main() -> None:
    ap = argparse.ArgumentParser(description="AgentDukaan buyer agent")
    ap.add_argument("mission")
    ap.add_argument("--brain", default="auto", choices=["auto", "scripted", "llm"])
    args = ap.parse_args()

    from .llm import get_brain

    client, note = await connect_tools()
    try:
        if note:
            print(note)
        runtime = AgentRuntime(get_brain(args.brain), client)
        async for event in runtime.run(args.mission):
            et = event["type"]
            if et == "thought":
                print(f"🧠 {event['text']}")
            elif et == "tool_call":
                print(f"🛠  {event['tool']}({event['args']})")
            elif et == "tool_result":
                print(f"    ↳ {event['summary']}")
            elif et == "approval_pending":
                print(
                    f"⚡ APPROVAL REQUIRED — {event['approval_id']} → decide at /dashboard"
                )
            elif et == "result":
                icon = (
                    "✅"
                    if event.get("success")
                    else ("⏸" if event.get("success") is None else "🛑")
                )
                print(f"{icon} {event['text']}")
            elif et == "mission":
                print(
                    f"🚀 mission started (brain={event['brain']}, transport={event['transport']})"
                )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(_main())
