"""The escalator builds a schema-valid draft and never writes it."""
from __future__ import annotations

from app.agents.escalator import build_ticket
from app.domain.playbooks import playbook_for
from app.domain.policy import decide
from app.models import Employee, IntakeResult, TicketDraft
from app.rag.retriever import RetrievalResult


def make_employee(**overrides) -> Employee:
    data = dict(
        email="sara.mikhail@nakhla.example",
        name="Sara Mikhail",
        department="Support",
        location="Remote",
        device_os="Windows 11 24H2",
        vpn_gateway="GW-1",
        account_status="active",
    )
    data.update(overrides)
    return Employee(**data)


def make_intake(message: str, code: str | None = None) -> IntakeResult:
    return IntakeResult(
        symptom=message,
        error_code=code,
        issue_topic="vpn" if code and code.startswith("NX-") else "other",
        affected_users=1,
        explicit_ticket_request=True,
        attempted_steps=["Restarted NakhlaConnect", "Ran NakhlaManage Sync"],
    )


def test_draft_is_schema_valid_and_has_policy_fields():
    message = "My NakhlaConnect shows NX-112 and I cannot work."
    emp = make_employee()
    intake = make_intake(message, "NX-112")
    decision = decide(message, intake, emp)
    retrieval = RetrievalResult(docs=[], unknown_code=False)

    payload, summary = build_ticket(intake, decision, emp, retrieval)

    TicketDraft(**payload)  # raises if the API would reject it
    assert payload["category"] == "network_access"
    assert payload["priority"] == decision.priority
    assert payload["requester_email"] == emp.email
    assert payload["department"] == "Support"
    assert payload["attempted_steps"]
    assert "KB-002" in payload["kb_refs"]
    assert "create" in summary.lower()


def test_vip_and_affected_users_raise_priority_before_the_draft():
    message = "Four of us see NX-112 and cannot work."
    emp = make_employee(department="Executive Office", vip=True)
    intake = make_intake(message, "NX-112")
    intake.affected_users = 4
    decision = decide(message, intake, emp)
    retrieval = RetrievalResult(docs=[], unknown_code=False)

    payload, _ = build_ticket(intake, decision, emp, retrieval)
    assert payload["priority"] == "P1"
    assert payload["affected_users"] == 4
