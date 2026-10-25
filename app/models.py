"""Pydantic models for the public API contract and internal actions."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

Priority = Literal["P1", "P2", "P3", "P4"]
Category = Literal[
    "network_access",
    "identity_access",
    "hardware",
    "software",
    "email_collab",
    "security",
    "onboarding",
    "other",
]
Department = Literal[
    "Engineering",
    "Finance",
    "HR",
    "Sales",
    "Support",
    "Executive Office",
    "Contractors",
]


class Citation(BaseModel):
    id: str
    title: str = ""
    url: str | None = None


class ToolCall(BaseModel):
    """The contract only requires tool, ok and ms; keep it exactly that."""

    tool: str
    ok: bool
    ms: int


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class PendingAction(BaseModel):
    action_id: str
    type: Literal["create_ticket", "add_ticket_note"]
    summary: str
    payload: dict[str, Any]


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    state: str | None = None
    pending_action: PendingAction | None = None
    result: dict[str, Any] | None = None
    citations: list[Citation] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None


class ChatRequest(BaseModel):
    session_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    user: dict[str, Any] | None = None


class ApproveRequest(BaseModel):
    session_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    # The contract shows action_id, but a client may approve "the latest
    # pending action" for a session.  We resolve that safely server-side.
    action_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    approved: bool = True
    approve_again: bool = False


class TicketDraft(BaseModel):
    """The exact body of POST /api/tickets (additionalProperties is false)."""

    model_config = ConfigDict(extra="forbid")

    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=120)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)]
    category: Category
    priority: Priority
    requester_email: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            to_lower=True,
            pattern=r"^[a-z0-9._-]+@nakhla\.example$",
        ),
    ]
    department: Department
    attempted_steps: Annotated[
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]],
        Field(min_length=1, max_length=20),
    ]
    kb_refs: Annotated[
        list[Annotated[str, StringConstraints(pattern=r"^(KB|SP)-\d{3}$")]],
        Field(default_factory=list, max_length=10),
    ]
    affected_users: int = Field(default=1, ge=1, le=1000)


class IntakeResult(BaseModel):
    """Structured result of the intake role."""

    error_code: str | None = None
    symptom: str = ""
    issue_topic: str = "other"
    affected_users: int = 1
    explicit_ticket_request: bool = False
    user_claim_department: str | None = None
    attempted_steps: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    asks_for_ticket_update: bool = False
    is_followup: bool = False
    resolution_failed: bool = False


class Employee(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    employee_id: str = ""
    name: str = ""
    department: str = ""
    title: str = ""
    location: str = ""
    device_os: str = ""
    vpn_client_version: str | None = None
    vpn_gateway: str | None = None
    mfa_enrolled: bool = False
    vip: bool = False
    contractor: bool = False
    contract_end: str | None = None
    manager_email: str | None = None
    account_status: str = "active"


class ActionRecord(BaseModel):
    action_id: str
    session_id: str
    type: Literal["create_ticket", "add_ticket_note"] = "create_ticket"
    summary: str = ""
    payload: dict[str, Any]
    status: Literal["proposed", "executing", "executed", "rejected", "failed"] = "proposed"
    result: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""
