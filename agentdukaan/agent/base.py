"""Agent primitives: actions, context, and the Brain contract.

A Brain is a pure decision function: (context so far) -> next action.
It holds no I/O authority — the runtime executes everything, so even a
misbehaving brain cannot bypass the trust plane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    name: str
    args: dict
    commentary: str = ""


@dataclass
class Wait:
    seconds: float
    reason: str = ""


@dataclass
class FinalAnswer:
    text: str
    success: bool | None = True  # None = paused/undecided (e.g. approval still pending)


Action = ToolCall | Wait | FinalAnswer


@dataclass
class AgentContext:
    """Everything a brain is allowed to know: the mission and the tool history."""

    mission: str
    history: list[dict] = field(default_factory=list)  # {"tool","args","result"}
    steps: int = 0

    def last(self, tool_name: str) -> dict | None:
        """Result of the most recent call to a tool (or None)."""
        for entry in reversed(self.history):
            if entry["tool"] == tool_name:
                return entry["result"]
        return None


@runtime_checkable
class Brain(Protocol):
    name: str

    async def next_action(self, ctx: AgentContext) -> Action: ...


def action_summary(action: Action) -> dict:
    """Compact, JSON-safe description of an action (for events / audit)."""
    if isinstance(action, ToolCall):
        return {
            "kind": "tool_call",
            "tool": action.name,
            "args": action.args,
            "commentary": action.commentary,
        }
    if isinstance(action, Wait):
        return {"kind": "wait", "seconds": action.seconds, "reason": action.reason}
    return {"kind": "final", "success": action.success, "text": action.text}
