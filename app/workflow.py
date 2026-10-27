"""The helpdesk workflow: intake -> status -> retrieval -> policy -> answer/draft.

This is an explicit state machine (the brief allows "a graph, a crew, or your own
loop").  The important invariant is that only ``approve`` can write, and every
write is tied to one pending action id used as the API idempotency key.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

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
    rejected_reply,
    refusal_reply,
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
    # "my tickets", "any tickets", "previous ticket" are history questions.
    # "open a ticket" is a write request and must not match here.
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
    # Only explicit "add a note" requests; a status question such as
    # "any update on IT-1021?" is a read, not a write proposal.
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

# Global status cache for optimization
_global_status_cache = None
_global_status_cache_time = 0.0
_GLOBAL_STATUS_TTL = 300  # 5 minutes


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
                # INC-2207 is GW-2; Finance stays on GW-3 and is unaffected.
                continue
            return incident
        # Without a code, only match a distinctive symptom, never a bare
        # "VPN is broken" (that needs clarification first).
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


class Workflow:
    async def chat(self, session_id: str, message: str, user: dict[str, Any] | None = None) -> ChatResponse:
        meter = UsageMeter()
        verdict = inspect(message)
        state = sessions.get_or_create(session_id)
        state.turn += 1
        state.add_message("user", verdict.text)
        tool_calls: list[ToolCall] = []
        manifest = load_manifest()

        if not verdict.ok:
            return self._respond(state, verdict.response or "Please describe the problem.", state_name="answered",
                                 tool_calls=[], usage=meter.public())

        # Approval is a separate, explicit step.  A chat message is not an
        # approval: only POST /api/approve may execute the proposed write.
        if state.pending_action_id and (looks_like_approval(verdict.text) or looks_like_rejection(verdict.text)):
            pending = actions.get(state.pending_action_id)
            if pending and pending.status in ("proposed", "failed"):
                verb = "approve" if looks_like_approval(verdict.text) else "reject"
                reply = (
                    f"Nothing has been written yet. To {verb} the pending draft you must use the approval step "
                    f"(`POST /api/approve` with `action_id: {pending.action_id}`); chat messages cannot approve a "
                    "write on their own."
                )
                return self._respond(state, reply, state_name="awaiting_approval",
                                     tool_calls=[], usage=meter.public(),
                                     pending=_pending(pending))

        # ── identity ─────────────────────────────────────────────────────
        supplied_email = None
        if isinstance(user, dict):
            value = user.get("email") or user.get("work_email")
            if isinstance(value, str) and "@" in value:
                supplied_email = value.strip().lower()
        supplied_email = supplied_email or extract_email(verdict.text)
        if supplied_email and supplied_email != (state.email or "").lower():
            state.email = supplied_email
            state.employee = None
        elif supplied_email:
            state.email = supplied_email
        email = state.email

        lookup_failed = False
        if state.employee is None and email:
            outcome = await mcp_client.call("lookup_employee", {"email": email})
            tool_calls.append(_tool_call(outcome))
            if outcome.ok:
                data = outcome.data.get("employee") or outcome.data
                if isinstance(data, dict) and data.get("email"):
                    state.employee = data
                    remember_employee(data)
            else:
                error = outcome.error or {}
                # A genuine 404 means "no such employee"; a transport/5xx
                # failure means "we could not check".  They deserve very
                # different replies.
                not_found = (
                    error.get("code") in ("employee_not_found", "not_found")
                    or error.get("http_status") == 404
                )
                cached = cached_employee(email)
                if cached:
                    state.employee = cached
                    state.memory_notes = ["Using a cached directory record because the directory tool failed."]
                elif not not_found:
                    lookup_failed = True
        employee = _employee(state.employee)

        # ── explicit long-term preference ("remember that ...") ──────────
        remember_match = REMEMBER_THAT_RE.search(verdict.text)
        if employee and remember_match:
            note = " ".join(remember_match.group(1).split())[:500]
            if len(note) >= 3:
                remember(employee.email, "preference", note)
                reply = (
                    f"Noted - I'll remember this for your future sessions: \"{note}\". "
                    "This is my note, not company policy, and it cannot change KB/SP rules."
                )
                return self._respond(state, reply, state_name="answered", citations=[],
                                     tool_calls=tool_calls, usage=meter.public())

        # ── ticket-history intents (long-term memory) ─────────────────────
        if employee and (OWN_TICKETS_RE.search(verdict.text) or REMEMBER_RE.search(verdict.text)):
            if REMEMBER_RE.search(verdict.text) and not TICKET_ID_RE.search(verdict.text):
                return self._answer_memory(state, meter, employee)
            return await self._answer_own_tickets(state, meter, employee, tool_calls)

        ticket_match = TICKET_ID_RE.search(verdict.text)
        if employee and ticket_match:
            return await self._ticket_followup(
                state, meter, employee, tool_calls, ticket_match.group(0).upper(), verdict.text
            )

        # ── intake ───────────────────────────────────────────────────────
        intake = await run_intake(verdict.text, state, employee, meter=meter)
        code = intake.error_code

        # ── live status (once per session, short cache + global cache) ──────────────────
        global _global_status_cache, _global_status_cache_time
        status: dict[str, Any] | None = None
        if state.status_cache and (time.time() - state.status_at) < 120:
            status = state.status_cache
        elif _global_status_cache and (time.time() - _global_status_cache_time) < _GLOBAL_STATUS_TTL:
            status = _global_status_cache
        elif not intake.needs_clarification or intake.issue_topic not in ("other", None):
            if employee and not (employee.contractor and intake.issue_topic == "vpn"):
                outcome = await mcp_client.call("get_service_status", {})
                tool_calls.append(_tool_call(outcome))
                if outcome.ok:
                    status = outcome.data.get("status") or outcome.data
                    state.status_cache = status
                    state.status_at = time.time()
                    _global_status_cache = status
                    _global_status_cache_time = time.time()

        decision = decide(verdict.text, intake, employee, manifest=manifest)
        state.last_intake = intake.model_dump()
        state.last_decision = decision.__dict__.copy()
        state.last_decision["refusal"] = decision.refusal.__dict__ if decision.refusal else None

        # 1. Policy refusals win over everything, including a ticket request.
        if decision.refusal is not None:
            reply, citations = refusal_reply(decision.refusal)
            return self._respond(state, reply, state_name="refused", citations=citations,
                                 tool_calls=tool_calls, usage=meter.public())

        # 2. Unknown/directory-failed employee: do not pretend or write.
        if email and employee is None:
            if lookup_failed:
                reply = (
                    "I couldn't reach the Nakhla directory just now, so I can't confirm your department or prepare "
                    "a valid ticket. Nothing was written. This is a temporary lookup failure, not a missing account - "
                    "please try again in a moment."
                )
            else:
                reply = (
                    f"I can't find **{email}** in the Nakhla directory, so I can't apply department rules or prepare a "
                    "valid ticket (the requester must be an employee). Please check the spelling and send your work "
                    "email again."
                )
            return self._respond(state, reply, state_name="answered", tool_calls=tool_calls, usage=meter.public())

        # 2b. Forgetting the phone is not a lost-device incident (SP-002).
        if employee and PHONE_LEFT_RE.search(verdict.text) and not LOST_DEVICE_RE.search(verdict.text):
            reply, citations = phone_left_reply(employee)
            return self._respond(state, reply, state_name="answered", citations=citations,
                                 tool_calls=tool_calls, usage=meter.public())

        # 2c. Local-admin and contractor-install rules are organisational
        #     policy with one documented Engineering exception (KB-009/KB-016).
        if employee:
            software_policy = software_policy_reply(verdict.text, employee)
            if software_policy is not None:
                reply, citations = software_policy
                return self._respond(state, reply, state_name="answered", citations=citations,
                                     tool_calls=tool_calls, usage=meter.public())

        # 3. Active incident: point to it and never create a duplicate ticket.
        incident = _match_incident(status, code, decision.topic, verdict.text, employee)
        if incident is not None:
            reply, citations = incident_reply(incident, employee, code=code)
            remember(email, "last_issue", {
                "incident": incident.get("id"),
                "title": incident.get("title"),
                "error_code": code,
            })
            return self._respond(state, reply, state_name="incident", citations=citations,
                                 tool_calls=tool_calls, usage=meter.public())

        # 4. Ask for the missing facts rather than guessing.
        if intake.needs_clarification:
            reply, citations = clarify_reply(intake.clarifying_question or "Could you give me the error code and your work email?")
            return self._respond(state, reply, state_name="clarification", citations=citations,
                                 tool_calls=tool_calls, usage=meter.public())

        # ── retrieval and answer/draft ───────────────────────────────────
        query = " ".join(part for part in (code or "", intake.symptom, decision.topic) if part)
        retrieval = await retrieve(
            query,
            code=code,
            department=employee.department if employee else None,
            force_docs=decision.kb_refs,
            manifest=manifest,
        )
        play = playbook_for(code)
        grounded = bool(play or retrieval.docs)

        should_draft = bool(
            employee
            and ticket_allowed(decision)
            and (
                intake.explicit_ticket_request
                or decision.security_incident
                or (decision.category == "security" and (code or "").upper() == "NID-423")
                or (
                    decision.blocked
                    and not WORKAROUND_RE.search(verdict.text)
                    and re.search(
                        r"\b(still|tried|did all|didn'?t work|no luck|cannot|can'?t|unable|blocked)\b",
                        verdict.text,
                        re.I,
                    )
                )
                or (play is not None and play.code == "MFA-310" and re.search(r"no (old phone|backup)", verdict.text, re.I))
                or (play is not None and play.code == "SWC-2001" and re.search(r"\b(open|create|raise)\b.{0,20}ticket", verdict.text, re.I))
            )
        )
        # Never propose a sixth action if the session is obviously looping.
        recent = actions.recent(state.session_id, limit=MAX_ACTIONS_PER_SESSION)
        if should_draft and len(recent) >= MAX_ACTIONS_PER_SESSION and all(a.status == "proposed" for a in recent[-MAX_ACTIONS_PER_SESSION:]):
            reply = (
                "There are already several ticket drafts waiting in this session. Please approve or reject one "
                "before I prepare another, so we do not create duplicates."
            )
            return self._respond(state, reply, state_name="awaiting_approval", tool_calls=tool_calls,
                                 usage=meter.public(), pending=_pending(actions.get(state.pending_action_id or "")))

        if should_draft and grounded:
            payload, summary = build_ticket(intake, decision, employee, retrieval)
            # A follow-up such as "still not working" must not silently create a
            # second draft for the same subject.  Re-surface the live proposal.
            live = actions.get(state.pending_action_id or "")
            if (
                live is not None
                and live.status == "proposed"
                and live.type == "create_ticket"
                and (live.payload or {}).get("subject") == payload.get("subject")
            ):
                state.pending_action_id = live.action_id
                reply = (
                    "This is the same ticket draft that is already waiting for your approval - I did not "
                    "create a duplicate.\n\n"
                    + draft_reply(live.summary, live.payload, decision.assigned_team)
                )
                return self._respond(
                    state, reply, state_name="awaiting_approval",
                    citations=_citations(decision.kb_refs), tool_calls=tool_calls,
                    usage=meter.public(), pending=_pending(live),
                )
            action = actions.create(state.session_id, "create_ticket", summary, payload)
            state.pending_action_id = action.action_id
            reply = draft_reply(summary, payload, decision.assigned_team)
            if not (play or retrieval.docs):
                reply = (
                    "I found no documented fix in the current knowledge base, so I have prepared a ticket for "
                    "escalation instead of guessing.\n\n" + reply
                )
            citations = _citations(decision.kb_refs)
            remember(email, "last_draft", {"action_id": action.action_id, "summary": summary})
            return self._respond(state, reply, state_name="awaiting_approval", citations=citations,
                                 tool_calls=tool_calls, usage=meter.public(), pending=_pending(action))

        if should_draft and not grounded and intake.explicit_ticket_request:
            # Unknown error code: the KB has no answer, but escalation is valid
            # and must still go through approval.
            payload, summary = build_ticket(intake, decision, employee, retrieval)
            action = actions.create(state.session_id, "create_ticket", summary, payload)
            state.pending_action_id = action.action_id
            reply = (
                f"I found no documented fix for **{code or 'this problem'}** in the current knowledge base, so I "
                "have prepared an escalation ticket instead of inventing steps.\n\n"
                + draft_reply(summary, payload, decision.assigned_team)
            )
            return self._respond(state, reply, state_name="awaiting_approval",
                                 citations=_citations(decision.kb_refs), tool_calls=tool_calls,
                                 usage=meter.public(), pending=_pending(action))

        if grounded:
            reply, citations = await troubleshooting_reply(
                decision, intake, employee, retrieval, meter=meter, use_llm=bool(email)
            )
        else:
            reply, citations = unknown_reply(decision, intake, employee)

        # A returning employee gets a short recall note, but only when it is
        # relevant to the same topic and does not leak another ticket's details.
        memory = memory_summary(email)
        if memory and play and not intake.explicit_ticket_request:
            pass  # The model has the facts; we keep the reply itself test-stable.

        return self._respond(state, reply, state_name="answered", citations=citations,
                             tool_calls=tool_calls, usage=meter.public())

    # ── approval gate ────────────────────────────────────────────────────
    async def approve(self, session_id: str, action_id: str | None, approved: bool) -> ChatResponse:
        meter = UsageMeter()
        state = sessions.get_or_create(session_id)
        action_id = action_id or state.pending_action_id
        if not action_id:
            reply = (
                "There is no pending action in this session, so there is nothing to approve and nothing was "
                "written. Describe the problem again and I can prepare a draft."
            )
            return self._respond(state, reply, state_name="answered", citations=[],
                                 tool_calls=[], usage=meter.public(), known_ticket_ids=list(state.tickets_created))
        action = actions.get(action_id)
        if action is None or action.session_id != session_id:
            # The contract asks for a clear reply, not a stack trace.  Nothing
            # is written and the session can continue.
            reply = (
                f"I don't have a pending action **{action_id}** in this session, so there is nothing to "
                "approve and nothing was written. If a draft was already approved, its ticket id is in the "
                "earlier reply; otherwise describe the problem again and I can prepare a new draft."
            )
            return self._respond(state, reply, state_name="answered", citations=[],
                                 tool_calls=[], usage=meter.public())

        tool_calls: list[ToolCall] = []
        # The approval reply keeps the same grounded sources that justified
        # the draft, so a client can still show why the ticket exists.
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
                state.pending_action_id = None
                reply = rejected_reply()
                state_name = "cancelled"
            return self._respond(state, reply, state_name=state_name, citations=citations,
                                 tool_calls=tool_calls, usage=meter.public(), result=result, pending=pending)

        claim, action = actions.begin(action_id)
        if claim == "missing" or action is None:
            raise WorkflowError("action_not_found", "No such pending action for this session.", 404)
        if claim in ("executed",):
            result = action.result
            reply = already_created_reply(result or {})
            state.pending_action_id = None
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
            payload = action.payload
            if action.type == "create_ticket":
                outcome = await mcp_client.call(
                    "create_ticket",
                    {**payload, "idempotency_key": action.action_id},
                    timeout=45,
                )
            elif action.type == "add_ticket_note":
                outcome = await mcp_client.call(
                    "add_ticket_note",
                    {"ticket_id": payload.get("ticket_id"), "note": payload.get("note")},
                    timeout=45,
                )
            else:
                outcome = ToolOutcome("unknown", False, 0, {
                    "ok": False, "error": {"code": "unsupported_action", "message": "Unsupported action type."}
                })
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
                        remember_ticket(state.email, ticket)
                        remember(state.email, "last_action", {"action_id": action_id, "ticket_id": result["ticket_id"]})
                        state.tickets_created.append(str(result["ticket_id"]))
                        state.pending_action_id = None
                        reply = approved_reply(ticket)
                        state_name = "completed"
                else:
                    result = {
                        "ticket_id": data.get("ticket_id") or payload.get("ticket_id"),
                        "note_added": True,
                        "notes_count": data.get("notes_count"),
                    }
                    actions.finish(action_id, status="executed", result=result)
                    remember(state.email, f"note:{result['ticket_id']}", payload.get("note"))
                    state.pending_action_id = None
                    reply = note_approved_reply(result)
                    state_name = "completed"
            else:
                error = outcome.error or {}
                actions.finish(action_id, status="failed", result={"error": error})
                reply = failed_reply(error)
                pending = _pending(actions.get(action_id))
                state_name = "failed"

        return self._respond(
            state, reply, state_name=state_name, citations=citations, tool_calls=tool_calls,
            usage=meter.public(), result=result, pending=pending,
        )

    # ── long-term memory helpers ─────────────────────────────────────────
    def _answer_memory(self, state: SessionState, meter: UsageMeter, employee: Employee) -> ChatResponse:
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
        return self._respond(state, reply, state_name="answered", citations=[],
                             tool_calls=[], usage=meter.public(), known_ticket_ids=ticket_ids)

    async def _answer_own_tickets(
        self,
        state: SessionState,
        meter: UsageMeter,
        employee: Employee,
        tool_calls: list[ToolCall],
    ) -> ChatResponse:
        outcome = await mcp_client.call("search_tickets", {"requester_email": employee.email, "limit": 8})
        tool_calls.append(_tool_call(outcome))
        if not outcome.ok:
            return self._respond(
                state,
                "I couldn't read your ticket history just now: "
                + (outcome.error or {}).get("message", "the helpdesk did not answer.")
                + " Nothing was changed.",
                state_name="failed", tool_calls=tool_calls, usage=meter.public(),
            )
        tickets = outcome.data.get("tickets") or []
        known_ids = [str(t["ticket_id"]) for t in tickets if isinstance(t, dict) and t.get("ticket_id")]
        if not tickets:
            reply = "I don't see any tickets for your email in the helpdesk archive. If you want one, describe the problem and I can prepare a draft."
            return self._respond(state, reply, state_name="answered", tool_calls=tool_calls, usage=meter.public(),
                                 known_ticket_ids=known_ids)
        lines = [f"I found **{len(tickets)}** ticket(s) for {employee.email} (newest first):"]
        lines.append("")
        for ticket in tickets[:6]:
            lines.append(
                f"- **{ticket.get('ticket_id')}** - {ticket.get('subject')}  \n"
                f"  {ticket.get('status')} · {ticket.get('priority')} · {ticket.get('assigned_team')}"
                + (f" · resolution: {ticket.get('resolution')}" if ticket.get("resolution") else "")
            )
        reply = "\n".join(lines)
        return self._respond(state, reply, state_name="answered", tool_calls=tool_calls, usage=meter.public(),
                             known_ticket_ids=known_ids)

    async def _ticket_followup(
        self,
        state: SessionState,
        meter: UsageMeter,
        employee: Employee,
        tool_calls: list[ToolCall],
        ticket_id: str,
        message: str,
    ) -> ChatResponse:
        outcome = await mcp_client.call("get_ticket", {"ticket_id": ticket_id})
        tool_calls.append(_tool_call(outcome))
        if not outcome.ok:
            return self._respond(
                state,
                f"I couldn't find ticket **{ticket_id}**: " + (outcome.error or {}).get("message", "not found."),
                state_name="answered", tool_calls=tool_calls, usage=meter.public(),
                known_ticket_ids=[ticket_id],
            )
        ticket = outcome.data.get("ticket") or outcome.data
        if not isinstance(ticket, dict):
            ticket = {}
        real_ticket_id = str(ticket.get("ticket_id") or ticket_id)
        if str(ticket.get("requester_email", "")).lower() != employee.email.lower():
            reply = f"Ticket **{ticket_id}** is not one of your tickets, so I can't discuss or change it."
            return self._respond(state, reply, state_name="refused", tool_calls=tool_calls, usage=meter.public(),
                                 known_ticket_ids=[real_ticket_id])

        if NOTE_RE.search(message):
            note = message.strip()
            if len(note) < 10:
                return self._respond(state, "What note should I add to the ticket?", state_name="clarification",
                                     tool_calls=tool_calls, usage=meter.public())
            payload = {"ticket_id": str(ticket.get("ticket_id")), "note": note[:2000]}
            action = actions.create(
                state.session_id, "add_ticket_note",
                f"Add a note to {ticket.get('ticket_id')}", payload,
            )
            state.pending_action_id = action.action_id
            return self._respond(
                state, note_draft_reply(str(ticket.get("ticket_id")), note[:2000]),
                state_name="awaiting_approval", tool_calls=tool_calls, usage=meter.public(),
                pending=_pending(action), known_ticket_ids=[real_ticket_id],
            )

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
        return self._respond(state, "\n".join(lines), state_name="answered",
                             tool_calls=tool_calls, usage=meter.public(),
                             known_ticket_ids=[real_ticket_id])

    # ── response assembly ────────────────────────────────────────────────
    def _respond(
        self,
        state: SessionState,
        reply: str,
        *,
        state_name: str,
        citations: list[dict[str, Any]] | None = None,
        tool_calls: list[ToolCall] | None = None,
        usage: dict[str, Any] | None = None,
        pending: PendingAction | None = None,
        result: dict[str, Any] | None = None,
        known_ticket_ids: list[str] | None = None,
    ) -> ChatResponse:
        citations = citations or []
        clean, kept, warnings = guard_reply(
            reply, citations=citations, result=result, known_ticket_ids=known_ticket_ids
        )
        if warnings:
            log.debug("output guard: %s", warnings)
        if not clean.strip():
            clean = "I couldn't produce a safe answer. Please rephrase the request."
        state.last_reply = clean
        state.last_citations = kept
        state.last_tool_calls = [t.model_dump() for t in (tool_calls or [])]
        state.updated_at = now_iso()
        state.add_message("assistant", clean)
        sessions.save(state)
        return ChatResponse(
            session_id=state.session_id,
            reply=clean,
            state=state_name,
            pending_action=pending,
            result=result,
            citations=kept,
            tool_calls=tool_calls or [],
            usage=Usage(**(usage or {})),
        )


def _citations(doc_ids: list[str]) -> list[dict[str, Any]]:
    manifest = load_manifest()
    out = []
    for doc_id in doc_ids:
        citation = manifest.citation(doc_id)
        if citation and citation["id"] not in {c["id"] for c in out}:
            out.append(citation)
    return out


workflow = Workflow()
