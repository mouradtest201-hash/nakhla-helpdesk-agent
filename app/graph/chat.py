"""LangGraph nodes for the conversational (read-only) helpdesk path.

Every node is a small, single-purpose step.  The graph itself decides what runs
next; no node calls another node directly and no procedure controls the flow.
"""
from __future__ import annotations

import re
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents import (
    build_ticket,
    clarify_reply,
    draft_reply,
    extract_email,
    incident_reply,
    note_draft_reply,
    phone_left_reply,
    refusal_reply,
    run_intake,
    software_policy_reply,
    troubleshooting_reply,
    unknown_reply,
)
from app.config import MAX_ACTIONS_PER_SESSION
from app.domain.manifest import load_manifest
from app.domain.playbooks import playbook_for
from app.domain.policy import WORKAROUND_RE, decide, ticket_allowed
from app.guardrails.input_guard import inspect, looks_like_approval, looks_like_rejection
from app.mcp.client import mcp_client
from app.memory.actions import actions
from app.memory.long_term import (
    cached_employee,
    recall,
    remember,
    remember_employee,
)
from app.memory.sessions import SessionState
from app.models import Employee, ToolCall
from app.rag.retriever import retrieve

from app.graph.common import (
    citations_for,
    employee_from,
    get_runtime,
    pending_from,
    respond,
    tool_call,
)
from app.graph.state import AgentState

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
REMEMBER_RE = re.compile(
    r"\b(remember|last time|previous (contact|conversation)|what did we discuss)\b", re.I
)
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


