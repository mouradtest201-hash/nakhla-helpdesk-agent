"""KB-016: the Finance exception covers VPN/remote access only, and never
means "walk the employee through client troubleshooting".

Regression for a real session: a Finance Manager on GW-3 asked whether to
switch to GW-1 after NX-503, and the assistant printed the generic NX-503
playbook (including "switch the other gateway") before saying not to.
"""
from __future__ import annotations

import asyncio

import pytest

from app.agents.resolver import troubleshooting_reply
from app.domain.playbooks import detect_topic
from app.domain.policy import decide
from app.graph.chat import match_incident
from app.models import Employee, IntakeResult
from app.rag.retriever import RetrievalResult

STATUS = {
    "services": [
        {
            "id": "vpn",
            "name": "NakhlaConnect VPN",
            "status": "degraded",
            "components": [
                {"id": "GW-1", "name": "Gateway GW-1", "status": "operational"},
                {"id": "GW-2", "name": "Gateway GW-2", "status": "degraded"},
                {"id": "GW-3", "name": "Gateway GW-3 (Finance)", "status": "operational"},
            ],
        }
    ],
    "incidents": [
        {
            "id": "INC-2207",
            "status": "active",
            "priority": "P1",
            "service": "vpn",
            "title": "VPN gateway GW-2 degraded",
            "affected": "Employees connected through gateway GW-2 (Ramallah office and remote staff assigned to GW-2).",
            "symptoms": "NakhlaConnect shows NX-503 or disconnects every few minutes.",
            "error_codes": ["NX-503"],
            "workaround": "In NakhlaConnect open Settings > Gateway and select GW-1, then reconnect.",
        }
    ],
}


def employee(department: str = "Finance", *, gateway: str = "GW-3", **overrides) -> Employee:
    data = {
        "email": "fadi.mansour@nakhla.example",
        "name": "Fadi Mansour",
        "department": department,
        "title": "Analyst",
        "vpn_gateway": gateway,
        "account_status": "active",
    }
    data.update(overrides)
    return Employee(**data)


def intake(message: str, *, code: str | None = None, affected: int = 1, explicit: bool = False) -> IntakeResult:
    return IntakeResult(
        symptom=message,
        error_code=code,
        issue_topic=detect_topic(message) or "other",
        affected_users=affected,
        explicit_ticket_request=explicit,
    )


def reply_for(message: str, *, code: str | None, emp: Employee, status: dict | None = STATUS) -> str:
    result = intake(message, code=code)
    decision = decide(message, result, emp)
    reply, _ = asyncio.run(
        troubleshooting_reply(
            decision,
            result,
            emp,
            RetrievalResult(),
            use_llm=False,
            status=status,
            message=message,
        )
    )
    return reply


# ── policy scope ─────────────────────────────────────────────────────────
def test_finance_vpn_is_security_p2_for_security_operations():
    message = "I'm having VPN connection problems with error NX-503."
    decision = decide(message, intake(message, code="NX-503"), employee())
    assert (decision.category, decision.priority) == ("security", "P2")
    assert decision.assigned_team == "Security Operations"
    assert decision.finance_vpn is True
    assert {"KB-002", "KB-016"} <= set(decision.kb_refs)


def test_finance_vpn_with_four_users_is_p1():
    message = "Four of us in Finance see NX-503."
    decision = decide(message, intake(message, code="NX-503", affected=4), employee())
    assert decision.priority == "P1"


@pytest.mark.parametrize(
    "message,code",
    [
        ("I got a phishing email asking me to sign in again.", None),
        ("I lost my laptop at the airport yesterday.", None),
        ("My account keeps locking, three times today.", "NID-423"),
        ("How do I add a pivot table in Excel?", None),
    ],
)
def test_non_vpn_finance_requests_are_not_finance_vpn(message: str, code: str | None):
    decision = decide(message, intake(message, code=code), employee())
    assert decision.finance_vpn is False
    assert "KB-002" not in decision.kb_refs
    assert not any("Finance VPN exception" in line for line in decision.rationale)


