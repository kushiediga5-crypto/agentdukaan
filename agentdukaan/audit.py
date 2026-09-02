"""Append-only audit ledger — the single source of truth for "what happened and why".

Every plane writes here: tool calls, policy decisions, approvals, gateway events.
The DB triggers in db.py make UPDATE/DELETE physically impossible.
"""

from __future__ import annotations

import json

from . import db


def log(
    actor: str,
    plane: str,
    action: str,
    payload: dict,
    decision: str,
    detail: dict | None = None,
) -> int:
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO audit_log (ts, actor, plane, action, payload_json, decision, detail_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                db.utcnow(),
                actor,
                plane,
                action,
                json.dumps(payload, default=str, ensure_ascii=False),
                decision,
                json.dumps(detail or {}, default=str, ensure_ascii=False),
            ),
        )
        return cur.lastrowid


def recent(limit: int = 25) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def count() -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
