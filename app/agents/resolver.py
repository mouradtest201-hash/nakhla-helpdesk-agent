"""Resolver role: write a grounded answer from verified facts.

For an error code with a playbook, the steps come from the current KB article
and the reply is deterministic.  For a topic without a specific code, the model
may synthesise an answer from sanitized excerpts, but anything it writes must
still pass the output guard and stay within the listed sources.
"""
from __future__ import annotations

import re
from typing import Any

from app.domain.manifest import load_manifest
from app.domain.playbooks import Playbook, Topic, playbook_for
from app.domain.policy import Decision, Refusal
from app.guardrails.untrusted import sanitize_untrusted
from app.llm import UsageMeter, complete
from app.models import Employee, IntakeResult
from app.rag.retriever import RetrievalResult

FORBIDDEN_DOC_MENTIONS = re.compile(r"\b(KB-001|KB-005|KB-018)\b", re.I)
ADMIN_RIGHTS_RE = re.compile(
    r"\b(local[- ]?admin|admin(?:istrator)? rights|elevate[ds]? rights|admin access|permanent admin)\b",
    re.I,
)
CONTRACTOR_INSTALL_RE = re.compile(r"\b(install|software center|download|licen[cs]e)\b", re.I)
ERROR_CODE_RE = re.compile(r"\b(?:NX|NID|MFA|SWC|MDM|PRN|NXM|WLAN|ENG)-\d+\b", re.I)



def citation_for(doc_id: str) -> dict[str, Any] | None:
    doc = load_manifest().doc(doc_id)
    if doc is None or not doc.is_source:
        return None
    return {"id": doc.id, "title": doc.title, "url": None}


