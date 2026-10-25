"""Short-term conversation memory, persisted so a restart does not lose a draft."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import MAX_CONVERSATION_MESSAGES, SESSION_TTL_MINUTES
from app.memory.db import connect, init_db


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SessionState:
    session_id: str
    email: str | None = None
    employee: dict[str, Any] | None = None
    messages: list[dict[str, str]] = field(default_factory=list)
    messages_saved: int = 0
    turn: int = 0
    last_intake: dict[str, Any] | None = None
    last_decision: dict[str, Any] | None = None
    last_citations: list[dict[str, Any]] = field(default_factory=list)
    last_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    last_reply: str = ""
    pending_action_id: str | None = None
    status_cache: dict[str, Any] | None = None
    status_at: float = 0.0
    offered_ticket: bool = False
    asked_for: list[str] = field(default_factory=list)
    tickets_created: list[str] = field(default_factory=list)
    memory_notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=lambda: now_iso())

    def add_message(self, role: str, text: str) -> None:
        self.messages.append({"role": role, "text": text, "at": now_iso()})
        if len(self.messages) > MAX_CONVERSATION_MESSAGES:
            self.messages = self.messages[-MAX_CONVERSATION_MESSAGES:]

    def recent(self, limit: int = 8) -> list[dict[str, str]]:
        return self.messages[-limit:]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class SessionStore:
    def __init__(self) -> None:
        init_db()
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionState | None:
        with self._lock:
            cached = self._sessions.get(session_id)
        if cached is not None:
            return cached
        with connect() as conn:
            row = conn.execute(
                "SELECT state FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            state = SessionState.from_dict(json.loads(row["state"]))
        except Exception:  # noqa: BLE001
            return None
        with self._lock:
            self._sessions[session_id] = state
        return state

    def get_or_create(self, session_id: str, email: str | None = None) -> SessionState:
        state = self.get(session_id)
        if state is None:
            state = SessionState(session_id=session_id, email=email)
            self.save(state)
        elif email and not state.email:
            state.email = email
        return state

    def save(self, state: SessionState) -> None:
        state.updated_at = now_iso()
        with self._lock:
            self._sessions[state.session_id] = state
        payload = json.dumps(state.to_dict(), ensure_ascii=False)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(session_id, email, state, created_at, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    email=excluded.email, state=excluded.state, updated_at=excluded.updated_at
                """,
                (state.session_id, state.email, payload, state.created_at, state.updated_at),
            )
            # Keep a searchable message log for the long-term memory demo.
            # Only insert messages that were not written on an earlier save.
            if state.messages_saved > len(state.messages):
                state.messages_saved = 0
            pending = state.messages[state.messages_saved:]
            for message in pending:
                conn.execute(
                    "INSERT INTO messages(session_id, role, text, created_at) VALUES(?,?,?,?)",
                    (state.session_id, message["role"], message["text"][:4000],
                     message.get("at") or now_iso()),
                )
            state.messages_saved = len(state.messages)

    def set_pending(self, state: SessionState, action_id: str | None) -> None:
        state.pending_action_id = action_id
        self.save(state)

    def cleanup(self) -> int:
        cutoff = time.time() - SESSION_TTL_MINUTES * 60
        with self._lock:
            stale = [
                sid for sid, state in self._sessions.items()
                if _epoch(state.updated_at) < cutoff and state.pending_action_id is None
            ]
            for sid in stale:
                self._sessions.pop(sid, None)
        return len(stale)


def _epoch(iso: str) -> float:
    try:
        return dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return time.time()


sessions = SessionStore()
