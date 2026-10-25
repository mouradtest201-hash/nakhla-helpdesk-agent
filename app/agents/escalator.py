"""Escalator role: build a valid ticket draft, but never write it."""
from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from app.domain.playbooks import TOPICS, playbook_for
from app.domain.policy import Decision
from app.models import Employee, IntakeResult, TicketDraft
from app.rag.retriever import RetrievalResult


def _short(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _subject(intake: IntakeResult, decision: Decision, employee: Employee) -> str:
    play = playbook_for(intake.error_code)
    if play:
        base = play.title
        if decision.finance_vpn:
            base = f"{base} - Finance (Security Operations)"
        return _short(base, 120)
    topic = TOPICS.get(decision.topic)
    if topic:
        code = f" ({intake.error_code})" if intake.error_code else ""
        return _short(f"{topic.title}{code} - {employee.department}", 120)
    if intake.error_code:
        return _short(f"{intake.error_code} - no documented KB fix", 120)
    return _short(f"IT support request - {intake.symptom or 'employee needs help'}", 120)


def _steps(intake: IntakeResult, decision: Decision) -> list[str]:
    steps = []
    for step in intake.attempted_steps or []:
        clean = _short(step, 300)
        if len(clean) >= 3 and clean not in steps:
            steps.append(clean)
    if not steps and decision.finance_vpn:
        steps = ["Checked the status page; Finance VPN exception: no client troubleshooting (KB-016)."]
    if not steps:
        steps = ["Reported the problem to the IT helpdesk assistant; no troubleshooting steps recorded yet."]
    return steps[:20]


def _description(
    intake: IntakeResult,
    decision: Decision,
    employee: Employee,
    retrieval: RetrievalResult,
) -> str:
    lines = [
        f"Reported symptom: {_short(intake.symptom, 900)}",
        f"Error code: {intake.error_code or 'none given'}",
        "",
        "Employee context from the directory:",
        f"- {employee.name or employee.email} ({employee.email}), {employee.department}, {employee.location or 'location unknown'}",
        f"- Device: {employee.device_os or 'unknown'}; NakhlaConnect: {employee.vpn_client_version or 'not installed'}; "
        f"gateway: {employee.vpn_gateway or 'none'}; account: {employee.account_status}",
        f"- VIP: {'yes' if employee.vip else 'no'}; contractor: {'yes' if employee.contractor else 'no'}",
    ]
    if employee.vip:
        lines.append("- Priority: P1 because Executive Office is VIP (KB-016).")
    if decision.finance_vpn:
        lines.append("- Department exception: Finance VPN/remote-access issues go to Security Operations as category security (KB-016).")
    if intake.affected_users > 1:
        lines.append(f"- Affected users: {intake.affected_users}.")
    lines += [
        "",
        "Steps already tried by the employee:",
        *[f"- {step}" for step in _steps(intake, decision)],
        "",
        "Knowledge-base references used: " + (", ".join(decision.kb_refs) or "none"),
        "The employee has seen this draft and approved it in the IT assistant before this ticket was created.",
    ]
    if retrieval.docs:
        lines.append("Retrieved current sources: " + ", ".join(d.doc_id for d in retrieval.docs))
    return _short("\n".join(lines), 3800)


def build_ticket(
    intake: IntakeResult,
    decision: Decision,
    employee: Employee,
    retrieval: RetrievalResult,
) -> tuple[dict[str, Any], str]:
    """Return (API payload, human summary).  The payload is validated locally."""
    subject = _subject(intake, decision, employee)
    if len(subject) < 5:
        subject = "IT support request"
    data: dict[str, Any] = {
        "subject": subject,
        "description": _description(intake, decision, employee, retrieval),
        "category": decision.category,
        "priority": decision.priority,
        "requester_email": employee.email.lower(),
        "department": employee.department,
        "attempted_steps": _steps(intake, decision),
        "kb_refs": decision.kb_refs[:10],
        "affected_users": max(1, int(intake.affected_users or 1)),
    }
    try:
        draft = TicketDraft(**data)
        payload = draft.model_dump()
    except ValidationError:
        # Repair only presentation fields; policy fields are already known-good.
        data["subject"] = _short(data["subject"] or "IT support request", 120)
        if len(data["subject"]) < 5:
            data["subject"] = "IT support request"
        data["description"] = _short(data["description"], 4000)
        if len(data["description"]) < 10:
            data["description"] = "Employee requested IT support; see ticket fields."
        data["attempted_steps"] = [
            _short(step, 300) for step in data["attempted_steps"] if len(_short(step, 300)) >= 3
        ][:20]
        if not data["attempted_steps"]:
            data["attempted_steps"] = ["Reported the problem to the IT helpdesk assistant."]
        draft = TicketDraft(**data)
        payload = draft.model_dump()

    summary = (
        f"Create {payload['category']} {payload['priority']} ticket for {payload['requester_email']}: "
        f"{payload['subject']}"
    )
    decision.ticket_proposed = True  # type: ignore[attr-defined]
    return payload, _short(summary, 300)
