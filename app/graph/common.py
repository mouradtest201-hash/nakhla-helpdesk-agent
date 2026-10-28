"""Shared helpers used by the LangGraph nodes.

These functions contain no orchestration decisions: routing lives in the graph
edges.  Keeping the small conversions here avoids duplicating them in every
node.
"""
from __future__ import annotations

import logging
from typing import Any

from app.domain.manifest import load_manifest
from app.guardrails.output_guard import guard_reply
from app.mcp.client import ToolOutcome
from app.memory.sessions import SessionState, now_iso, sessions
from app.models import ChatResponse, Employee, PendingAction, ToolCall, Usage

log = logging.getLogger(__name__)


def tool_call(outcome: ToolOutcome) -> ToolCall:
    return ToolCall(tool=outcome.tool, ok=outcome.ok, ms=outcome.ms)


def employee_from(data: dict[str, Any] | None) -> Employee | None:
    if not data:
        return None
    try:
        return Employee(**data)
    except Exception:  # noqa: BLE001
        return None


def pending_from(action: Any) -> PendingAction | None:
    if action is None:
        return None
    return PendingAction(
        action_id=action.action_id,
        type=action.type,
        summary=action.summary,
        payload=action.payload,
    )


def citations_for(doc_ids: list[str]) -> list[dict[str, Any]]:
    manifest = load_manifest()
    out: list[dict[str, Any]] = []
    for doc_id in doc_ids:
        citation = manifest.citation(doc_id)
        if citation and citation["id"] not in {c["id"] for c in out}:
            out.append(citation)
    return out


def respond(
    session: SessionState,
    reply: str,
    state_name: str,
    citations: list[dict[str, Any]] | None = None,
    tool_calls: list[ToolCall] | None = None,
    usage: dict[str, Any] | None = None,
    pending: PendingAction | None = None,
    result: dict[str, Any] | None = None,
    known_ticket_ids: list[str] | None = None,
) -> ChatResponse:
    """Apply the output guard, persist the turn and build the API response."""
    citations = citations or []
    clean, kept, warnings = guard_reply(
        reply,
        citations=citations,
        result=result,
        known_ticket_ids=known_ticket_ids,
    )
    if warnings:
        log.debug("output guard: %s", warnings)
    if not clean.strip():
        clean = "I couldn't produce a safe answer. Please rephrase the request."

    session.last_reply = clean
    session.last_citations = kept
    session.last_tool_calls = [t.model_dump() for t in (tool_calls or [])]
    session.updated_at = now_iso()
    session.add_message("assistant", clean)
    sessions.save(session)

    return ChatResponse(
        session_id=session.session_id,
        reply=clean,
        state=state_name,
        pending_action=pending,
        result=result,
        citations=kept,
        tool_calls=tool_calls or [],
        usage=Usage(**(usage or {})),
    )


def get_runtime(config: dict[str, Any]) -> tuple[SessionState, Any]:
    """Return the per-request session and usage meter from graph config."""
    configurable = config.get("configurable") or {}
    return configurable["session"], configurable["meter"]
