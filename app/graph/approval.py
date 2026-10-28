"""LangGraph nodes for the approval path.

This is the only graph that reaches a write tool.  Its edges encode the
proposed -> executing -> executed/rejected/failed action lifecycle, so the write
gate is visible in the graph rather than hidden in a procedure.
"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents import (
    already_created_reply,
    approved_reply,
    failed_reply,
    note_approved_reply,
    rejected_reply,
)
from app.mcp.client import ToolOutcome, mcp_client
from app.memory.actions import actions
from app.memory.long_term import remember, remember_ticket

from app.graph.common import (
    citations_for,
    get_runtime,
    pending_from,
    respond,
    tool_call,
)
from app.graph.errors import AgentError
from app.graph.state import AgentState


class ApprovalNodes:
    """The nodes of the approval graph."""

    def load_action(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        action_id = state.get("action_id") or session.pending_action_id
        if not action_id:
            return {
                "reply": (
                    "There is no pending action in this session, so there is nothing to approve and "
                    "nothing was written. Describe the problem again and I can prepare a draft."
                ),
                "state_name": "answered",
                "action": None,
                "halted": True,
            }

        action = actions.get(action_id)
        if action is None or action.session_id != session.session_id:
            return {
                "reply": (
                    f"I don't have a pending action **{action_id}** in this session, so there is nothing "
                    "to approve and nothing was written. If a draft was already approved, its ticket id "
                    "is in the earlier reply; otherwise describe the problem again and I can prepare a "
                    "new draft."
                ),
                "state_name": "answered",
                "action": None,
                "halted": True,
            }

        return {
            "action_id": action_id,
            "action": action,
            "citations": citations_for(list((action.payload or {}).get("kb_refs") or [])),
            "halted": False,
        }

    def reject_action(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        action = state.get("action")
        assert action is not None

        result: dict[str, Any] | None = None
        pending = None
        state_name = "answered"

        if action.status == "executed":
            result = action.result
            reply = already_created_reply(result or {})
            state_name = "completed"
        elif action.status == "rejected":
            reply = "This draft was already rejected. Nothing was written."
            state_name = "cancelled"
        elif action.status == "executing":
            reply = "This action is already being processed; I can't cancel it now."
            pending = pending_from(action)
            state_name = "awaiting_approval"
        else:
            actions.reject(action.action_id)
            session.pending_action_id = None
            reply = rejected_reply()
            state_name = "cancelled"

        return {
            "reply": reply,
            "state_name": state_name,
            "result": result,
            "pending": pending,
            "halted": True,
        }

    def claim_action(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        action_id = state.get("action_id")
        assert action_id is not None
        claim, action = actions.begin(action_id)
        if claim == "missing" or action is None:
            raise AgentError(
                "action_not_found",
                "No such pending action for this session.",
                404,
            )
        return {"claim": claim, "action": action, "halted": False}

    def already_executed(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        action = state.get("action")
        assert action is not None
        result = action.result
        session.pending_action_id = None
        return {
            "reply": already_created_reply(result or {}),
            "state_name": "completed",
            "result": result,
            "halted": True,
        }

    def already_executing(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        action = state.get("action")
        return {
            "reply": (
                "This approval is already being processed. I won't send a second write; the same "
                "action id is used as the idempotency key."
            ),
            "state_name": "awaiting_approval",
            "pending": pending_from(action),
            "halted": True,
        }

    def already_rejected(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        return {
            "reply": (
                "This draft was rejected earlier, so there is nothing to execute. Prepare a new draft "
                "if you still want a ticket."
            ),
            "state_name": "cancelled",
            "halted": True,
        }

    async def write_ticket(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        action = state.get("action")
        assert action is not None
        payload = action.payload or {}
        outcome = await mcp_client.call(
            "create_ticket",
            {**payload, "idempotency_key": action.action_id},
            timeout=45,
        )
        return {
            "tool_outcome": outcome,
            "tool_calls": [tool_call(outcome)],
            "halted": False,
        }

    async def write_note(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        action = state.get("action")
        assert action is not None
        payload = action.payload or {}
        outcome = await mcp_client.call(
            "add_ticket_note",
            {"ticket_id": payload.get("ticket_id"), "note": payload.get("note")},
            timeout=45,
        )
        return {
            "tool_outcome": outcome,
            "tool_calls": [tool_call(outcome)],
            "halted": False,
        }

    def unsupported_action(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        outcome = ToolOutcome(
            "unknown",
            False,
            0,
            {
                "ok": False,
                "error": {
                    "code": "unsupported_action",
                    "message": "Unsupported action type.",
                },
            },
        )
        return {
            "tool_outcome": outcome,
            "tool_calls": [tool_call(outcome)],
            "halted": False,
        }

    def ticket_success(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        action = state.get("action")
        outcome = state.get("tool_outcome")
        assert action is not None and outcome is not None

        data = outcome.data
        ticket = data.get("ticket") if isinstance(data.get("ticket"), dict) else data
        if not isinstance(ticket, dict) or not ticket.get("ticket_id"):
            actions.finish(action.action_id, status="failed", result={"error": "no_ticket_id"})
            return {
                "reply": failed_reply(
                    {"code": "no_ticket_id", "message": "The helpdesk did not return a ticket id."}
                ),
                "state_name": "failed",
                "pending": pending_from(actions.get(action.action_id)),
                "halted": True,
            }

        result = {
            "ticket_id": ticket.get("ticket_id"),
            "status": ticket.get("status", "open"),
            "category": ticket.get("category"),
            "priority": ticket.get("priority"),
            "assigned_team": ticket.get("assigned_team"),
            "subject": ticket.get("subject"),
        }
        actions.finish(action.action_id, status="executed", result=result)
        if session.email:
            remember_ticket(session.email, ticket)
            remember(
                session.email,
                "last_action",
                {"action_id": action.action_id, "ticket_id": result["ticket_id"]},
            )
        session.tickets_created.append(str(result["ticket_id"]))
        session.pending_action_id = None
        return {
            "reply": approved_reply(ticket),
            "state_name": "completed",
            "result": result,
            "halted": True,
        }

    def note_success(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        action = state.get("action")
        outcome = state.get("tool_outcome")
        assert action is not None and outcome is not None

        payload = action.payload or {}
        data = outcome.data
        result = {
            "ticket_id": data.get("ticket_id") or payload.get("ticket_id"),
            "note_added": True,
            "notes_count": data.get("notes_count"),
        }
        actions.finish(action.action_id, status="executed", result=result)
        if session.email:
            remember(session.email, f"note:{result['ticket_id']}", payload.get("note"))
        session.pending_action_id = None
        return {
            "reply": note_approved_reply(result),
            "state_name": "completed",
            "result": result,
            "halted": True,
        }

    def write_failure(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        action = state.get("action")
        outcome = state.get("tool_outcome")
        assert action is not None
        error = (outcome.error if outcome else None) or {}
        actions.finish(action.action_id, status="failed", result={"error": error})
        return {
            "reply": failed_reply(error),
            "state_name": "failed",
            "pending": pending_from(actions.get(action.action_id)),
            "halted": True,
        }

    def finalize(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, meter = get_runtime(config)
        response = respond(
            session,
            state.get("reply") or "The approval could not be completed.",
            state.get("state_name") or "answered",
            citations=state.get("citations"),
            tool_calls=state.get("tool_calls"),
            usage=meter.public(),
            pending=state.get("pending"),
            result=state.get("result"),
            known_ticket_ids=state.get("known_ticket_ids"),
        )
        return {"response": response}
