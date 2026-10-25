"""Durable memory: employee profile cache and useful facts across sessions.

The helpdesk API is the system of record for employees and tickets.  We keep a
small local memory so the agent can recognise a returning employee, remember
what was already tried, and avoid asking the same question twice.  A memory is
always labelled as our note, never as company policy.
"""
from __future__ import annotations

import json
from typing import Any

from app.memory.db import connect, init_db
from app.memory.sessions import now_iso


def remember_employee(employee: dict[str, Any]) -> None:
    init_db()
    email = (employee.get("email") or "").strip().lower()
    if not email:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO employees(email, data, updated_at) VALUES(?,?,?)
            ON CONFLICT(email) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
            """,
            (email, json.dumps(employee, ensure_ascii=False), now_iso()),
        )


def cached_employee(email: str | None) -> dict[str, Any] | None:
    if not email:
        return None
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT data FROM employees WHERE email = ?", (email.lower(),)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["data"])
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def remember(email: str | None, key: str, value: Any) -> None:
    if not email:
        return
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO memories(email, key, value, updated_at) VALUES(?,?,?,?)
            ON CONFLICT(email, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (email.lower(), key[:120], json.dumps(value, ensure_ascii=False), now_iso()),
        )


def recall(email: str | None, limit: int = 8) -> list[dict[str, Any]]:
    if not email:
        return []
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT key, value, updated_at FROM memories WHERE email = ? ORDER BY updated_at DESC LIMIT ?",
            (email.lower(), max(1, min(limit, 30))),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row["value"])
        except ValueError:
            value = row["value"]
        out.append({"key": row["key"], "value": value, "at": row["updated_at"]})
    return out


def remember_ticket(email: str | None, ticket: dict[str, Any]) -> None:
    ticket_id = ticket.get("ticket_id")
    if not ticket_id:
        return
    remember(email, f"ticket:{ticket_id}", {
        "ticket_id": ticket_id,
        "subject": ticket.get("subject"),
        "category": ticket.get("category"),
        "priority": ticket.get("priority"),
        "status": ticket.get("status"),
        "assigned_team": ticket.get("assigned_team"),
    })
    remember(email, "last_issue", {
        "ticket_id": ticket_id,
        "subject": ticket.get("subject"),
        "error_code": next((r for r in (ticket.get("kb_refs") or []) if r.startswith("KB-")), None),
    })


def memory_summary(email: str | None) -> str:
    facts = recall(email, limit=6)
    if not facts:
        return ""
    lines = []
    for fact in facts:
        value = fact.get("value")
        if fact["key"].startswith("ticket:") and isinstance(value, dict):
            lines.append(
                f"- previous ticket {value.get('ticket_id')}: {value.get('subject')} "
                f"({value.get('status')}, {value.get('priority')})"
            )
        elif fact["key"] in ("last_issue", "preference"):
            lines.append(f"- {fact['key']}: {json.dumps(value, ensure_ascii=False)[:200]}")
    return "\n".join(lines[-5:])
