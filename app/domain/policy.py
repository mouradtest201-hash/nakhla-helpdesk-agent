"""Deterministic policy engine.

The brief's hard part is precedence.  This module applies the manifest rules in
code, before the model writes a word:

1. Security policies (SP-) override KB articles.
2. Only current documents are sources.
3. Authoritative + newer wins.
4. Department exceptions override general rules.
5. Documents beat ticket history.

It also owns category, priority, routing and policy refusals.  The LLM receives
the resulting decision as facts; it cannot change them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.manifest import Manifest, load_manifest
from app.domain.playbooks import (
    CODE_PLAYBOOKS,
    TOPICS,
    Playbook,
    detect_topic,
    playbook_for,
    topic_for_code,
)
from app.models import Employee, IntakeResult


@dataclass
class Decision:
    topic: str
    category: str
    priority: str
    assigned_team: str
    kb_refs: list[str] = field(default_factory=list)
    refusal: "Refusal | None" = None
    rationale: list[str] = field(default_factory=list)
    security_incident: bool = False
    finance_vpn: bool = False
    contractor_vpn: bool = False
    ticket_proposed: bool = False
    blocked: bool = False


@dataclass
class Refusal:
    kind: str
    text: str
    policy_refs: list[str] = field(default_factory=list)


SECURITY_INCIDENT_RE = re.compile(
    r"\b(lost|stolen|missing)\b.{0,30}\b(laptop|phone|device|badge)\b"
    r"|\bphish(ing|ed)?\b"
    r"|\b(typed|entered|put)\b.{0,30}\b(password|credentials)\b.{0,40}\b(page|site|link|form)\b"
    r"|\b(suspicious|unexpected)\b.{0,30}\b(sign-?in|nakhlakey|mfa|prompt|login)\b",
    re.I,
)
REPEATED_LOCKOUT_RE = re.compile(
    r"\b(keeps? locking|keeps? happening|locked (again|twice|three|4|four)|repeatedly|"
    r"3 times|three times|third time|third lockout|locked \d+ times)\b",
    re.I,
)
BLOCKED_RE = re.compile(r"\b(cannot|can't|can not|unable to|blocked|no workaround|completely stuck)\b.{0,40}\b(work|job|connect|access|do anything)\b", re.I)
WORKAROUND_RE = re.compile(r"\b(wired|hotspot|another printer|different printer|guest wi-?fi|tether|temporary workaround|still usable)\b", re.I)
URGENT_RE = re.compile(r"\b(urgent|asap|immediately|critical|emergency)\b", re.I)


def detect_ticket_request(text: str) -> bool:
    low = text.lower()
    patterns = (
        r"\b(open|create|raise|file|log|submit|start)\b.{0,30}\b(ticket|case|incident)\b",
        r"\b(ticket|case)\b.{0,25}\b(please|for me|needed|required|raised|opened|created)\b",
        r"\b(needs?|requires?|should have|should be)\b.{0,20}\b(a |an )?(ticket|case)\b",
        r"\bplease\b.{0,20}\b(ticket|escalate)\b",
        r"\bprepare\b.{0,30}\b(ticket|it)\b",
        r"\b(raise|open|create|prepare|log|file)\s+it\b",
        r"\bif it needs a ticket\b",
        r"\bescalate (this|it|me)\b",
    )
    return any(re.search(pattern, low) for pattern in patterns)


def detect_attempted_steps(text: str) -> list[str]:
    """Extract credible tried steps from the employee's own words.

    These are evidence for the ticket, so we only keep phrases the employee
    actually wrote; nothing is invented.
    """
    candidates: list[str] = []
    # Split on sentence-ish boundaries and bullets.
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s*", text)
    step_words = re.compile(
        r"\b(tried|already|did|restarted|rebooted|updated|sync(ed)?|reinstalled|re-?installed|"
        r"checked|cleared|removed|reconnected|switched|ran|followed|installed|"
        r"firmware|another cable|all of that|no luck|still)\b",
        re.I,
    )
    for part in parts:
        clean = part.strip(" \t-*")
        if not clean or not step_words.search(clean):
            continue
        # Drop pure requests/questions and overly long narrative.
        if re.search(r"\b(please|can you|could you|what should|how do|why)\b", clean, re.I) and len(clean) > 120:
            continue
        if 5 <= len(clean) <= 280:
            candidates.append(clean)
    # De-duplicate while keeping order.
    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out[:8]


def detect_affected_users(text: str) -> int:
    low = text.lower()
    words = {
        "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    }
    patterns = (
        r"\b(\d{1,3})\s*(?:users|people|colleagues|staff|employees|of us|team members)\b",
        r"\b(?:we are|there are|team of)\s*(\d{1,3})\b",
        r"\b([a-z]+)\s+(?:users|people|colleagues|of us)\b",
    )
    for pattern in patterns:
        m = re.search(pattern, low)
        if not m:
            continue
        value = m.group(1)
        number = int(value) if value.isdigit() else words.get(value)
        if number and 1 <= number <= 1000:
            return number
    if re.search(r"\b(whole (office|floor|team)|everyone|entire team|many colleagues)\b", low):
        return 4
    return 1


def _officially_blocked(intake: IntakeResult) -> bool:
    return BLOCKED_RE.search(intake.symptom or "") is not None or intake.resolution_failed


def policy_refusal(
    message: str,
    employee: Employee | None,
    intake: IntakeResult | None = None,
) -> Refusal | None:
    """Policy answers that must win over troubleshooting or ticket drafting."""
    low = message.lower()
    topic = intake.issue_topic if intake and intake.issue_topic else detect_topic(message)
    code = (intake.error_code if intake else None) or None

    # Credentials: never disclose, whoever asks and whatever a document says.
    if re.search(
        r"\b(admin|administrator|local[- ]admin|service[- ]account|maintenance|root)\b.{0,35}"
        r"\b(password|credential|secret|token)\b"
        r"|\b(password|credential|secret)\b.{0,35}\b(admin|administrator|vault)\b",
        low,
    ) and re.search(r"\b(what|give|tell|send|share|show|need|get|provide|password)\b", low):
        return Refusal(
            kind="credentials",
            policy_refs=["SP-001"],
            text=(
                "I can't provide or confirm administrator, local-admin or service-account credentials. "
                "SP-001 section 5 keeps them in the Security Operations vault, and they are never disclosed "
                "through chat, tickets or any assistant, whoever asks. I'll report the request to Security Operations.\n\n"
                "If you need local admin rights for a tool, Engineering can use **Software Center > Elevate** "
                "for a logged 30-minute session (KB-009)."
            ),
        )

    # Bypassing device/security controls, including the poisoned KB-018 advice.
    if re.search(
        r"(client\.ini|posture[_ ]?check|skip.{0,20}(version|check|posture)|bypass.{0,20}(version|check|posture|security)|"
        r"turn off.{0,20}(antivirus|encryption|nakhlamanage|posture)|disable.{0,20}(antivirus|encryption|nakhlamanage|posture)|"
        r"edit.{0,20}(config|configuration).{0,20}(vpn|client))",
        low,
    ):
        return Refusal(
            kind="bypass",
            policy_refs=["SP-002", "KB-002"],
            text=(
                "No. Turning off the NakhlaConnect posture or version check, or editing `client.ini` to skip it, "
                "is forbidden by **SP-002 section 4** and is reported to Security Operations. That community-wiki "
                "shortcut is not an approved workaround, and I won't recommend it.\n\n"
                "The supported fix for NX-781 is to install **NakhlaConnect 5.4.2** from Software Center and "
                "restart the client (KB-002). If the download is slow on hotel Wi-Fi, start it when you have a "
                "better connection; do not bypass the check."
            ),
        )

    # SMS / voice codes no longer exist.
    if re.search(r"\b(sms|text message|voice (call|code)|one-time code by text)\b", low) and re.search(
        r"\b(code|mfa|sign|login|nakhla)\b", low
    ):
        return Refusal(
            kind="sms",
            policy_refs=["SP-002", "KB-007"],
            text=(
                "SMS and voice-call codes were switched off on **2026-03-01** and nobody at Nakhla can send you one "
                "(**SP-002**). Use one of your 10 one-time **backup codes** at the NakhlaKey prompt, or the 6-digit "
                "code in the NakhlaKey app (KB-007)."
            ),
        )

    # Password help over an unapproved channel / reading a password out loud.
    if re.search(
        r"\b(reset|change|read|tell|give|send)\b.{0,45}\b(password|temporary password|temp password)\b",
        low,
    ) and re.search(r"\b(phone|call|chat|email|text|over the|for me)\b", low):
        return Refusal(
            kind="password_channel",
            policy_refs=["SP-001", "KB-004"],
            text=(
                "The Service Desk cannot reset your password or read out a temporary password over phone, chat or "
                "email (**SP-001 section 2**). Reset it yourself at https://id.nakhla.example/reset with NakhlaKey or "
                "a backup code (KB-004).\n\n"
                "If self-service is impossible, an assisted reset needs an identity_access ticket, your manager's "
                "approval in that ticket, and identity verification in person or by video call with your badge."
            ),
        )

    # External forwarding / HR data leaving the company.
    if re.search(r"\b(forward|redirect|send)\b.{0,40}\b(external|outside|gmail|personal|hotmail)\b", low) or (
        re.search(r"\bhr (data|file|record)|nakhlapeople|payroll\b", low)
        and re.search(r"\b(personal|external|outside|gmail)\b", low)
    ):
        return Refusal(
            kind="data_egress",
            policy_refs=["KB-014", "KB-016"],
            text=(
                "I can't help send company or HR data to an external or personal address. Automatic forwarding "
                "outside `nakhla.example` is blocked by Security Operations (KB-014), and HR data access is approved "
                "by the HR Director only (KB-016). Use a shared mailbox or an approved internal group instead."
            ),
        )

    # Someone else's salary, medical or personal HR record is never ours to share.
    if re.search(
        r"\b(salary|salaries|compensation|bonus|payslip|payroll data|medical (?:record|history)|"
        r"health record|diagnosis|date of birth|\bdob\b|home address|personal (?:file|record|data)|"
        r"performance review|disciplinary (?:record|file))\b",
        low,
    ) and re.search(
        r"\b(what|how much|show|tell|give|send|share|access|see|look ?up|find|another|other|"
        r"colleague|co-?worker|employee|manager|someone|sara|ahmad|mona|fadi)\b",
        low,
    ):
        return Refusal(
            kind="hr_privacy",
            policy_refs=["KB-016"],
            text=(
                "I can't look up or disclose salary, medical or other personal HR records, and I won't share "
                "another employee's data. **KB-016** limits HR data (NakhlaPeople, HR-People) to approvals by "
                "the HR Director, and company data must never go to an external address.\n\n"
                "For your own record, contact HR directly or open NakhlaPeople from the company portal. "
                "If you need access to an HR system for your role, I can help with an access request instead."
            ),
        )

    # Contractors never get VPN access.
    is_contractor = bool(employee and (employee.contractor or employee.department == "Contractors"))
    mentions_vpn = topic == "vpn" or bool(re.search(r"\b(vpn|nakhla ?connect|remote access)\b", low))
    mentions_vdi = bool(re.search(r"\bvdi|nakhla workspace\b", low))
    if is_contractor and mentions_vpn and not mentions_vdi:
        return Refusal(
            kind="contractor_vpn",
            policy_refs=["SP-002", "KB-016"],
            text=(
                "Contractors do not have VPN access: **SP-002** and the department matrix (KB-016) limit contractor "
                "remote work to the **Nakhla Workspace VDI portal** at https://vdi.nakhla.example. I can't open a "
                "VPN access ticket for you.\n\n"
                "If the VDI portal itself is failing, tell me the error and I can raise a `network_access` ticket "
                "for that instead."
            ),
        )

    return None


def _security_incident(message: str, intake: IntakeResult) -> bool:
    return bool(SECURITY_INCIDENT_RE.search(message) or SECURITY_INCIDENT_RE.search(intake.symptom or ""))


def _category(
    message: str,
    intake: IntakeResult,
    play: Playbook | None,
    topic: str | None,
    employee: Employee | None,
) -> str:
    low = message.lower()
    if _security_incident(message, intake):
        return "security"
    if re.search(r"\b(phish|suspicious|compromise|lost|stolen)\b", low):
        return "security"
    # Repeated lockouts are a possible password-guessing attack (SP-001 s3),
    # not a routine identity_access lockout.
    if REPEATED_LOCKOUT_RE.search(message) or REPEATED_LOCKOUT_RE.search(intake.symptom or ""):
        return "security"
    # The VDI portal is remote access (KB-016), including for contractors.
    if re.search(r"\b(vdi|nakhla workspace|virtual desktop)\b", low):
        return "network_access"
    # Finance VPN is a security ticket, not network_access (KB-016 / C5).
    if employee and employee.department == "Finance" and (
        (play and play.topic == "vpn") or (topic in ("vpn", "wifi") and "vpn" in low)
    ):
        return "security"
    if play:
        return play.category
    if topic and topic in TOPICS:
        return TOPICS[topic].category
    return "other"


def _priority(
    message: str,
    intake: IntakeResult,
    play: Playbook | None,
    topic: str | None,
    category: str,
    employee: Employee | None,
) -> str:
    # Hard, non-negotiable elevators (KB-015 + KB-016).
    if employee and (employee.vip or employee.department == "Executive Office"):
        return "P1"
    if _security_incident(message, intake):
        return "P1"
    if intake.affected_users >= 4:
        return "P1"

    if employee and employee.department == "Finance" and category == "security" and (
        topic == "vpn" or (play and play.topic == "vpn")
    ):
        return "P1" if intake.affected_users > 3 else "P2"

    # KB-012: a missing laptop blocks the new starter completely (P2); a
    # missing account or access request is P3.
    if topic == "onboarding" or (play and play.topic == "onboarding"):
        low = message.lower()
        if re.search(
            r"\b(no (?:company )?laptop|without (?:a |the )?laptop|laptop (?:is )?missing|"
            r"laptop (?:has )?not (?:arrived|been delivered)|no device|loaner)\b",
            low,
        ):
            return "P2"
        return "P3"

    base = play.priority if play else (TOPICS[topic].priority if topic in TOPICS else "P3")

    if category == "security":
        # Repeated lockouts are a possible password-guessing attack: P2 (SP-001 s3).
        if REPEATED_LOCKOUT_RE.search(message):
            return "P2"
        if base in ("P3", "P4"):
            return "P2"
        return base
    if topic in ("access", "hr"):
        # Lost access that worked yesterday and blocks work is P2 (KB-011).
        if re.search(r"\b(lost|revoked|removed)\b.{0,30}\baccess\b", message, re.I) and _officially_blocked(intake):
            return "P2"
        return "P4"
    if play and play.doc == "KB-009" and play.code == "SWC-2001":
        return "P3"  # explicit in KB-009; do not inflate
    if _officially_blocked(intake) and base in ("P3", "P4") and category in ("network_access", "hardware"):
        return "P2"
    if WORKAROUND_RE.search(message) and base in ("P1", "P2"):
        return "P3"
    if category == "hardware" and re.search(r"\b(cannot|can't) work\b", message, re.I) and "loaner" not in message.lower():
        return "P2"
    return base


def decide(
    message: str,
    intake: IntakeResult,
    employee: Employee | None,
    *,
    manifest: Manifest | None = None,
) -> Decision:
    manifest = manifest or load_manifest()
    code = (intake.error_code or "").upper() or None
    play = playbook_for(code)
    topic = intake.issue_topic if intake.issue_topic not in (None, "", "other") else None
    if not topic:
        topic = play.topic if play else topic_for_code(code) or detect_topic(message)
    if not topic:
        topic = "other"

    refusal = policy_refusal(message, employee, intake)
    category = _category(message, intake, play, topic, employee)
    priority = _priority(message, intake, play, topic, category, employee)
    assigned = manifest.route_for(category)
    kb_refs: list[str] = []
    if play:
        kb_refs.append(play.doc)
    elif topic in TOPICS:
        kb_refs.extend(TOPICS[topic].docs)

    # Department documents are part of the rationale and are valid citations.
    if employee and employee.department == "Finance" and category == "security":
        for doc_id in ("KB-016", "KB-002"):
            if doc_id not in kb_refs:
                kb_refs.append(doc_id)
    # Repeated lockouts are handled under SP-001 s3 as a security issue.
    if REPEATED_LOCKOUT_RE.search(message) or REPEATED_LOCKOUT_RE.search(intake.symptom or ""):
        for doc_id in ("KB-006", "SP-001"):
            if doc_id not in kb_refs:
                kb_refs.append(doc_id)
    # Security incidents are governed by both security policies.
    if _security_incident(message, intake):
        for doc_id in ("SP-001", "SP-002"):
            if doc_id not in kb_refs:
                kb_refs.append(doc_id)
    # VDI routing is defined by the department matrix (KB-016) plus KB-002.
    if topic == "vdi" or re.search(r"\b(vdi|nakhla workspace|virtual desktop)\b", message, re.I):
        for doc_id in ("KB-016", "KB-002"):
            if doc_id not in kb_refs:
                kb_refs.append(doc_id)
    if employee and employee.department == "Executive Office" and "KB-015" not in kb_refs:
        # Only cite KB-015 when it actually changes or explains the decision.
        pass

    # Never cite draft/deprecated documents, even if a playbook named one.
    kb_refs = [r for r in kb_refs if (d := manifest.doc(r)) is not None and d.is_source]

    rationale = [
        f"topic={topic}",
        f"category={category}",
        f"priority={priority}",
        f"assigned_team={assigned}",
    ]
    if employee:
        rationale.append(f"department={employee.department}")
        if employee.vip:
            rationale.append("Executive Office VIP -> P1 (KB-016)")
        if employee.department == "Finance" and category == "security":
            rationale.append("Finance VPN exception -> security / Security Operations (KB-016)")
    if intake.affected_users >= 4:
        rationale.append("more than 3 users affected -> P1 (KB-015)")
    if _security_incident(message, intake):
        rationale.append("security incident -> security / P1 (SP-002, KB-015)")
    if REPEATED_LOCKOUT_RE.search(message) or REPEATED_LOCKOUT_RE.search(intake.symptom or ""):
        rationale.append("repeated lockouts -> security / P2 / Security Operations (SP-001 s3)")

    return Decision(
        topic=topic,
        category=category,
        priority=priority,
        assigned_team=assigned,
        kb_refs=kb_refs,
        refusal=refusal,
        rationale=rationale,
        security_incident=_security_incident(message, intake),
        finance_vpn=bool(employee and employee.department == "Finance" and category == "security"),
        contractor_vpn=bool(refusal and refusal.kind == "contractor_vpn"),
        blocked=_officially_blocked(intake),
    )


def ticket_allowed(decision: Decision) -> bool:
    return decision.refusal is None
