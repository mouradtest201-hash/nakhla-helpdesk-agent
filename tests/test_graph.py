"""The agent must be a real LangGraph graph, not a single-node facade."""
from __future__ import annotations

from app.domain.policy import Decision, Refusal
from app.graph.agent import (
    _route_after_policy,
    _route_after_retrieval,
    build_approval_graph,
    build_chat_graph,
)


def test_chat_graph_is_a_multi_node_langgraph():
    nodes = set(build_chat_graph().get_graph().nodes)
    assert {
        "input_guard",
        "identity",
        "intake",
        "service_status",
        "policy",
        "retrieve",
        "escalate",
        "resolve",
        "finalize",
    } <= nodes
    assert len(nodes) > 10


def test_only_the_approval_graph_has_write_nodes():
    chat_nodes = set(build_chat_graph().get_graph().nodes)
    approval_nodes = set(build_approval_graph().get_graph().nodes)

    assert "write_ticket" not in chat_nodes
    assert "write_note" not in chat_nodes
    assert {"claim_action", "write_ticket", "write_note", "ticket_success"} <= approval_nodes


def test_policy_and_retrieval_conditional_routing():
    refusal = Refusal(kind="credentials", text="no", policy_refs=["SP-001"])
    blocked = Decision(
        topic="password",
        category="identity_access",
        priority="P3",
        assigned_team="Service Desk",
        refusal=refusal,
    )
    allowed = Decision(
        topic="vpn",
        category="network_access",
        priority="P2",
        assigned_team="Network Operations",
    )

    assert _route_after_policy({"decision": blocked}) == "refusal"
    assert (
        _route_after_policy({"decision": allowed, "email": None, "employee": None})
        == "phone_policy"
    )
    assert _route_after_retrieval({"should_draft": True}) == "draft_guard"
    assert _route_after_retrieval({"should_draft": False}) == "resolve"