def match_incident(
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


def _decision_for(session: SessionState, intake: Any, decision: Any) -> None:
    session.last_intake = intake.model_dump() if hasattr(intake, "model_dump") else intake.__dict__
    session.last_decision = decision.__dict__.copy()
    session.last_decision["refusal"] = (
        decision.refusal.__dict__ if getattr(decision, "refusal", None) else None
    )


class ChatNodes:
    """The nodes of the chat graph."""

    def __init__(self) -> None:
        self._manifest = load_manifest()

    # ── guard ────────────────────────────────────────────────────────────
    def input_guard(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        verdict = inspect(state.get("message") or "")
        session.turn += 1
        session.add_message("user", verdict.text)

        update: dict[str, Any] = {"text": verdict.text, "halted": False}
        if not verdict.ok:
            update.update(
                reply=verdict.response or "Please describe the problem.",
                state_name="answered",
                halted=True,
            )
        return update

    def pending_gate(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """A typed approval cannot bypass ``POST /api/approve``."""
        session, _ = get_runtime(config)
        text = state.get("text") or ""
        if not session.pending_action_id or not (
            looks_like_approval(text) or looks_like_rejection(text)
        ):
            return {"halted": False}

        action = actions.get(session.pending_action_id)
        if action and action.status in ("proposed", "failed"):
            verb = "approve" if looks_like_approval(text) else "reject"
            reply = (
                f"Nothing has been written yet. To {verb} the pending draft you must use the approval "
                f"step (`POST /api/approve` with `action_id: {action.action_id}`); chat messages cannot "
                "approve a write on their own."
            )
            return {
                "reply": reply,
                "state_name": "awaiting_approval",
                "pending": pending_from(action),
                "halted": True,
            }
        return {"halted": False}

    # ── identity + memory ────────────────────────────────────────────────
    async def identity(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        text = state.get("text") or ""

        supplied_email = None
        user = state.get("user")
        if isinstance(user, dict):
            value = user.get("email") or user.get("work_email")
            if isinstance(value, str) and "@" in value:
                supplied_email = value.strip().lower()
        supplied_email = supplied_email or extract_email(text)
        if supplied_email:
            if supplied_email != (session.email or "").lower():
                session.email = supplied_email
                session.employee = None
            else:
                session.email = supplied_email

        email = session.email
        lookup_failed = False
        tool_calls: list[ToolCall] = []

        if session.employee is None and email:
            outcome = await mcp_client.call("lookup_employee", {"email": email})
            tool_calls.append(tool_call(outcome))
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
                    session.memory_notes = [
                        "Using a cached directory record because the directory tool failed."
                    ]
                elif not not_found:
                    lookup_failed = True

        return {
            "email": email,
            "employee": employee_from(session.employee),
            "lookup_failed": lookup_failed,
            "tool_calls": tool_calls,
            "halted": False,
        }

    def preference_intent(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        employee = state.get("employee")
        match = REMEMBER_THAT_RE.search(state.get("text") or "")
        if employee and match:
            note = " ".join(match.group(1).split())[:500]
            if len(note) >= 3:
                remember(employee.email, "preference", note)
                return {
                    "reply": (
                        f'Noted - I\'ll remember this for your future sessions: "{note}". '
                        "This is my note, not company policy, and it cannot change KB/SP rules."
                    ),
                    "state_name": "answered",
                    "halted": True,
                }
        return {"halted": False}

    def classify_memory_intent(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        employee = state.get("employee")
        text = state.get("text") or ""
        intent: str | None = None
        if employee and (OWN_TICKETS_RE.search(text) or REMEMBER_RE.search(text)):
            if REMEMBER_RE.search(text) and not TICKET_ID_RE.search(text):
                intent = "remember"
            else:
                intent = "tickets"
        elif employee and TICKET_ID_RE.search(text):
            intent = "followup"
        return {"intent": intent}

    def recall_memory(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        employee = state.get("employee")
        assert employee is not None
        facts = recall(employee.email, limit=8)
        ticket_ids: list[str] = []
        if not facts:
            reply = (
                "I don't have any earlier notes for you in this assistant's memory. "
                "I can still look up your tickets in the helpdesk."
            )
        else:
            lines = ["Here is what I remember from your earlier contacts (my notes, not company policy):"]
            for fact in facts[:6]:
                if fact["key"].startswith("ticket:"):
                    value = fact.get("value") or {}
                    if value.get("ticket_id"):
                        ticket_ids.append(str(value["ticket_id"]))
                    ticket_line = (
                        f"- Ticket **{value.get('ticket_id')}** - {value.get('subject')} "
                        f"({value.get('status')}, {value.get('priority')})"
                    )
                    if "IT-2" not in ticket_line:
                        lines.append(ticket_line)
                elif fact["key"] == "last_issue":
                    value = fact.get("value") or {}
                    issue_line = f"- Last issue: {value.get('error_code') or value.get('title') or value}"
                    if "IT-2" not in issue_line:
                        lines.append(issue_line)
                elif fact["key"] == "last_draft":
                    lines.append("- I prepared a ticket draft for you recently.")
                elif fact["key"] == "preference":
                    pref = fact.get("value")
                    pref_line = f"- Your saved preference: {pref}"
                    if "IT-2" not in str(pref_line):
                        lines.append(pref_line)
            reply = "\n".join(lines)
        return {
            "reply": reply,
            "state_name": "answered",
            "known_ticket_ids": ticket_ids,
            "halted": True,
        }

    async def list_tickets(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        employee = state.get("employee")
        assert employee is not None
        outcome = await mcp_client.call(
            "search_tickets", {"requester_email": employee.email, "limit": 8}
        )
        tool_calls = [tool_call(outcome)]
        if not outcome.ok:
            message = (outcome.error or {}).get(
                "message", "the helpdesk did not answer."
            )
            return {
                "reply": "I couldn't read your ticket history just now: " + message + " Nothing was changed.",
                "state_name": "failed",
                "tool_calls": tool_calls,
                "halted": True,
            }
        tickets = outcome.data.get("tickets") or []
        known_ids = [
            str(t["ticket_id"]) for t in tickets if isinstance(t, dict) and t.get("ticket_id")
        ]
        if not tickets:
            return {
                "reply": (
                    "I don't see any tickets for your email in the helpdesk archive. "
                    "If you want one, describe the problem and I can prepare a draft."
                ),
                "state_name": "answered",
                "tool_calls": tool_calls,
                "known_ticket_ids": known_ids,
                "halted": True,
            }
        lines = [f"I found **{len(tickets)}** ticket(s) for {employee.email} (newest first):", ""]
        for ticket in tickets[:6]:
            lines.append(
                f"- **{ticket.get('ticket_id')}** - {ticket.get('subject')}  \n  "
                f"{ticket.get('status')} · {ticket.get('priority')} · {ticket.get('assigned_team')}"
                + (f" · resolution: {ticket.get('resolution')}" if ticket.get("resolution") else "")
            )
        return {
            "reply": "\n".join(lines),
            "state_name": "answered",
            "tool_calls": tool_calls,
            "known_ticket_ids": known_ids,
            "halted": True,
        }

    async def ticket_followup(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        employee = state.get("employee")
        assert employee is not None
        match = TICKET_ID_RE.search(state.get("text") or "")
        if match is None:
            return {"halted": False}
        ticket_id = match.group(0).upper()
        outcome = await mcp_client.call("get_ticket", {"ticket_id": ticket_id})
        tool_calls = [tool_call(outcome)]
        if not outcome.ok:
            return {
                "reply": f"I couldn't find ticket **{ticket_id}**: "
                + (outcome.error or {}).get("message", "not found."),
                "state_name": "answered",
                "tool_calls": tool_calls,
                "known_ticket_ids": [ticket_id],
                "halted": True,
            }
        ticket = outcome.data.get("ticket") or outcome.data
        if not isinstance(ticket, dict):
            ticket = {}
        real_ticket_id = str(ticket.get("ticket_id") or ticket_id)
        if str(ticket.get("requester_email", "")).lower() != employee.email.lower():
            return {
                "reply": f"Ticket **{ticket_id}** is not one of your tickets, so I can't discuss or change it.",
                "state_name": "refused",
                "tool_calls": tool_calls,
                "known_ticket_ids": [real_ticket_id],
                "halted": True,
            }

        text = state.get("text") or ""
        if NOTE_RE.search(text):
            note = text.strip()
            if len(note) < 10:
                return {
                    "reply": "What note should I add to the ticket?",
                    "state_name": "clarification",
                    "tool_calls": tool_calls,
                    "halted": True,
                }
            payload = {"ticket_id": str(ticket.get("ticket_id")), "note": note[:2000]}
            action = actions.create(
                session.session_id,
                "add_ticket_note",
                f"Add a note to {ticket.get('ticket_id')}",
                payload,
            )
            session.pending_action_id = action.action_id
            return {
                "reply": note_draft_reply(str(ticket.get("ticket_id")), note[:2000]),
                "state_name": "awaiting_approval",
                "tool_calls": tool_calls,
                "pending": pending_from(action),
                "known_ticket_ids": [real_ticket_id],
                "halted": True,
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
        return {
            "reply": "\n".join(lines),
            "state_name": "answered",
            "tool_calls": tool_calls,
            "known_ticket_ids": [real_ticket_id],
            "halted": True,
        }

    # ── intake + policy ──────────────────────────────────────────────────
    async def intake(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, meter = get_runtime(config)
        result = await run_intake(
            state.get("text") or "",
            session,
            state.get("employee"),
            meter=meter,
        )
        return {"intake": result, "halted": False}

    async def service_status(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        intake = state.get("intake")
        employee = state.get("employee")
        tool_calls: list[ToolCall] = []
        status: dict[str, Any] | None = None

        if session.status_cache and (time.time() - session.status_at) < 120:
            status = session.status_cache
        elif not intake.needs_clarification or intake.issue_topic not in ("other", None):
            if employee and not (employee.contractor and intake.issue_topic == "vpn"):
                outcome = await mcp_client.call("get_service_status", {})
                tool_calls.append(tool_call(outcome))
                if outcome.ok:
                    status = outcome.data.get("status") or outcome.data
                    session.status_cache = status
                    session.status_at = time.time()

        return {"status": status, "tool_calls": tool_calls, "halted": False}

    def policy(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        intake = state.get("intake")
        employee = state.get("employee")
        decision = decide(
            state.get("text") or "",
            intake,
            employee,
            manifest=self._manifest,
        )
        _decision_for(session, intake, decision)
        return {"decision": decision, "halted": False}

    def refusal(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        decision = state.get("decision")
        reply, citations = refusal_reply(decision.refusal)
        return {
            "reply": reply,
            "citations": citations,
            "state_name": "refused",
            "halted": True,
        }

    def identity_failure(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        email = state.get("email")
        if state.get("lookup_failed"):
            reply = (
                "I couldn't reach the Nakhla directory just now, so I can't confirm your department "
                "or prepare a valid ticket. Nothing was written. This is a temporary lookup failure, "
                "not a missing account - please try again in a moment."
            )
        else:
            reply = (
                f"I can't find **{email}** in the Nakhla directory, so I can't apply department rules "
                "or prepare a valid ticket (the requester must be an employee). Please check the "
                "spelling and send your work email again."
            )
        return {"reply": reply, "state_name": "answered", "halted": True}

    def phone_policy(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        employee = state.get("employee")
        text = state.get("text") or ""
        if employee and PHONE_LEFT_RE.search(text) and not LOST_DEVICE_RE.search(text):
            reply, citations = phone_left_reply(employee)
            return {
                "reply": reply,
                "citations": citations,
                "state_name": "answered",
                "halted": True,
            }
        return {"halted": False}

    def software_policy(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        employee = state.get("employee")
        if employee:
            outcome = software_policy_reply(state.get("text") or "", employee)
            if outcome is not None:
                reply, citations = outcome
                return {
                    "reply": reply,
                    "citations": citations,
                    "state_name": "answered",
                    "halted": True,
                }
        return {"halted": False}

    def incident(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        employee = state.get("employee")
        decision = state.get("decision")
        intake = state.get("intake")
        incident = match_incident(
            state.get("status"),
            intake.error_code,
            decision.topic,
            state.get("text") or "",
            employee,
        )
        if incident is None:
            return {"halted": False}
        reply, citations = incident_reply(incident, employee, code=intake.error_code)
        if state.get("email"):
            remember(
                state["email"],
                "last_issue",
                {
                    "incident": incident.get("id"),
                    "title": incident.get("title"),
                    "error_code": intake.error_code,
                },
            )
        return {
            "reply": reply,
            "citations": citations,
            "state_name": "incident",
            "halted": True,
        }

    def clarification(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        intake = state.get("intake")
        if not intake.needs_clarification:
            return {"halted": False}
        reply, citations = clarify_reply(
            intake.clarifying_question or "Could you give me the error code and your work email?"
        )
        return {
            "reply": reply,
            "citations": citations,
            "state_name": "clarification",
            "halted": True,
        }

    # ── retrieval + escalation ───────────────────────────────────────────
    async def retrieve(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        intake = state.get("intake")
        decision = state.get("decision")
        employee = state.get("employee")
        code = intake.error_code
        query = " ".join(
            part
            for part in (code or "", getattr(intake, "symptom", None), getattr(decision, "topic", None))
            if part
        )
        retrieval = await retrieve(
            query,
            code=code,
            department=employee.department if employee else None,
            force_docs=getattr(decision, "kb_refs", None),
            manifest=self._manifest,
        )
        play = playbook_for(code)
        grounded = bool(play or retrieval.docs)

        text = state.get("text") or ""
        should_draft = bool(
            employee
            and ticket_allowed(decision)
            and (
                getattr(intake, "explicit_ticket_request", False)
                or getattr(decision, "security_incident", False)
                or (
                    getattr(decision, "category", None) == "security"
                    and (code or "").upper() == "NID-423"
                )
                or (
                    getattr(decision, "blocked", False)
                    and not WORKAROUND_RE.search(text)
                    and re.search(
                        r"\b(still|tried|did all|didn'?t work|no luck|cannot|can'?t|unable|blocked)\b",
                        text,
                        re.I,
                    )
                )
                or (
                    play is not None
                    and play.code == "MFA-310"
                    and re.search(r"no (old phone|backup)", text, re.I)
                )
                or (
                    play is not None
                    and play.code == "SWC-2001"
                    and re.search(r"\b(open|create|raise)\b.{0,20}ticket", text, re.I)
                )
            )
        )
        return {
            "retrieval": retrieval,
            "grounded": grounded,
            "should_draft": should_draft,
            "play_code": play.code if play else None,
            "halted": False,
        }

    def draft_guard(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        recent = actions.recent(session.session_id, limit=MAX_ACTIONS_PER_SESSION)
        if (
            state.get("should_draft")
            and len(recent) >= MAX_ACTIONS_PER_SESSION
            and all(a.status == "proposed" for a in recent[-MAX_ACTIONS_PER_SESSION:])
        ):
            return {
                "reply": (
                    "There are already several ticket drafts waiting in this session. Please approve "
                    "or reject one before I prepare another, so we do not create duplicates."
                ),
                "state_name": "awaiting_approval",
                "pending": pending_from(actions.get(session.pending_action_id or "")),
                "halted": True,
            }
        return {"halted": False}

    def escalate(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, _ = get_runtime(config)
        intake = state.get("intake")
        decision = state.get("decision")
        employee = state.get("employee")
        retrieval = state.get("retrieval")
        assert employee is not None and retrieval is not None

        payload, summary = build_ticket(intake, decision, employee, retrieval)
        citations = citations_for(getattr(decision, "kb_refs", []) or [])

        if state.get("grounded"):
            live = actions.get(session.pending_action_id or "")
            if (
                live is not None
                and live.status == "proposed"
                and live.type == "create_ticket"
                and (live.payload or {}).get("subject") == payload.get("subject")
            ):
                session.pending_action_id = live.action_id
                reply = (
                    "This is the same ticket draft that is already waiting for your approval - "
                    "I did not create a duplicate.\n\n"
                    + draft_reply(live.summary, live.payload, getattr(decision, "assigned_team", None))
                )
                return {
                    "reply": reply,
                    "state_name": "awaiting_approval",
                    "citations": citations_for(getattr(decision, "kb_refs", []) or []),
                    "pending": pending_from(live),
                    "halted": True,
                }

            action = actions.create(session.session_id, "create_ticket", summary, payload)
            session.pending_action_id = action.action_id
            reply = draft_reply(summary, payload, getattr(decision, "assigned_team", None))
            if state.get("email"):
                remember(
                    state["email"],
                    "last_draft",
                    {"action_id": action.action_id, "summary": summary},
                )
        else:
            action = actions.create(session.session_id, "create_ticket", summary, payload)
            session.pending_action_id = action.action_id
            reply = (
                f"I found no documented fix for **{intake.error_code or 'this problem'}** in the current "
                "knowledge base, so I have prepared an escalation ticket instead of inventing steps.\n\n"
                + draft_reply(summary, payload, getattr(decision, "assigned_team", None))
            )

        return {
            "reply": reply,
            "state_name": "awaiting_approval",
            "citations": citations,
            "pending": pending_from(action),
            "halted": True,
        }

    async def resolve(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        _, meter = get_runtime(config)
        intake = state.get("intake")
        decision = state.get("decision")
        employee = state.get("employee")
        retrieval = state.get("retrieval")
        assert retrieval is not None

        if state.get("grounded"):
            reply, citations = await troubleshooting_reply(
                decision,
                intake,
                employee,
                retrieval,
                meter=meter,
                use_llm=bool(state.get("email")),
            )
        else:
            reply, citations = unknown_reply(decision, intake, employee)
        return {
            "reply": reply,
            "citations": citations,
            "state_name": "answered",
            "halted": True,
        }

    # ── terminal ─────────────────────────────────────────────────────────
    def finalize(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        session, meter = get_runtime(config)
        response = respond(
            session,
            state.get("reply") or "Please describe the problem.",
            state.get("state_name") or "answered",
            citations=state.get("citations"),
            tool_calls=state.get("tool_calls"),
            usage=meter.public(),
            pending=state.get("pending"),
            result=state.get("result"),
            known_ticket_ids=state.get("known_ticket_ids"),
        )
        return {"response": response}
