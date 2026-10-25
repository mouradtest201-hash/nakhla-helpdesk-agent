"""Deterministic policy tests: the rules must not depend on the model."""
from __future__ import annotations

import pytest

from app.domain.playbooks import detect_topic
from app.domain.policy import decide, detect_affected_users, detect_ticket_request, ticket_allowed
from app.models import Employee, IntakeResult


def employee(
    email: str = "sara.mikhail@nakhla.example",
    department: str = "Support",
    *,
    vip: bool = False,
    contractor: bool = False,
) -> Employee:
    return Employee(
        email=email,
        name="Test User",
        department=department,
        vip=vip,
        contractor=contractor,
        account_status="active",
        vpn_gateway="GW-1",
    )


def intake(message: str, *, code: str | None = None, affected: int = 1, explicit: bool = False,
           failed: bool = False) -> IntakeResult:
    return IntakeResult(
        symptom=message,
        error_code=code,
        issue_topic=detect_topic(message) or "other",
        affected_users=affected,
        explicit_ticket_request=explicit,
        resolution_failed=failed,
    )


def test_nx781_uses_current_document_not_deprecated():
    decision = decide(
        "NakhlaConnect shows NX-781 and the old guide says 4.8 is fine",
        intake("NakhlaConnect shows NX-781", code="NX-781"),
        employee("tariq.hamdan@nakhla.example", "Engineering"),
    )
    assert decision.category == "network_access"
    assert "KB-002" in decision.kb_refs
    assert "KB-001" not in decision.kb_refs


def test_finance_vpn_is_security_p2_and_security_operations():
    decision = decide(
        "I'm in Finance. NakhlaConnect shows NX-403.",
        intake("I'm in Finance. NakhlaConnect shows NX-403.", code="NX-403"),
        employee("fadi.mansour@nakhla.example", "Finance"),
    )
    assert (decision.category, decision.priority) == ("security", "P2")
    assert decision.assigned_team == "Security Operations"
    assert {"KB-016", "KB-002"} <= set(decision.kb_refs)


def test_finance_vpn_with_four_users_is_p1():
    decision = decide(
        "Four of us in Finance see NX-403.",
        intake("Four of us in Finance see NX-403.", code="NX-403", affected=4),
        employee("fadi.mansour@nakhla.example", "Finance"),
    )
    assert decision.category == "security"
    assert decision.priority == "P1"


def test_vip_is_always_p1():
    decision = decide(
        "My VPN shows NX-112.",
        intake("My VPN shows NX-112.", code="NX-112"),
        employee("samir.nassar@nakhla.example", "Executive Office", vip=True),
    )
    assert decision.priority == "P1"


def test_repeated_lockout_is_security_not_routine_identity_access():
    decision = decide(
        "My NakhlaID account is locked for the third time today, NID-423.",
        intake("My NakhlaID account is locked for the third time today, NID-423.", code="NID-423"),
        employee(),
    )
    assert (decision.category, decision.priority) == ("security", "P2")
    assert decision.assigned_team == "Security Operations"
    assert {"KB-006", "SP-001"} <= set(decision.kb_refs)


def test_contractor_vpn_is_refused_but_vdi_is_network_access():
    contractor = employee("ziad.hourani@nakhla.example", "Contractors", contractor=True)
    vpn = decide("My VPN shows NX-403. Please open a ticket.",
                 intake("My VPN shows NX-403.", code="NX-403", explicit=True), contractor)
    assert vpn.refusal is not None and vpn.refusal.kind == "contractor_vpn"
    assert not ticket_allowed(vpn)

    vdi = decide("The Nakhla Workspace VDI portal will not load. Please open a ticket.",
                 intake("The Nakhla Workspace VDI portal will not load.", explicit=True), contractor)
    assert vdi.refusal is None
    assert (vdi.category, vdi.assigned_team) == ("network_access", "Network Operations")
    assert "KB-016" in vdi.kb_refs


def test_onboarding_priority_depends_on_what_is_missing():
    no_laptop = decide(
        "A new starter has no laptop and cannot work.",
        intake("A new starter has no laptop and cannot work.", explicit=True),
        employee(),
    )
    assert no_laptop.category == "onboarding"
    assert no_laptop.priority == "P2"

    no_account = decide(
        "The new hire account is missing and they cannot sign in.",
        intake("The new hire account is missing and they cannot sign in.", explicit=True),
        employee(),
    )
    assert no_account.category == "onboarding"
    assert no_account.priority == "P3"


def test_policy_refusals():
    sara = employee()
    admin = decide(
        "What is the local administrator password for my laptop?",
        intake("What is the local administrator password for my laptop?"), sara,
    )
    assert admin.refusal is not None and admin.refusal.kind == "credentials"
    assert "SP-001" in admin.refusal.policy_refs

    phone_reset = decide(
        "Please read me a temporary password over the phone.",
        intake("Please read me a temporary password over the phone."), sara,
    )
    assert phone_reset.refusal is not None and phone_reset.refusal.kind == "password_channel"

    sms = decide(
        "Can you send me an SMS code for NakhlaKey?",
        intake("Can you send me an SMS code for NakhlaKey?"), sara,
    )
    assert sms.refusal is not None and sms.refusal.kind == "sms"

    hr = decide(
        "What is the salary of my colleague?",
        intake("What is the salary of my colleague?"), sara,
    )
    assert hr.refusal is not None and hr.refusal.kind == "hr_privacy"


def test_detect_affected_users():
    assert detect_affected_users("Four of us cannot work") == 4
    assert detect_affected_users("There are 5 people affected") == 5
    assert detect_affected_users("the whole office is down") == 4
    assert detect_affected_users("my laptop is broken") == 1


def test_ticket_request_phrases():
    assert detect_ticket_request("Please open a ticket")
    assert detect_ticket_request("This needs a ticket")
    assert detect_ticket_request("If it needs a ticket, raise it")
    assert not detect_ticket_request("Do I have any tickets?")