# ── reply content ────────────────────────────────────────────────────────
def test_gateway_question_is_answered_no_without_client_steps():
    message = (
        "I'm having VPN connection problems with error NX-503. "
        "Should I switch to gateway GW-1 to fix it?"
    )
    reply = reply_for(message, code="NX-503", emp=employee())

    assert "don't switch gateways" in reply.lower()
    assert "Settings > Gateway" not in reply
    assert "switch to the other gateway" not in reply.lower()
    assert "Security Operations" in reply
    assert "P2" in reply
    assert "KB-016" in reply


def test_reply_reports_status_page_and_unrelated_incident():
    message = "VPN connection problems with error NX-503."
    reply = reply_for(message, code="NX-503", emp=employee())
    assert "GW-3 is operational" in reply
    assert "INC-2207" in reply
    assert "does not affect you" in reply


def test_nx503_wording_does_not_contradict_operational_gateway():
    """The code explanation must not imply an outage when GW-3 is up."""
    reply = reply_for("VPN connection problems with error NX-503.", code="NX-503", emp=employee())
    assert "is the gateway-unavailable code" in reply
    assert "isn't a gateway outage" in reply
    assert "means the gateway is unavailable" not in reply


def test_nx503_wording_reports_a_degraded_gateway():
    status = {
        "services": [
            {
                "id": "vpn",
                "name": "NakhlaConnect VPN",
                "status": "degraded",
                "components": [
                    {"id": "GW-3", "name": "Gateway GW-3 (Finance)", "status": "degraded"}
                ],
            }
        ],
        "incidents": [],
    }
    reply = reply_for(
        "VPN connection problems with error NX-503.", code="NX-503", emp=employee(), status=status
    )
    assert "GW-3 is degraded" in reply
    assert "gateway itself needs attention" in reply
    assert "isn't a gateway outage" not in reply


def test_reply_does_not_explain_gateway_change_when_not_asked():
    reply = reply_for("NX-503 when I connect from home.", code="NX-503", emp=employee())
    assert "don't switch gateways" not in reply.lower()
    assert "GW-3 is operational" in reply


def test_non_finance_vpn_still_gets_the_playbook_steps():
    message = "NakhlaConnect shows NX-503."
    reply = reply_for(message, code="NX-503", emp=employee("Sales", gateway="GW-1"))
    assert "Settings > Gateway" in reply
    assert "don't switch gateways" not in reply.lower()


def test_finance_vpn_offer_is_single_and_goes_to_security_operations():
    message = "NX-503 on my Finance laptop."
    reply = reply_for(message, code="NX-503", emp=employee())
    assert reply.lower().count("ticket") >= 1
    assert "Network Operations" not in reply


# ── incident matching ────────────────────────────────────────────────────
def test_gw2_incident_does_not_match_finance_on_gw3():
    assert match_incident(STATUS, "NX-503", "vpn", "NX-503 please help", employee()) is None


def test_gw2_incident_still_matches_employees_on_gw2():
    omar = employee("Sales", gateway="GW-2", email="omar.farah@nakhla.example")
    incident = match_incident(STATUS, "NX-503", "vpn", "NX-503 since this morning", omar)
    assert incident is not None
    assert incident["id"] == "INC-2207"


def test_gateway_incident_matches_finance_when_it_names_gw3():
    status = {
        "services": STATUS["services"],
        "incidents": [
            {
                "id": "INC-2301",
                "status": "active",
                "priority": "P1",
                "service": "vpn",
                "title": "VPN gateway GW-3 degraded",
                "affected": "Employees connected through gateway GW-3 (Finance).",
                "error_codes": ["NX-503"],
            }
        ],
    }
    incident = match_incident(status, "NX-503", "vpn", "NX-503", employee())
    assert incident is not None
    assert incident["id"] == "INC-2301"
