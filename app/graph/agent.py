"""Compiled LangGraph graphs and the public agent facade.

There are two graphs:

* ``chat_graph``  - read-only conversation, intake, retrieval, policy and draft
                    creation.  It can never call a write tool.
* ``approval_graph`` - the only graph that can execute ``create_ticket`` or
                    ``add_ticket_note``.

The API layer calls :class:`HelpdeskAgent`, which simply invokes the matching
compiled graph.  There is no separate procedural dispatcher.
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.llm import UsageMeter
from app.memory.sessions import sessions

from app.graph.approval import ApprovalNodes
from app.graph.chat import ChatNodes
from app.graph.state import AgentState


# ── chat graph routing ───────────────────────────────────────────────────
def _halt_or(next_node: str):
    def route(state: AgentState) -> str:
        return "finalize" if state.get("halted") else next_node

    return route


def _route_after_input_guard(state: AgentState) -> str:
    return "finalize" if state.get("halted") else "pending_gate"


def _route_after_memory_intent(state: AgentState) -> str:
    intent = state.get("intent")
    if intent == "remember":
        return "recall_memory"
    if intent == "tickets":
        return "list_tickets"
    if intent == "followup":
        return "ticket_followup"
    return "intake"


def _route_after_policy(state: AgentState) -> str:
    decision = state.get("decision")
    if decision is not None and decision.refusal is not None:
        return "refusal"
    if state.get("email") and state.get("employee") is None:
        return "identity_failure"
    return "phone_policy"


def _route_after_retrieval(state: AgentState) -> str:
    return "draft_guard" if state.get("should_draft") else "resolve"


def build_chat_graph():
    nodes = ChatNodes()
    graph = StateGraph(AgentState)

    graph.add_node("input_guard", nodes.input_guard)
    graph.add_node("pending_gate", nodes.pending_gate)
    graph.add_node("identity", nodes.identity)
    graph.add_node("preference_intent", nodes.preference_intent)
    graph.add_node("classify_memory_intent", nodes.classify_memory_intent)
    graph.add_node("recall_memory", nodes.recall_memory)
    graph.add_node("list_tickets", nodes.list_tickets)
    graph.add_node("ticket_followup", nodes.ticket_followup)
    graph.add_node("intake", nodes.intake)
    graph.add_node("service_status", nodes.service_status)
    graph.add_node("policy", nodes.policy)
    graph.add_node("refusal", nodes.refusal)
    graph.add_node("identity_failure", nodes.identity_failure)
    graph.add_node("phone_policy", nodes.phone_policy)
    graph.add_node("software_policy", nodes.software_policy)
    graph.add_node("incident", nodes.incident)
    graph.add_node("clarification", nodes.clarification)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("draft_guard", nodes.draft_guard)
    graph.add_node("escalate", nodes.escalate)
    graph.add_node("resolve", nodes.resolve)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges(
        "input_guard",
        _route_after_input_guard,
        {"finalize": "finalize", "pending_gate": "pending_gate"},
    )
    graph.add_conditional_edges(
        "pending_gate",
        _halt_or("identity"),
        {"finalize": "finalize", "identity": "identity"},
    )
    graph.add_edge("identity", "preference_intent")
    graph.add_conditional_edges(
        "preference_intent",
        _halt_or("classify_memory_intent"),
        {"finalize": "finalize", "classify_memory_intent": "classify_memory_intent"},
    )
    graph.add_conditional_edges(
        "classify_memory_intent",
        _route_after_memory_intent,
        {
            "recall_memory": "recall_memory",
            "list_tickets": "list_tickets",
            "ticket_followup": "ticket_followup",
            "intake": "intake",
        },
    )
    graph.add_edge("recall_memory", "finalize")
    graph.add_edge("list_tickets", "finalize")
    graph.add_edge("ticket_followup", "finalize")
    graph.add_edge("intake", "service_status")
    graph.add_edge("service_status", "policy")
    graph.add_conditional_edges(
        "policy",
        _route_after_policy,
        {
            "refusal": "refusal",
            "identity_failure": "identity_failure",
            "phone_policy": "phone_policy",
        },
    )
    graph.add_edge("refusal", "finalize")
    graph.add_edge("identity_failure", "finalize")
    graph.add_conditional_edges(
        "phone_policy",
        _halt_or("software_policy"),
        {"finalize": "finalize", "software_policy": "software_policy"},
    )
    graph.add_conditional_edges(
        "software_policy",
        _halt_or("incident"),
        {"finalize": "finalize", "incident": "incident"},
    )
    graph.add_conditional_edges(
        "incident",
        _halt_or("clarification"),
        {"finalize": "finalize", "clarification": "clarification"},
    )
    graph.add_conditional_edges(
        "clarification",
        _halt_or("retrieve"),
        {"finalize": "finalize", "retrieve": "retrieve"},
    )
    graph.add_conditional_edges(
        "retrieve",
        _route_after_retrieval,
        {"draft_guard": "draft_guard", "resolve": "resolve"},
    )
    graph.add_conditional_edges(
        "draft_guard",
        _halt_or("escalate"),
        {"finalize": "finalize", "escalate": "escalate"},
    )
    graph.add_edge("escalate", "finalize")
    graph.add_edge("resolve", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


# ── approval graph routing ───────────────────────────────────────────────
def _route_after_load(state: AgentState) -> str:
    if state.get("halted") or state.get("action") is None:
        return "finalize"
    return "claim_action" if state.get("approved") else "reject_action"


def _route_after_claim(state: AgentState) -> str:
    claim = state.get("claim")
    if claim == "executed":
        return "already_executed"
    if claim == "executing":
        return "already_executing"
    if claim == "rejected":
        return "already_rejected"
    if claim != "claimed":
        return "write_failure"

    action = state.get("action")
    if action is not None and action.type == "create_ticket":
        return "write_ticket"
    if action is not None and action.type == "add_ticket_note":
        return "write_note"
    return "unsupported_action"


def _route_after_write(state: AgentState) -> str:
    outcome = state.get("tool_outcome")
    if outcome is not None and outcome.ok:
        return "ticket_success"
    return "write_failure"


def _route_after_note_write(state: AgentState) -> str:
    outcome = state.get("tool_outcome")
    if outcome is not None and outcome.ok:
        return "note_success"
    return "write_failure"


def build_approval_graph():
    nodes = ApprovalNodes()
    graph = StateGraph(AgentState)

    graph.add_node("load_action", nodes.load_action)
    graph.add_node("reject_action", nodes.reject_action)
    graph.add_node("claim_action", nodes.claim_action)
    graph.add_node("already_executed", nodes.already_executed)
    graph.add_node("already_executing", nodes.already_executing)
    graph.add_node("already_rejected", nodes.already_rejected)
    graph.add_node("write_ticket", nodes.write_ticket)
    graph.add_node("write_note", nodes.write_note)
    graph.add_node("unsupported_action", nodes.unsupported_action)
    graph.add_node("ticket_success", nodes.ticket_success)
    graph.add_node("note_success", nodes.note_success)
    graph.add_node("write_failure", nodes.write_failure)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, "load_action")
    graph.add_conditional_edges(
        "load_action",
        _route_after_load,
        {
            "finalize": "finalize",
            "reject_action": "reject_action",
            "claim_action": "claim_action",
        },
    )
    graph.add_edge("reject_action", "finalize")
    graph.add_conditional_edges(
        "claim_action",
        _route_after_claim,
        {
            "already_executed": "already_executed",
            "already_executing": "already_executing",
            "already_rejected": "already_rejected",
            "write_ticket": "write_ticket",
            "write_note": "write_note",
            "unsupported_action": "unsupported_action",
            "write_failure": "write_failure",
        },
    )
    graph.add_conditional_edges(
        "write_ticket",
        _route_after_write,
        {"ticket_success": "ticket_success", "write_failure": "write_failure"},
    )
    graph.add_conditional_edges(
        "write_note",
        _route_after_note_write,
        {"note_success": "note_success", "write_failure": "write_failure"},
    )
    graph.add_edge("unsupported_action", "write_failure")
    graph.add_edge("ticket_success", "finalize")
    graph.add_edge("note_success", "finalize")
    graph.add_edge("write_failure", "finalize")
    graph.add_edge("already_executed", "finalize")
    graph.add_edge("already_executing", "finalize")
    graph.add_edge("already_rejected", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


class HelpdeskAgent:
    """Facade around the two compiled LangGraph graphs."""

    def __init__(self) -> None:
        self.chat_graph = build_chat_graph()
        self.approval_graph = build_approval_graph()

    async def chat(
        self,
        session_id: str,
        message: str,
        user: dict[str, Any] | None = None,
    ):
        session = sessions.get_or_create(session_id)
        meter = UsageMeter()
        initial: AgentState = {
            "mode": "chat",
            "session_id": session_id,
            "message": message,
            "user": user,
            "approved": False,
            "action_id": None,
            "tool_calls": [],
            "halted": False,
        }
        config = {"configurable": {"session": session, "meter": meter}}
        final = await self.chat_graph.ainvoke(initial, config=config)
        return final["response"]

    async def approve(
        self,
        session_id: str,
        action_id: str | None,
        approved: bool,
    ):
        session = sessions.get_or_create(session_id)
        meter = UsageMeter()
        initial: AgentState = {
            "mode": "approve",
            "session_id": session_id,
            "message": "",
            "user": None,
            "approved": approved,
            "action_id": action_id,
            "tool_calls": [],
            "halted": False,
        }
        config = {"configurable": {"session": session, "meter": meter}}
        final = await self.approval_graph.ainvoke(initial, config=config)
        return final["response"]


helpdesk_agent = HelpdeskAgent()
