"""Smoke test the LIVE MCP server exactly like a buyer agent would.

Requires both servers running:
    python -m agentdukaan.server.mcp_server     # :8001
    python -m agentdukaan.server.http_app       # :8000

Then:  python scripts/mcp_smoke_test.py
"""

import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = "http://127.0.0.1:8001/mcp"
API = "http://127.0.0.1:8000"


def show(label: str, obj) -> None:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=str)
    print(f"\n=== {label} ===\n{text[:1200]}")


async def main() -> None:
    async with streamable_http_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", ", ".join(t.name for t in tools.tools))

            r = await session.call_tool("get_store_manifest", {})
            manifest = json.loads(r.content[0].text)
            show("manifest (trust limits)", manifest["trust"])

            r = await session.call_tool("search_products", {"query": "whey isolate"})
            search = json.loads(r.content[0].text)
            show(
                "search",
                {"count": search["count"], "top": search["results"][0]["name"]},
            )
            pid = search["results"][0]["product_id"]

            r = await session.call_tool(
                "quote_order",
                {
                    "items": [
                        {"product_id": pid, "qty": 1},
                        {"product_id": "prd_shaker", "qty": 1},
                    ],
                    "pincode": "600001",
                },
            )
            quote = json.loads(r.content[0].text)
            show("quote", quote["totals"])

            r = await session.call_tool(
                "create_order",
                {
                    "quote_id": quote["quote_id"],
                    "idempotency_key": f"smoke-{quote['quote_id']}",
                },
            )
            order = json.loads(r.content[0].text)
            show(
                "order",
                {
                    "order_id": order["order"]["order_id"],
                    "total": order["order"]["total_rupees"],
                    "status": order["order"]["status"],
                },
            )
            oid = order["order"]["order_id"]

            r = await session.call_tool("request_payment", {"order_id": oid})
            pay = json.loads(r.content[0].text)
            show(
                "payment request (agent can only get PENDING)",
                {
                    "status": pay["status"],
                    "approval_id": pay.get("approval_id"),
                    "note": pay.get("note"),
                },
            )

            if pay.get("approval_id"):
                resp = httpx.post(
                    f"{API}/api/approvals/{pay['approval_id']}",
                    params={"approved": "true", "approver": "smoke-test-human"},
                    timeout=10,
                )
                show(
                    "HUMAN approves (the only path to money)",
                    {
                        "status": resp.json().get("status"),
                        "payment_ref": (resp.json().get("order") or {}).get(
                            "razorpay_payment_id"
                        ),
                    },
                )

            r = await session.call_tool("get_order_status", {"order_id": oid})
            final = json.loads(r.content[0].text)
            show(
                "final",
                {
                    "status": final["order"]["status"],
                    "audited_events": len(final["order"]["timeline"]),
                },
            )
            assert final["order"]["status"] == "paid", "loop did not complete!"
            print(
                "\n🎉 FULL AGENT PURCHASE LOOP COMPLETE — agent bought, human gated, ledger saw all."
            )


if __name__ == "__main__":
    asyncio.run(main())
