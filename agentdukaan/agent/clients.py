"""Tool clients: where the agent's tools physically live.

  - MCPToolClient  : the real path — the buyer agent transacts with the store's
                     MCP commerce server over streamable HTTP, exactly how
                     ACP/UCP-style commerce is deployed in the wild.
  - DirectToolClient: in-process fallback (tests, offline demos, resilience).
                     Same Commerce service, same audit trail — only the wire
                     changes.

connect_tools() prefers MCP and falls back to direct with an honest note,
so the demo never dies just because a second process didn't boot.
"""

from __future__ import annotations

import asyncio
import json

from ..config import settings
from ..service import Commerce


class MCPToolClient:
    transport = "mcp"

    def __init__(self, url: str | None = None, timeout: float = 4.0):
        self.url = url or settings.agent_mcp_url
        self.timeout = timeout
        self._cm = None
        self._session_cm = None
        self.session = None

    async def __aenter__(self) -> "MCPToolClient":
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        self._cm = streamable_http_client(self.url)
        read, write = await self._cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self.session = await self._session_cm.__aenter__()
        await asyncio.wait_for(self.session.initialize(), timeout=self.timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(*exc)
        if self._cm is not None:
            await self._cm.__aexit__(*exc)
        self.session = None

    async def call(self, name: str, args: dict) -> dict:
        if self.session is None:
            return {"ok": False, "error": "mcp_session_not_open"}
        r = await self.session.call_tool(name, args)
        text = getattr(r.content[0], "text", None) if r.content else None
        if text is None:
            return {"ok": False, "error": "empty_tool_output"}
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": "unparseable_tool_output"}

    async def close(self) -> None:
        """Best-effort teardown. SSE clients disconnect mid-mission all the time
        (closed tab, finished stream) — cleanup must never raise through a
        cancelled task group."""
        try:
            await self.__aexit__(None, None, None)
        except BaseException:
            pass


class DirectToolClient:
    """Calls the same Commerce service in-process. For tests and resilience."""

    transport = "direct"

    def __init__(self):
        self.commerce = Commerce()

    async def call(self, name: str, args: dict) -> dict:
        c = self.commerce
        try:
            if name == "get_store_manifest":
                return c.store_manifest()
            if name == "search_products":
                return c.search_products(**args)
            if name == "get_product":
                return c.get_product(args["product_id"])
            if name == "quote_order":
                return c.quote_order(args["items"], args["pincode"])
            if name == "create_order":
                return c.create_order(**args)
            if name == "request_payment":
                return c.request_payment(**args)
            if name == "get_order_status":
                return c.get_order(args["order_id"])
            if name == "open_mission":
                return c.open_mission(**args)
            return {"ok": False, "error": f"unknown_tool:{name}"}
        except Exception as exc:
            return {"ok": False, "error": f"direct_client_error: {exc}"}

    async def close(self) -> None:
        return None


async def connect_tools(mcp_url: str | None = None) -> tuple:
    """Prefer the MCP surface; degrade to in-process tools if it's down.

    Returns (client, fallback_note_or_None).
    """
    try:
        client = MCPToolClient(mcp_url)
        await client.__aenter__()
        return client, None
    except Exception as exc:
        return DirectToolClient(), (
            f"⚠️ MCP surface unreachable ({exc.__class__.__name__}) — running on the "
            "in-process tool client. Same service, same audit trail; only the wire changed."
        )
