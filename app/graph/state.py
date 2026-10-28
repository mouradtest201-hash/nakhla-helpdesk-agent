"""Typed graph state for the Nakhla helpdesk agent.

The state is deliberately explicit: each LangGraph node reads the fields it
needs and returns only the fields it changes.  Session objects, the usage meter
and long-lived caches are *runtime context* (see ``config["configurable"]``),
not graph state, because they outlive one graph invocation.
"""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.policy import Decision
from app.mcp.client import ToolOutcome
from app.memory.actions import Action
from app.models import ChatResponse, Employee, IntakeResult, PendingAction, ToolCall
from app.rag.retriever import RetrievalResult


class AgentState(TypedDict, total=False):
    """State passed between the agent's LangGraph nodes."""

    # ── request ──────────────────────────────────────────────────────────
    mode: str
    session_id: str
    message: str
    user: dict[str, Any] | None
    approved: bool
    action_id: str | None

    # ── guard + identity ─────────────────────────────────────────────────
    text: str
    halted: bool
    email: str | None
    employee: Employee | None
    lookup_failed: bool
    intent: str | None

    # ── intake + policy ──────────────────────────────────────────────────
    intake: IntakeResult | None
    decision: Decision | None
    status: dict[str, Any] | None

    # ── retrieval + escalation ───────────────────────────────────────────
    retrieval: RetrievalResult | None
    grounded: bool
    should_draft: bool
    play_code: str | None

    # ── approval ─────────────────────────────────────────────────────────
    action: Action | None
    claim: str | None
    tool_outcome: ToolOutcome | None

    # ── response ─────────────────────────────────────────────────────────
    reply: str
    state_name: str
    citations: list[dict[str, Any]]
    tool_calls: Annotated[list[ToolCall], add]
    pending: PendingAction | None
    result: dict[str, Any] | None
    known_ticket_ids: list[str]
    response: ChatResponse | None
