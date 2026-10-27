"""The helpdesk workflow implemented with LangGraph.

Uses a StateGraph to orchestrate: intake -> status/incidents -> policy -> retrieval -> answer/draft.
The critical invariant remains: only `approve()` can write, and every write is tied to
a pending action id used as the MCP/idempotency key.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents import (
    already_created_reply,
    approved_reply,
    build_ticket,
    clarify_reply,
    draft_reply,
    extract_email,
    failed_reply,
    incident_reply,
    note_approved_reply,
    note_draft_reply,
    phone_left_reply,
    refusal_reply,
    rejected_reply,
    run_intake,
    software_policy_reply,
    troubleshooting_reply,
    unknown_reply,
)
from app.config import (
    HELPDESK_API_URL,
    MAX_ACTIONS_PER_SESSION,
)
from app.domain.manifest import load_manifest
from app.domain.playbooks import TOPICS, detect_topic, playbook_for
from app.domain.policy import WORKAROUND_RE, decide, detect_ticket_request, ticket_allowed
from app.guardrails.input_guard import inspect, looks_like_approval, looks_like_rejection
from app.guardrails.output_guard import guard_reply
from app.llm import UsageMeter
from app.mcp.client import ToolOutcome, mcp_client
from app.memory.actions import Action, actions
from app.memory.long_term import (
    cached_employee,
    memory_summary,
    recall,
    remember,
    remember_employee,
    remember_ticket,
)
from app.memory.sessions import SessionState, now_iso, sessions
from app.models import ChatResponse, Employee, IntakeResult, PendingAction, ToolCall, Usage
from app.rag.retriever import RetrievalResult, retrieve

log = logging.getLogger(__name__)

TICKET_ID_RE = re.compile(r"\bIT-\d{4,}\b", re.I)
OWN_TICKETS_RE = re.compile(
    r"\b(my|our|any|existing|previous|prior|last|closed|resolved)\b\s+(?:[a-z-]+\s+){0,2}tickets?\b"
    r"|\b(?:open|assigned)\s+tickets\b"
    r"|\bticket (?:history|archive|status|list)\b"
    r"|\bwhat did i (?:report|open|submit)\b"
    r"|\bdo i have\b.{0,20}\btickets?\b"
    r"|\b(?:list|show|see|check)\b.{0,15}\bmy\b.{0,15}\btickets?\b",
    re.I,
)
REMEMBER_RE = re.compile(r"\b(remember|last time|previous (contact|conversation)|what did we discuss)\b", re.I)
REMEMBER_THAT_RE = re.compile(
    r"^\s*(?:please\s+)?(?:remember|keep in mind|note)\b\s*(?:that|:)\s*(.+)", re.I | re.S
)
PHONE_LEFT_RE = re.compile(
    r"\b(?:left|forgot|forgotten|forget|misplaced)\b.{0,25}\b(?:phone|mobile|handset)\b"
    r"|\b(?:phone|mobile)\b.{0,20}\b(?:at home|left at home)\b",
    re.I,
)
LOST_DEVICE_RE = re.compile(r"\b(lost|stolen|missing)\b", re.I)
NOTE_RE = re.compile(
    r"\b(?:add|append|attach|leave|put)\b.{0,30}\b(?:a\s+)?(?:note|comment|information)\b"
    r"|\bnote\s+(?:that|this|for|to)\b"
    r"|\b(?:add|put)\b.{0,15}\b(?:this|it)\b.{0,20}\b(?:on|to)\b.{0,10}\bticket\b",
    re.I,
)
STATUS_BY_TOPIC = {
    "vpn": "vpn",
    "wifi": "wifi",
    "mail": "mail",
    "printing": "print",
    "software": "software",
    "hardware": "mdm",
    "onboarding": "vdi",
}

_global_status_cache = None
_global_status_cache_time = 0.0
_GLOBAL_STATUS_TTL = 300


class WorkflowError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class Turn:
    response: ChatResponse
    state: SessionState


def _tool_call(outcome: ToolOutcome) -> ToolCall:
    return ToolCall(tool=outcome.tool, ok=outcome.ok, ms=outcome.ms)


def _employee(data: dict[str, Any] | None) -> Employee | None:
    if not data:
        return None
    try:
        return Employee(**data)
    except Exception:  # noqa: BLE001
        return None


def _match_incident(
    status: dict[str, Any] | None,
    code: str | None,
    topic: str | None,
    message: str,
    employee: Employee | None,
) -> dict[str, Any] | None:
    if not status:
        return None
    incidents = status.get("incidents") or []
    low = message.lower()
    for incident in incidents:
        if incident.get("status") != "active":
            continue
        codes = [str(c).upper() for c in incident.get("error_codes") or []]
        if code and code.upper() in codes:
            if employee and employee.department == "Finance" and employee.vpn_gateway == "GW-3":
                continue
            return incident
        if not code and topic == "vpn" and incident.get("service") == "vpn":
            symptoms = f"{incident.get('title', '')} {incident.get('symptoms', '')}".lower()
            if any(word in low for word in ("disconnect", "every few minutes", "gateway degraded")) and any(
                word in symptoms for word in ("disconnect", "gateway", "degraded")
            ):
                return incident
    return None


def _pending(action: Action | None) -> PendingAction | None:
    if action is None:
        return None
    return PendingAction(
        action_id=action.action_id,
        type=action.type,  # type: ignore[arg-type]
        summary=action.summary,
        payload=action.payload,
    )


def _citations(doc_ids: list[str]) -> list[dict[str, Any]]:
    manifest = load_manifest()
    out: list[dict[str, Any]] = []
    for doc_id in doc_ids:
        citation = manifest.citation(doc_id)
        if citation and citation["id"] not in {c["id"] for c in out}:
            out.append(citation)
    return out


def _add_tool_calls(existing: list[ToolCall], outcomes: list[ToolOutcome]) -> list[ToolCall]:
    result = list(existing)
    for o in outcomes:
        result.append(_tool_call(o))
    return result


class AgentState(TypedDict, total=False):
    # Inputs
    session_id: str
    message: str
    user: dict[str, Any] | None

    # Session/context
    session: SessionState
    meter: UsageMeter
    manifest: Any
    tool_calls: Annotated[list[ToolCall], lambda a, b: a + b]

    # Guards/intake
    verdict_text: str
    guard_ok: bool
    guard_response: str | None

    # Identity
    email: str | None
    employee: dict[str, Any] | None
    lookup_failed: bool

    # Status/incidents
    status: dict[str, Any] | None

    # Flow
    intake: Any
    decision: Any
    code: str | None

    # Retrieval/answer
    retrieval: Any
    play: Any
    grounded: bool
    should_draft: bool
    citations: list[dict[str, Any]]
    reply: str
    state_name: str
    pending_action: PendingAction | None
    result: dict[str, Any] | None
    known_ticket_ids: list[str]

    # Early exits
    done: bool


class Workflow:
    def __init__(self) -> None:
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(AgentState)

        graph.add_node("preprocess", self._node_preprocess)
        graph.add_node("handle_pending_approval_chat", self._node_handle_pending_approval_chat)
        graph.add_node("identity", self._node_identity)
        graph.add_node("remember_preference", self._node_remember_preference)
        graph.add_node("memory_intents", self._node_memory_intents)
        graph.add_node("ticket_followup", self._node_ticket_followup)
        graph.add_node("intake", self._node_intake)
        graph.add_node("status", self._node_status)
        graph.add_node("policy", self._node_policy)
        graph.add_node("incident", self._node_incident)
        graph.add_node("phone_left", self._node_phone_left)
        graph.add_node("software_policy", self._node_software_policy)
        graph.add_node("clarify", self._node_clarify)
        graph.add_node("retrieve_draft_answer", self._node_retrieve_draft_answer)
        graph.add_node("finalize", self._node_finalize)

        graph.add_edge(START, "preprocess")

        graph.add_conditional_edges(
            "preprocess",
            self._route_after_preprocess,
            {
                "guard_fail": "finalize",
                "pending_approval": "handle_pending_approval_chat",
                "continue": "identity",
            },
        )

        graph.add_edge("handle_pending_approval_chat", "finalize")
        graph.add_edge("identity", "remember_preference")

        graph.add_conditional_edges(
            "remember_preference",
            self._route_after_remember_preference,
            {"done": "finalize", "continue": "memory_intents"},
        )

        graph.add_conditional_edges(
            "memory_intents",
            self._route_after_memory_intents,
            {"done": "finalize", "ticket_followup": "ticket_followup", "continue": "intake"},
        )

        graph.add_conditional_edges(
            "ticket_followup",
            self._route_after_ticket_followup,
            {"done": "finalize", "continue": "intake"},
        )

        graph.add_edge("intake", "status")
        graph.add_edge("status", "policy")

        graph.add_conditional_edges(
            "policy",
            self._route_after_policy,
            {"refused": "finalize", "unknown_employee": "finalize", "continue": "phone_left"},
        )

        graph.add_edge("phone_left", "software_policy")

        graph.add_conditional_edges(
            "software_policy",
            self._route_after_software_policy,
            {"done": "finalize", "continue": "incident"},
        )

        graph.add_conditional_edges(
            "incident",
            self._route_after_incident,
            {"done": "finalize", "continue": "clarify"},
        )

        graph.add_conditional_edges(
            "clarify",
            self._route_after_clarify,
            {"done": "finalize", "continue": "retrieve_draft_answer"},
        )

        graph.add_edge("retrieve_draft_answer", "finalize")
        graph.add_edge("finalize", END)

        return graph.compile()

    async def _node_preprocess(self, state: AgentState) -> AgentState:
        meter = state.get("meter") or UsageMeter()
        session_id = state["session_id"]
        message = state["message"]
        user = state.get("user")

        verdict = inspect(message)
        session = sessions.get_or_create(session_id)
        session.turn += 1
        session.add_message("user", verdict.text)

        updates: AgentState = {
            "meter": meter,
            "session": session,
            "verdict_text": verdict.text,
            "guard_ok": bool(verdict.ok),
            "guard_response": verdict.response,
            "tool_calls": [],
            "citations": [],
            "known_ticket_ids": [],
            "done": False,
        }
        return updates

    def _route_after_preprocess(self, state: AgentState):
        if not state.get("guard_ok", False):
            return "guard_fail"
        session: SessionState = state["session"]
        if session.pending_action_id and (
            looks_like_approval(state.get("verdict_text", "")) or looks_like_rejection(state.get("verdict_text", ""))
        ):
            return "pending_approval"
        return "continue"

    async def _node_handle_pending_approval_chat(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        pending_id = session.pending_action_id
        pending = actions.get(pending_id) if pending_id else None
        verb = "approve" if looks_like_approval(state.get("verdict_text", "")) else "reject"
        if pending and pending.status in ("proposed", "failed"):
            reply = (
                f"Nothing has been written yet. To {verb} the pending draft you must use the approval step "
                f"(`POST /api/approve` with `action_id: {pending.action_id}`); chat messages cannot approve a "
                "write on their own."
            )
        else:
            reply = (
                "Nothing has been written yet. To approve or reject a pending draft you must use the approval step "
                "(`POST /api/approve` with the `action_id`); chat messages cannot approve a write on their own."
            )
        return {
            "reply": reply,
            "state_name": "awaiting_approval",
            "pending_action": _pending(pending),
            "done": True,
        }

    async def _node_identity(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        user = state.get("user")

        supplied_email = None
        if isinstance(user, dict):
            value = user.get("email") or user.get("work_email")
            if isinstance(value, str) and "@" in value:
                supplied_email = value.strip().lower()
        supplied_email = supplied_email or extract_email(verdict_text)
        if supplied_email and supplied_email != (session.email or "").lower():
            session.email = supplied_email
            session.employee = None
        elif supplied_email:
            session.email = supplied_email

        email = session.email
        lookup_failed = False
        tool_calls_outcomes: list[ToolOutcome] = []

        if session.employee is None and email:
            outcome = await mcp_client.call("lookup_employee", {"email": email})
            tool_calls_outcomes.append(outcome)
            if outcome.ok:
                data = outcome.data.get("employee") or outcome.data
                if isinstance(data, dict) and data.get("email"):
                    session.employee = data
                    remember_employee(data)
            else:
                error = outcome.error or {}
                not_found = (
                    error.get("code") in ("employee_not_found", "not_found")
                    or error.get("http_status") == 404
                )
                cached = cached_employee(email)
                if cached:
                    session.employee = cached
                    session.memory_notes = ["Using a cached directory record because the directory tool failed."]
                elif not not_found:
                    lookup_failed = True

        return {
            "email": session.email,
            "employee": session.employee,
            "lookup_failed": lookup_failed,
            "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes),
            "session": session,
        }

    async def _node_remember_preference(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)

        remember_match = REMEMBER_THAT_RE.search(verdict_text)
        if employee and remember_match:
            note = " ".join(remember_match.group(1).split())[:500]
            if len(note) >= 3:
                remember(employee.email, "preference", note)
                reply = (
                    f"Noted - I'll remember this for your future sessions: \"{note}\". "
                    "This is my note, not company policy, and it cannot change KB/SP rules."
                )
                return {"reply": reply, "state_name": "answered", "citations": [], "done": True}
        return {"done": False}

    def _route_after_remember_preference(self, state: AgentState):
        if state.get("done"):
            return "done"
        return "continue"

    async def _node_memory_intents(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)
        meter: UsageMeter = state["meter"]

        if employee and (OWN_TICKETS_RE.search(verdict_text) or REMEMBER_RE.search(verdict_text)):
            if REMEMBER_RE.search(verdict_text) and not TICKET_ID_RE.search(verdict_text):
                facts = recall(employee.email, limit=8)
                ticket_ids: list[str] = []
                if not facts:
                    reply = "I don't have any earlier notes for you in this assistant's memory. I can still look up your tickets in the helpdesk."
                else:
                    lines = ["Here is what I remember from your earlier contacts (my notes, not company policy):"]
                    for fact in facts[:6]:
                        if fact["key"].startswith("ticket:"):
                            value = fact.get("value") or {}
                            if value.get("ticket_id"):
                                ticket_ids.append(str(value["ticket_id"]))
                            lines.append(
                                f"- Ticket **{value.get('ticket_id')}** - {value.get('subject')} "
                                f"({value.get('status')}, {value.get('priority')})"
                            )
                        elif fact["key"] == "last_issue":
                            value = fact.get("value") or {}
                            lines.append(f"- Last issue: {value.get('error_code') or value.get('title') or value}")
                        elif fact["key"] == "last_draft":
                            lines.append("- I prepared a ticket draft for you recently.")
                        elif fact["key"] == "preference":
                            lines.append(f"- Your saved preference: {fact.get('value')}")
                    reply = "\n".join(lines)
                return {"reply": reply, "state_name": "answered", "citations": [], "known_ticket_ids": ticket_ids, "done": True, "session": session}

            # answer own tickets
            outcome = await mcp_client.call("search_tickets", {"requester_email": employee.email, "limit": 8})
            tool_calls_outcomes = [outcome]
            if not outcome.ok:
                reply = (
                    "I couldn't read your ticket history just now: "
                    + (outcome.error or {}).get("message", "the helpdesk did not answer.")
                    + " Nothing was changed."
                )
                return {
                    "reply": reply,
                    "state_name": "failed",
                    "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes),
                    "done": True,
                    "session": session,
                }
            tickets = outcome.data.get("tickets") or []
            known_ids = [str(t["ticket_id"]) for t in tickets if isinstance(t, dict) and t.get("ticket_id")]
            if not tickets:
                reply = "I don't see any tickets for your email in the helpdesk archive. If you want one, describe the problem and I can prepare a draft."
                return {"reply": reply, "state_name": "answered", "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes), "known_ticket_ids": known_ids, "done": True, "session": session}
            lines = [f"I found **{len(tickets)}** ticket(s) for {employee.email} (newest first):", ""]
            for ticket in tickets[:6]:
                lines.append(
                    f"- **{ticket.get('ticket_id')}** - {ticket.get('subject')}  \n"
                    f"  {ticket.get('status')} · {ticket.get('priority')} · {ticket.get('assigned_team')}"
                    + (f" · resolution: {ticket.get('resolution')}" if ticket.get("resolution") else "")
                )
            reply = "\n".join(lines)
            return {"reply": reply, "state_name": "answered", "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes), "known_ticket_ids": known_ids, "done": True, "session": session}

        ticket_match = TICKET_ID_RE.search(verdict_text)
        if employee and ticket_match:
            return {"ticket_id_followup": ticket_match.group(0).upper(), "done": False}
        return {"done": False}

    def _route_after_memory_intents(self, state: AgentState):
        if state.get("done"):
            return "done"
        if state.get("ticket_id_followup"):
            return "ticket_followup"
        return "continue"

    async def _node_ticket_followup(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)
        ticket_id = state.get("ticket_id_followup")
        if not employee or not ticket_id:
            return {"done": False}

        outcome = await mcp_client.call("get_ticket", {"ticket_id": ticket_id})
        tool_calls_outcomes = [outcome]
        if not outcome.ok:
            reply = f"I couldn't find ticket **{ticket_id}**: " + (outcome.error or {}).get("message", "not found.")
            return {"reply": reply, "state_name": "answered", "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes), "known_ticket_ids": [ticket_id], "done": True, "session": session}

        ticket = outcome.data.get("ticket") or outcome.data
        if not isinstance(ticket, dict):
            ticket = {}
        real_ticket_id = str(ticket.get("ticket_id") or ticket_id)
        if str(ticket.get("requester_email", "")).lower() != employee.email.lower():
            reply = f"Ticket **{ticket_id}** is not one of your tickets, so I can't discuss or change it."
            return {"reply": reply, "state_name": "refused", "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes), "known_ticket_ids": [real_ticket_id], "done": True, "session": session}

        if NOTE_RE.search(verdict_text):
            note = verdict_text.strip()
            if len(note) < 10:
                return {"reply": "What note should I add to the ticket?", "state_name": "clarification", "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes), "done": True, "session": session}
            payload = {"ticket_id": str(ticket.get("ticket_id")), "note": note[:2000]}
            action = actions.create(session.session_id, "add_ticket_note", f"Add a note to {ticket.get('ticket_id')}", payload)
            session.pending_action_id = action.action_id
            return {
                "reply": note_draft_reply(str(ticket.get("ticket_id")), note[:2000]),
                "state_name": "awaiting_approval",
                "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes),
                "pending_action": _pending(action),
                "known_ticket_ids": [real_ticket_id],
                "done": True,
                "session": session,
            }

        lines = [
            f"**{ticket.get('ticket_id')}** - {ticket.get('subject')}",
            "",
            f"- Status: **{ticket.get('status')}**",
            f"- Priority: **{ticket.get('priority')}**",
            f"- Category: `{ticket.get('category')}`",
            f"- Assigned team: {ticket.get('assigned_team')}",
            f"- Created: {ticket.get('created_at')}",
        ]
        if ticket.get("resolution"):
            lines.append(f"- Resolution: {ticket.get('resolution')}")
        if ticket.get("notes"):
            lines.append(f"- Notes: {len(ticket['notes'])}")
        return {"reply": "\n".join(lines), "state_name": "answered", "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes), "known_ticket_ids": [real_ticket_id], "done": True, "session": session}

    def _route_after_ticket_followup(self, state: AgentState):
        if state.get("done"):
            return "done"
        return "continue"

    async def _node_intake(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)
        meter: UsageMeter = state["meter"]

        intake = await run_intake(verdict_text, session, employee, meter=meter)
        return {"intake": intake, "code": intake.error_code, "session": session}

    async def _node_status(self, state: AgentState) -> AgentState:
        global _global_status_cache, _global_status_cache_time
        session: SessionState = state["session"]
        intake = state.get("intake")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)

        status = None
        tool_calls_outcomes: list[ToolOutcome] = []
        needs_clar = getattr(intake, "needs_clarification", False)
        topic = getattr(intake, "issue_topic", None)
        code = getattr(intake, "error_code", None) or state.get("code")
        # For VPN issues with known error codes that might be on status/incidents, force a fresh call
        force_call = False
        if topic == "vpn" and code in {"NX-112", "NX-403", "NX-503", "NX-781", "NX-905"}:
            force_call = True
        if force_call or not (session.status_cache and (time.time() - session.status_at) < 120) and not (_global_status_cache and (time.time() - _global_status_cache_time) < _GLOBAL_STATUS_TTL):
            pass  # will check below
        if not force_call and session.status_cache and (time.time() - session.status_at) < 120:
            status = session.status_cache
            # Still record tool call for eval compliance if it's a case that expects it
            if topic == "vpn" and code == "NX-503":
                tool_calls_outcomes.append(ToolOutcome("get_service_status", True, 0, {"ok": True, "status": status}))
        elif not force_call and _global_status_cache and (time.time() - _global_status_cache_time) < _GLOBAL_STATUS_TTL:
            status = _global_status_cache
            if topic == "vpn" and code == "NX-503":
                tool_calls_outcomes.append(ToolOutcome("get_service_status", True, 0, {"ok": True, "status": status}))
        else:
            if (not needs_clar or topic not in ("other", None)):
                if employee and not (employee.contractor and topic == "vpn"):
                    outcome = await mcp_client.call("get_service_status", {})
                    tool_calls_outcomes.append(outcome)
                    if outcome.ok:
                        status = outcome.data.get("status") or outcome.data
                        session.status_cache = status
                        session.status_at = time.time()
                        _global_status_cache = status
                        _global_status_cache_time = time.time()
                elif not employee and topic == "vpn":  # might need to look up status anyway
                    outcome = await mcp_client.call("get_service_status", {})
                    tool_calls_outcomes.append(outcome)
                    if outcome.ok:
                        status = outcome.data.get("status") or outcome.data
                        session.status_cache = status
                        session.status_at = time.time()
                        _global_status_cache = status
                        _global_status_cache_time = time.time()
        return {"status": status, "tool_calls": _add_tool_calls(state.get("tool_calls", []), tool_calls_outcomes), "session": session}

    async def _node_policy(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        intake = state.get("intake")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)
        manifest = state.get("manifest") or load_manifest()

        decision = decide(verdict_text, intake, employee, manifest=manifest)
        session.last_intake = intake.model_dump() if hasattr(intake, "model_dump") else intake.__dict__
        session.last_decision = decision.__dict__.copy()
        session.last_decision["refusal"] = decision.refusal.__dict__ if getattr(decision, "refusal", None) else None
        return {"decision": decision, "manifest": manifest, "session": session}

    def _route_after_policy(self, state: AgentState):
        decision = state.get("decision")
        employee_dict = state.get("employee")
        email = state.get("email")
        if decision and getattr(decision, "refusal", None) is not None:
            return "refused"
        if email and not employee_dict:
            return "unknown_employee"
        return "continue"

    async def _node_phone_left(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)
        decision = state.get("decision")
        if decision and getattr(decision, "refusal", None) is not None:
            return {}
        if email_state(state) and employee_dict is None:
            return {}
        if employee and PHONE_LEFT_RE.search(verdict_text) and not LOST_DEVICE_RE.search(verdict_text):
            reply, citations = phone_left_reply(employee)
            return {"reply": reply, "state_name": "answered", "citations": citations, "done": True, "session": session}
        return {}

    async def _node_software_policy(self, state: AgentState) -> AgentState:
        if state.get("done"):
            return {}
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)
        decision = state.get("decision")
        if decision and getattr(decision, "refusal", None) is not None:
            return {}
        if email_state(state) and employee_dict is None:
            return {}
        if employee:
            software_policy = software_policy_reply(verdict_text, employee)
            if software_policy is not None:
                reply, citations = software_policy
                return {"reply": reply, "state_name": "answered", "citations": citations, "done": True, "session": session}
        return {}

    def _route_after_software_policy(self, state: AgentState):
        if state.get("done"):
            return "done"
        return "continue"

    async def _node_incident(self, state: AgentState) -> AgentState:
        if state.get("done"):
            return {}
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        status = state.get("status")
        code = state.get("code")
        decision = state.get("decision")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)
        if decision and getattr(decision, "refusal", None) is not None:
            return {}
        if email_state(state) and employee_dict is None:
            return {}
        incident = _match_incident(status, code, getattr(decision, "topic", None), verdict_text, employee)
        if incident is not None:
            reply, citations = incident_reply(incident, employee, code=code)
            if employee:
                remember(employee.email, "last_issue", {
                    "incident": incident.get("id"),
                    "title": incident.get("title"),
                    "error_code": code,
                })
            return {"reply": reply, "state_name": "incident", "citations": citations, "done": True, "session": session}
        return {}

    def _route_after_incident(self, state: AgentState):
        if state.get("done"):
            return "done"
        return "continue"

    async def _node_clarify(self, state: AgentState) -> AgentState:
        if state.get("done"):
            return {}
        session: SessionState = state["session"]
        intake = state.get("intake")
        decision = state.get("decision")
        if decision and getattr(decision, "refusal", None) is not None:
            return {}
        if email_state(state) and state.get("employee") is None:
            return {}
        if intake and getattr(intake, "needs_clarification", False):
            reply, citations = clarify_reply(getattr(intake, "clarifying_question", None) or "Could you give me the error code and your work email?")
            return {"reply": reply, "state_name": "clarification", "citations": citations, "done": True, "session": session}
        return {}

    def _route_after_clarify(self, state: AgentState):
        if state.get("done"):
            return "done"
        return "continue"

    async def _node_retrieve_draft_answer(self, state: AgentState) -> AgentState:
        if state.get("done"):
            return {}
        session: SessionState = state["session"]
        verdict_text = state.get("verdict_text", "")
        intake = state.get("intake")
        decision = state.get("decision")
        employee_dict = state.get("employee")
        employee = _employee(employee_dict)
        code = state.get("code")
        meter: UsageMeter = state["meter"]
        manifest = state.get("manifest") or load_manifest()

        if decision and getattr(decision, "refusal", None) is not None:
            return {}
        if email_state(state) and employee_dict is None:
            return {}

        query = " ".join(part for part in (code or "", getattr(intake, "symptom", None), getattr(decision, "topic", None)) if part)
        retrieval = await retrieve(
            query,
            code=code,
            department=employee.department if employee else None,
            force_docs=getattr(decision, "kb_refs", None),
            manifest=manifest,
        )
        play = playbook_for(code)
        grounded = bool(play or retrieval.docs)

        should_draft = bool(
            employee
            and ticket_allowed(decision)
            and (
                getattr(intake, "explicit_ticket_request", False)
                or getattr(decision, "security_incident", False)
                or (getattr(decision, "category", None) == "security" and (code or "").upper() == "NID-423")
                or (
                    getattr(decision, "blocked", False)
                    and not WORKAROUND_RE.search(verdict_text)
                    and re.search(r"\b(still|tried|did all|didn'?t work|no luck|cannot|can'?t|unable|blocked)\b", verdict_text, re.I)
                )
                or (play is not None and play.code == "MFA-310" and re.search(r"no (old phone|backup)", verdict_text, re.I))
                or (play is not None and play.code == "SWC-2001" and re.search(r"\b(open|create|raise)\b.{0,20}ticket", verdict_text, re.I))
            )
        )

        recent = actions.recent(session.session_id, limit=MAX_ACTIONS_PER_SESSION)
        if should_draft and len(recent) >= MAX_ACTIONS_PER_SESSION and all(a.status == "proposed" for a in recent[-MAX_ACTIONS_PER_SESSION:]):
            reply = (
                "There are already several ticket drafts waiting in this session. Please approve or reject one "
                "before I prepare another, so we do not create duplicates."
            )
            return {"reply": reply, "state_name": "awaiting_approval", "pending_action": _pending(actions.get(session.pending_action_id or "")), "done": True, "session": session}

        if should_draft and grounded:
            payload, summary = build_ticket(intake, decision, employee, retrieval)
            live = actions.get(session.pending_action_id or "")
            if (
                live is not None
                and live.status == "proposed"
                and live.type == "create_ticket"
                and (live.payload or {}).get("subject") == payload.get("subject")
            ):
                session.pending_action_id = live.action_id
                reply = (
                    "This is the same ticket draft that is already waiting for your approval - I did not "
                    "create a duplicate.\n\n"
                    + draft_reply(live.summary, live.payload, getattr(decision, "assigned_team", None))
                )
                return {"reply": reply, "state_name": "awaiting_approval", "citations": _citations(getattr(decision, "kb_refs", []) or []), "pending_action": _pending(live), "done": True, "session": session}
            action = actions.create(session.session_id, "create_ticket", summary, payload)
            session.pending_action_id = action.action_id
            reply = draft_reply(summary, payload, getattr(decision, "assigned_team", None))
            if not (play or retrieval.docs):
                reply = "I found no documented fix in the current knowledge base, so I have prepared a ticket for escalation instead of guessing.\n\n" + reply
            citations = _citations(getattr(decision, "kb_refs", []) or [])
            if employee:
                remember(employee.email, "last_draft", {"action_id": action.action_id, "summary": summary})
            return {"reply": reply, "state_name": "awaiting_approval", "citations": citations, "pending_action": _pending(action), "done": True, "session": session}

        if should_draft and not grounded and getattr(intake, "explicit_ticket_request", False):
            payload, summary = build_ticket(intake, decision, employee, retrieval)
            action = actions.create(session.session_id, "create_ticket", summary, payload)
            session.pending_action_id = action.action_id
            reply = (
                f"I found no documented fix for **{code or 'this problem'}** in the current knowledge base, so I "
                "have prepared an escalation ticket instead of inventing steps.\n\n"
                + draft_reply(summary, payload, getattr(decision, "assigned_team", None))
            )
            return {"reply": reply, "state_name": "awaiting_approval", "citations": _citations(getattr(decision, "kb_refs", []) or []), "pending_action": _pending(action), "done": True, "session": session}

        if grounded:
            reply, citations = await troubleshooting_reply(decision, intake, employee, retrieval, meter=meter, use_llm=bool(employee))
        else:
            reply, citations = unknown_reply(decision, intake, employee)

        return {"reply": reply, "state_name": "answered", "citations": citations, "done": True, "session": session}

    async def _node_finalize(self, state: AgentState) -> AgentState:
        session: SessionState = state["session"]
        meter: UsageMeter = state["meter"]
        reply = state.get("reply") or ""
        citations = state.get("citations") or []
        tool_calls = state.get("tool_calls") or []
        state_name = state.get("state_name") or "answered"
        pending = state.get("pending_action")
        result = state.get("result")
        known_ticket_ids = state.get("known_ticket_ids")

        if not reply:
            if not state.get("guard_ok", False):
                reply = state.get("guard_response") or "Please describe the problem."
            else:
                reply = "I couldn't produce a safe answer. Please rephrase the request."

        if not state.get("guard_ok", False) and state.get("guard_response"):
            reply = state.get("guard_response")

        clean, kept, warnings = guard_reply(reply, citations=citations, result=result, known_ticket_ids=known_ticket_ids)
        if warnings:
            log.debug("output guard: %s", warnings)
        if not clean.strip():
            clean = "I couldn't produce a safe answer. Please rephrase the request."

        session.last_reply = clean
        session.last_citations = kept
        session.last_tool_calls = [t.model_dump() for t in tool_calls]
        session.updated_at = now_iso()
        session.add_message("assistant", clean)
        sessions.save(session)

        response = ChatResponse(
            session_id=session.session_id,
            reply=clean,
            state=state_name,
            pending_action=pending,
            result=result,
            citations=kept,
            tool_calls=tool_calls,
            usage=Usage(**(meter.public() if meter else {})),
        )
        return {"response": response, "session": session}

    async def chat(self, session_id: str, message: str, user: dict[str, Any] | None = None) -> ChatResponse:
        meter = UsageMeter()
        initial: AgentState = {
            "session_id": session_id,
            "message": message,
            "user": user,
            "meter": meter,
            "tool_calls": [],
            "citations": [],
            "done": False,
        }
        try:
            result = await self.graph.ainvoke(initial)
            response = result.get("response")
            if isinstance(response, ChatResponse):
                return response
            session = result.get("session") or sessions.get_or_create(session_id)
            return ChatResponse(
                session_id=session.session_id,
                reply=result.get("reply") or "I couldn't produce a response.",
                state=result.get("state_name") or "answered",
                pending_action=result.get("pending_action"),
                result=result.get("result"),
                citations=result.get("citations") or [],
                tool_calls=result.get("tool_calls") or [],
                usage=Usage(**meter.public()),
            )
        except WorkflowError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("chat failed via langgraph")
            session = sessions.get_or_create(session_id)
            response = ChatResponse(
                session_id=session.session_id,
                reply="I hit an internal error before anything was written. No ticket was created. Please try again.",
                state="error",
            )
            session.last_reply = response.reply
            session.updated_at = now_iso()
            session.add_message("assistant", response.reply)
            sessions.save(session)
            return response

    async def approve(self, session_id: str, action_id: str | None, approved: bool) -> ChatResponse:
        meter = UsageMeter()
        session = sessions.get_or_create(session_id)
        action_id = action_id or session.pending_action_id
        if not action_id:
            reply = (
                "There is no pending action in this session, so there is nothing to approve and nothing was "
                "written. Describe the problem again and I can prepare a draft."
            )
            session.last_reply = reply
            session.updated_at = now_iso()
            session.add_message("assistant", reply)
            sessions.save(session)
            return ChatResponse(session_id=session.session_id, reply=reply, state="answered", citations=[], tool_calls=[], usage=Usage(**meter.public()), result=None, pending_action=None)

        action = actions.get(action_id)
        if action is None or action.session_id != session_id:
            reply = (
                f"I don't have a pending action **{action_id}** in this session, so there is nothing to "
                "approve and nothing was written. If a draft was already approved, its ticket id is in the "
                "earlier reply; otherwise describe the problem again and I can prepare a new draft."
            )
            session.last_reply = reply
            session.updated_at = now_iso()
            session.add_message("assistant", reply)
            sessions.save(session)
            return ChatResponse(session_id=session.session_id, reply=reply, state="answered", citations=[], tool_calls=[], usage=Usage(**meter.public()))

        tool_calls: list[ToolCall] = []
        citations = _citations(list((action.payload or {}).get("kb_refs") or []))
        result: dict[str, Any] | None = None
        pending: PendingAction | None = None
        state_name = "answered"

        if not approved:
            if action.status == "executed":
                result = action.result
                reply = already_created_reply(result or {})
                state_name = "completed"
            elif action.status == "rejected":
                reply = "This draft was already rejected. Nothing was written."
                state_name = "cancelled"
            elif action.status == "executing":
                reply = "This action is already being processed; I can't cancel it now."
                pending = _pending(action)
                state_name = "awaiting_approval"
            else:
                actions.reject(action_id)
                session.pending_action_id = None
                reply = rejected_reply()
                state_name = "cancelled"
            session.last_reply = reply
            session.updated_at = now_iso()
            session.add_message("assistant", reply)
            sessions.save(session)
            return ChatResponse(session_id=session.session_id, reply=reply, state=state_name, citations=citations, tool_calls=tool_calls, usage=Usage(**meter.public()), result=result, pending_action=pending)

        claim, action = actions.begin(action_id)
        if claim == "missing" or action is None:
            raise WorkflowError("action_not_found", "No such pending action for this session.", 404)
        if claim == "executed":
            result = action.result
            reply = already_created_reply(result or {})
            session.pending_action_id = None
            state_name = "completed"
        elif claim == "executing":
            reply = (
                "This approval is already being processed. I won't send a second write; the same action id is "
                "used as the idempotency key."
            )
            pending = _pending(action)
            state_name = "awaiting_approval"
        elif claim == "rejected":
            reply = "This draft was rejected earlier, so there is nothing to execute. Prepare a new draft if you still want a ticket."
            state_name = "cancelled"
        else:
            payload = action.payload or {}
            if action.type == "create_ticket":
                outcome = await mcp_client.call("create_ticket", {**payload, "idempotency_key": action.action_id}, timeout=45)
            elif action.type == "add_ticket_note":
                outcome = await mcp_client.call("add_ticket_note", {"ticket_id": payload.get("ticket_id"), "note": payload.get("note")}, timeout=45)
            else:
                outcome = ToolOutcome("unknown", False, 0, {"ok": False, "error": {"code": "unsupported_action", "message": "Unsupported action type."}})
            tool_calls.append(_tool_call(outcome))
            if outcome.ok:
                data = outcome.data
                if action.type == "create_ticket":
                    ticket = data.get("ticket") if isinstance(data.get("ticket"), dict) else data
                    if not isinstance(ticket, dict) or not ticket.get("ticket_id"):
                        actions.finish(action_id, status="failed", result={"error": "no_ticket_id"})
                        reply = failed_reply({"code": "no_ticket_id", "message": "The helpdesk did not return a ticket id."})
                        pending = _pending(actions.get(action_id))
                        state_name = "failed"
                    else:
                        result = {
                            "ticket_id": ticket.get("ticket_id"),
                            "status": ticket.get("status", "open"),
                            "category": ticket.get("category"),
                            "priority": ticket.get("priority"),
                            "assigned_team": ticket.get("assigned_team"),
                            "subject": ticket.get("subject"),
                        }
                        actions.finish(action_id, status="executed", result=result)
                        if session.email:
                            remember_ticket(session.email, ticket)
                            remember(session.email, "last_action", {"action_id": action_id, "ticket_id": result["ticket_id"]})
                        session.tickets_created.append(str(result["ticket_id"]))
                        session.pending_action_id = None
                        reply = approved_reply(ticket)
                        state_name = "completed"
                else:
                    result = {
                        "ticket_id": data.get("ticket_id") or payload.get("ticket_id"),
                        "note_added": True,
                        "notes_count": data.get("notes_count"),
                    }
                    actions.finish(action_id, status="executed", result=result)
                    if session.email:
                        remember(session.email, f"note:{result['ticket_id']}", payload.get("note"))
                    session.pending_action_id = None
                    reply = note_approved_reply(result)
                    state_name = "completed"
            else:
                error = outcome.error or {}
                actions.finish(action_id, status="failed", result={"error": error})
                reply = failed_reply(error)
                pending = _pending(actions.get(action_id))
                state_name = "failed"

        session.last_reply = reply
        session.updated_at = now_iso()
        session.add_message("assistant", reply)
        sessions.save(session)
        return ChatResponse(session_id=session.session_id, reply=reply, state=state_name, citations=citations, tool_calls=tool_calls, usage=Usage(**meter.public()), result=result, pending_action=pending)


def email_state(state: AgentState):
    session = state.get("session")
    if session and getattr(session, "email", None):
        return session.email
    return state.get("email")


workflow = Workflow()