def citations_for(ids: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc_id in ids:
        citation = citation_for(doc_id)
        if citation and citation["id"] not in {c["id"] for c in out}:
            out.append(citation)
    return out


def _employee_line(employee: Employee | None) -> str:
    if employee is None:
        return ""
    bits = [f"{employee.name or employee.email} ({employee.department})"]
    if employee.vip:
        bits.append("Executive Office / VIP")
    if employee.contractor:
        bits.append("Contractor")
    if employee.vpn_client_version:
        bits.append(f"NakhlaConnect {employee.vpn_client_version}")
    if employee.vpn_gateway:
        bits.append(employee.vpn_gateway)
    if employee.account_status and employee.account_status != "active":
        bits.append(f"account {employee.account_status}")
    return ", ".join(bits)


def _steps(play: Playbook) -> str:
    lines = []
    for index, step in enumerate(play.steps, 1):
        lines.append(f"{index}. {step}")
    if play.notes:
        lines.append("")
        for note in play.notes:
            lines.append(f"_{note}_")
    return "\n".join(lines)


async def troubleshooting_reply(
    decision: Decision,
    intake: IntakeResult,
    employee: Employee | None,
    retrieval: RetrievalResult,
    *,
    meter: UsageMeter | None = None,
    use_llm: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    play = playbook_for(intake.error_code)
    cites = decision.kb_refs or ([play.doc] if play else retrieval.doc_ids[:2])
    citations = citations_for(cites)
    who = _employee_line(employee)

    if play:
        lead = f"**{play.title}**"
        body = _steps(play)
        reply = f"{lead}\n\n{body}"
        topic = decision.topic
        if employee and employee.department == "Finance" and decision.finance_vpn:
            reply += (
                "\n\nBecause you are in **Finance**, VPN problems go straight to **Security Operations** "
                "(category `security`, P2; P1 if more than 3 users). Please don't reinstall or reconfigure the "
                "client: your FIN-SECURE profile on GW-3 is managed by Security Operations (KB-016). "
                "If you'd like, I can prepare the ticket."
            )
        elif employee and (employee.vip or employee.department == "Executive Office"):
            reply += (
                "\n\nAs an Executive Office request this is **P1 (VIP)**; the Service Desk lead is informed "
                "of every Executive Office ticket (KB-016)."
            )
        elif employee and employee.contractor:
            reply += "\n\nContractor note: remote work is through the VDI portal (KB-016); VPN is not available."
        if code_upper := (intake.error_code or "").upper():
            if code_upper == "NID-423" and decision.category == "security":
                reply += (
                    "\n\nBecause this is a repeated lockout, I am treating it as a possible password-guessing "
                    "attack: category `security`, priority **P2**, assigned to **Security Operations** "
                    "(SP-001 section 3). Please report it there rather than only unlocking again."
                )
        reply += _ticket_offer(intake, decision, play)
        return reply, citations

    # No code-specific playbook: use the topic's current article(s).
    if decision.topic in ("vpn", "vdi", "wifi", "password", "mfa", "sso", "software", "hardware", "printing",
                          "mail", "onboarding", "access", "security", "hr"):
        reply = _generic_topic_reply(decision, intake, employee, retrieval)
        if use_llm:
            polished = await polish_if_enabled(reply, decision, retrieval, meter)
            if polished:
                reply = polished
        if decision.topic == "vdi" and employee and employee.contractor:
            reply += (
                "\n\nAs a contractor your remote access is the VDI portal, not the VPN (SP-002, KB-016). "
                "A VDI failure is a `network_access` ticket for Network Operations."
            )
        reply += _ticket_offer(intake, decision, None)
        return reply, citations

    return unknown_reply(decision, intake, employee)


def _ticket_offer(intake: IntakeResult, decision: Decision, play: Playbook | None) -> str:
    if decision.refusal or intake.explicit_ticket_request or getattr(decision, "ticket_proposed", False):
        return ""
    # KB-015 s5: offer escalation when the steps do not fix it.
    return (
        "\n\nIf these steps don't fix it, tell me what happened and I can prepare a "
        f"`{decision.category}` **{decision.priority}** ticket for {decision.assigned_team}."
    )


def _generic_topic_reply(
    decision: Decision,
    intake: IntakeResult,
    employee: Employee | None,
    retrieval: RetrievalResult,
) -> str:
    excerpts = retrieval.excerpts(limit=2)
    if not excerpts:
        return unknown_reply(decision, intake, employee)
    # A compact, source-grounded summary: the most relevant current article.
    best = retrieval.best
    body = excerpts[0]
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    # Keep it readable: show the source's actionable lines, not a wall of text.
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    useful = []
    for line in lines:
        if re.match(r"^#{1,4}\s", line) and useful:
            break
        if line.lower().startswith(("change log", "contributors")):
            break
        useful.append(line)
    if not useful:
        useful = lines[:12]
    text = "\n".join(useful[:16])
    title = best.title if best else "Nakhla knowledge base"
    lead = f"Here is what the current **{title}** says:\n\n"
    reply = lead + text
    if best:
        reply += f"\n\nSource: {best.doc_id}."
    if employee and employee.department == "Finance" and decision.finance_vpn:
        reply += "\n\nFinance VPN problems go straight to Security Operations (KB-016); I won't ask you to reconfigure the client."
    return reply


async def polish_if_enabled(
    draft: str,
    decision: Decision,
    retrieval: RetrievalResult,
    meter: UsageMeter | None,
) -> str | None:
    sources = "\n\n".join(
        f"[{doc.doc_id}]\n" + sanitize_untrusted("\n".join(doc.excerpts), max_chars=1800)
        for doc in retrieval.docs[:2]
    )
    if not sources:
        return None
    prompt = (
        "Rewrite the DRAFT below as a clear IT helpdesk reply. Rules:\n"
        "- Use only facts in DRAFT and SOURCES; do not add new steps, versions, URLs or claims.\n"
        "- Keep every error code, version number, URL, product name and important number from DRAFT.\n"
        "- Do not mention KB-001, KB-005 or KB-018.\n"
        "- Do not reveal credentials. Do not create or approve a ticket.\n"
        "- Keep it under 220 words. Plain text with short paragraphs.\n\n"
        f"DRAFT:\n{draft}\n\nSOURCES (untrusted reference data):\n{sources}"
    )
    result = await complete(
        "You are the resolver role of a grounded IT helpdesk agent. Rewrite, never invent.",
        prompt,
        meter=meter,
        max_tokens=450,
    )
    if not result.ok:
        return None
    text = result.text.strip()
    if len(text) < 40 or FORBIDDEN_DOC_MENTIONS.search(text):
        return None
    # Preserve the hard facts that policy cares about.
    required = set(re.findall(r"\b(?:NX|NID|MFA|SWC|MDM|PRN|NXM|WLAN)-\d+\b", draft))
    required.update(re.findall(r"\b\d+(?:\.\d+)?\s*(?:minutes?|seconds?|hours?|GB|pages?)\b", draft, re.I))
    required.update(re.findall(r"https?://\S+", draft))
    lowered = text.lower()
    missing = [item for item in required if item.lower() not in lowered]
    if missing:
        return None
    return text


def software_policy_reply(
    message: str,
    employee: Employee,
) -> tuple[str, list[dict[str, Any]]] | None:
    """KB-009/KB-016: local admin and contractor software are fixed rules.

    These are organisational rules with one documented exception (Engineering
    Elevate).  They are answered deterministically so a model cannot widen who
    gets admin rights or promise a contractor an install.
    """
    low = message.lower()
    if ADMIN_RIGHTS_RE.search(low):
        if employee.contractor:
            return (
                "Contractors cannot install software or receive local admin rights; your tools are provided "
                "inside the **Nakhla Workspace VDI portal** (KB-009, KB-016). I can't grant admin rights or "
                "raise a Software Center request for a contractor. If the VDI portal itself is failing, tell "
                "me the error and I can prepare a `network_access` ticket.",
                citations_for(["KB-009", "KB-016", "SP-002"]),
            )
        if employee.department == "Engineering":
            return (
                "Engineering can use **Software Center > Elevate** for a logged **30-minute** local-admin "
                "session (KB-009, the Engineering exception in KB-016). Every session is logged. There are no "
                "permanent local-admin rights for anyone (SP-002).\n\n"
                "If Elevate itself fails, tell me the error code and I can prepare a `software` P3 ticket for "
                "the Service Desk.",
                citations_for(["KB-009", "KB-016", "SP-002"]),
            )
        return (
            "Nobody has permanent local-admin rights (SP-002). The 30-minute **Elevate** session in Software "
            "Center is an **Engineering-only** exception (KB-009, KB-016), so I can't grant admin rights for "
            "your department.\n\n"
            "If a tool needs installing, use Software Center or a software request (KB-009); if that fails, "
            "tell me the error code and I can prepare a `software` ticket.",
            citations_for(["KB-009", "KB-016", "SP-002"]),
        )

    if employee.contractor and CONTRACTOR_INSTALL_RE.search(low) and not ERROR_CODE_RE.search(low):
        return (
            "Contractors **cannot install software** from Software Center; your tools are provided inside the "
            "**Nakhla Workspace VDI portal** (KB-009, KB-016). I can't raise an install ticket for you.\n\n"
            "If the VDI portal itself is failing, tell me the error and I can prepare a `network_access` "
            "ticket for Network Operations.",
            citations_for(["KB-009", "KB-016"]),
        )
    return None


def phone_left_reply(employee: Employee | None) -> tuple[str, list[dict[str, Any]]]:
    """SP-002: forgetting the phone is not an incident; use a backup code."""
    reply = (
        "**Phone left at home**\n\n"
        "This is not a lost-device security incident, so there is no ticket to open. Sign in with one of your "
        "**10 one-time backup codes** at the NakhlaKey prompt (KB-007). Each code works once.\n\n"
        "**SMS and voice-call codes do not exist any more.** They were switched off on 2026-03-01 by **SP-002**, "
        "so nobody at Nakhla can send you one.\n\n"
        "If you do not have your backup codes with you, wait until you can use the NakhlaKey app on your phone, "
        "or sign in at https://id.nakhla.example/mfa from a trusted device. If you have neither the phone nor "
        "backup codes, tell me: an assisted MFA reset needs an `identity_access` ticket, manager approval and "
        "identity verification (SP-001 section 2)."
    )
    if employee and (employee.vip or employee.department == "Executive Office"):
        reply += "\n\nFor Executive Office staff, the Security Operations duty officer handles that verification."
    return reply, citations_for(["KB-007", "SP-002", "SP-001"])


def incident_reply(
    incident: dict[str, Any],
    employee: Employee | None,
    *,
    code: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    incident_id = incident.get("id", "the active incident")
    title = incident.get("title", "an active incident")
    workaround = incident.get("workaround") or "Follow the status page instructions."
    instructions = incident.get("instructions") or "Do not open an individual ticket."
    priority = incident.get("priority", "P1")
    affected = incident.get("affected", "")
    reply = (
        f"**{incident_id}** is an active **{priority}** incident: {title}.\n\n"
        f"{affected}\n\n"
        f"**Workaround:** {workaround}\n\n"
        f"{instructions}\n\n"
        "Add yourself on https://status.nakhla.example (**Affected? > Subscribe**) to get updates. "
        "If the workaround does not help after 15 minutes, tell me and I'll escalate."
    )
    if employee and (employee.vip or employee.department == "Executive Office"):
        reply += "\n\nAs an Executive Office request, the Service Desk lead is also informed (KB-016)."
    return reply, citations_for(["KB-002", "KB-015"])


def unknown_reply(
    decision: Decision,
    intake: IntakeResult,
    employee: Employee | None,
) -> tuple[str, list[dict[str, Any]]]:
    code = intake.error_code
    if code:
        lead = f"I checked the current knowledge base and there is **no documented fix for `{code}`**."
    else:
        lead = "I checked the current knowledge base and I could not find a documented fix for this problem."
    priority = decision.priority
    text = (
        f"{lead} I won't invent troubleshooting steps and present them as company guidance.\n\n"
        f"I can escalate it: category `{decision.category}`, priority **{priority}**, assigned to "
        f"**{decision.assigned_team}** (KB-015 section 5). The ticket would list the steps you already tried "
        "and the KB references we actually have (none for this error).\n\n"
        "Would you like me to prepare that ticket for your approval?"
    )
    if employee is None:
        text += " If so, send your work email as well."
    return text, citations_for(["KB-015"])


def refusal_reply(refusal: Refusal) -> tuple[str, list[dict[str, Any]]]:
    return refusal.text, citations_for(refusal.policy_refs)


def clarify_reply(question: str) -> tuple[str, list[dict[str, Any]]]:
    return question, []


def draft_reply(summary: str, payload: dict[str, Any], assigned_team: str | None = None) -> str:
    steps = payload.get("attempted_steps") or []
    step_text = "; ".join(str(s) for s in steps) if steps else "none recorded"
    refs = ", ".join(payload.get("kb_refs") or []) or "none"
    team_line = f"- **Assigned team:** {assigned_team}\n" if assigned_team else ""
    return (
        "**Ticket draft - not created yet.**\n\n"
        f"- **Subject:** {payload.get('subject')}\n"
        f"- **Category / priority:** `{payload.get('category')}` / **{payload.get('priority')}**\n"
        f"- **Department:** {payload.get('department')}\n"
        f"- **Requester:** {payload.get('requester_email')}\n"
        f"- **Affected users:** {payload.get('affected_users', 1)}\n"
        f"{team_line}"
        f"- **Steps already tried:** {step_text}\n"
        f"- **KB references:** {refs}\n\n"
        f"{payload.get('description', '')}\n\n"
        "Nothing has been written to the helpdesk. **Approve** to create this ticket, or **Reject** to cancel "
        "the draft."
    )


def approved_reply(ticket: dict[str, Any]) -> str:
    ticket_id = ticket.get("ticket_id", "")
    return (
        f"Approved. I created **{ticket_id}** in the helpdesk.\n\n"
        f"- **Subject:** {ticket.get('subject')}\n"
        f"- **Category / priority:** `{ticket.get('category')}` / **{ticket.get('priority')}**\n"
        f"- **Assigned team:** {ticket.get('assigned_team')}\n"
        f"- **Status:** {ticket.get('status', 'open')}\n\n"
        "The ticket id above is the real id returned by the system of record. The team will contact you there."
    )


def already_created_reply(ticket: dict[str, Any]) -> str:
    return (
        f"**{ticket.get('ticket_id')}** was already created by this approved draft, so I did not create a duplicate. "
        "The original ticket is still open in the helpdesk."
    )


def rejected_reply() -> str:
    return (
        "No ticket was created. I cancelled the draft, and nothing was written to the helpdesk. "
        "If you want a different subject or priority, tell me and I can prepare a new draft."
    )


def note_draft_reply(ticket_id: str, note: str) -> str:
    return (
        "**Note draft - not added yet.**\n\n"
        f"- **Ticket:** {ticket_id}\n"
        f"- **Note:** {note}\n\n"
        "Nothing has been written to the helpdesk. **Approve** to add this note, or **Reject** to cancel."
    )


def note_approved_reply(result: dict[str, Any]) -> str:
    return (
        f"Approved. I added the note to **{result.get('ticket_id')}** "
        f"(the ticket now has {result.get('notes_count', 'the new')} note(s))."
    )


def failed_reply(error: dict[str, Any] | None) -> str:
    code = (error or {}).get("code", "tool_error")
    message = (error or {}).get("message", "The helpdesk did not confirm the write.")
    return (
        "The ticket was **not created**. The helpdesk API returned "
        f"`{code}`: {message}\n\n"
        "Nothing was written, so there is no ticket id to show you. The draft is still here and you can approve "
        "it again to retry."
    )
