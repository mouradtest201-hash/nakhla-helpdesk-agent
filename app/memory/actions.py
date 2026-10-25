"""Pending actions and the single-write approval gate.

An action is a *proposal*.  It becomes a write only in ``/api/approve``, and the
same action id is used as the API idempotency key, so a double click (or a
retried HTTP request) cannot create a second ticket.  The ``proposed ->
executing`` transition is atomic in SQLite.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from app.memory.db import connect, init_db
from app.memory.sessions import now_iso


@dataclass
class Action:
    action_id: str
    session_id: str
    type: str
    summary: str
    payload: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    created_at: str
    updated_at: str

    def as_public(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "type": self.type,
            "summary": self.summary,
            "payload": self.payload,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "session_id": self.session_id,
            "type": self.type,
            "summary": self.summary,
            "payload": self.payload,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _row_to_action(row) -> Action:
    return Action(
        action_id=row["action_id"],
        session_id=row["session_id"],
        type=row["type"],
        summary=row["summary"],
        payload=json.loads(row["payload"]),
        status=row["status"],
        result=json.loads(row["result"]) if row["result"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ActionStore:
    def __init__(self) -> None:
        init_db()

    def create(self, session_id: str, action_type: str, summary: str, payload: dict[str, Any]) -> Action:
        action_id = f"act_{uuid.uuid4().hex[:20]}"
        now = now_iso()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO actions(action_id, session_id, type, summary, payload, status, result, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (action_id, session_id, action_type, summary,
                 json.dumps(payload, ensure_ascii=False), "proposed", None, now, now),
            )
        return Action(action_id, session_id, action_type, summary, payload, "proposed", None, now, now)

    def get(self, action_id: str) -> Action | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM actions WHERE action_id = ?", (action_id,)).fetchone()
        return _row_to_action(row) if row else None

    def pending_for_session(self, session_id: str) -> Action | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM actions WHERE session_id = ? AND status = 'proposed' ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return _row_to_action(row) if row else None

    def begin(self, action_id: str) -> tuple[str, Action | None]:
        """Atomically claim an action for execution.

        Returns (state, action) where state is one of:
        ``claimed``, ``executed``, ``executing``, ``missing``, ``rejected``.
        """
        now = now_iso()
        with connect() as conn:
            cur = conn.execute(
                "UPDATE actions SET status='executing', updated_at=? "
                "WHERE action_id=? AND status IN ('proposed','failed')",
                (now, action_id),
            )
            changed = cur.rowcount
            row = conn.execute("SELECT * FROM actions WHERE action_id = ?", (action_id,)).fetchone()
        if row is None:
            return "missing", None
        action = _row_to_action(row)
        if changed:
            return "claimed", action
        if action.status == "executed":
            return "executed", action
        if action.status == "executing":
            return "executing", action
        if action.status == "rejected":
            return "rejected", action
        return action.status, action

    def finish(self, action_id: str, *, status: str, result: dict[str, Any] | None) -> Action | None:
        with connect() as conn:
            conn.execute(
                "UPDATE actions SET status=?, result=?, updated_at=? WHERE action_id=?",
                (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                 now_iso(), action_id),
            )
            row = conn.execute("SELECT * FROM actions WHERE action_id = ?", (action_id,)).fetchone()
        return _row_to_action(row) if row else None

    def reject(self, action_id: str) -> Action | None:
        return self.finish(action_id, status="rejected", result=None)

    def recent(self, session_id: str, limit: int = 10) -> list[Action]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM actions WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, max(1, min(limit, 50))),
            ).fetchall()
        return [_row_to_action(row) for row in rows]


actions = ActionStore()
