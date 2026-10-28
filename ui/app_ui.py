"""Gradio client for the Nakhla IT Helpdesk Agent.

It is deliberately a *client*: it only knows the two public endpoints,
``POST /api/chat`` and ``POST /api/approve``.  It never calls the helpdesk API
or the model directly, so the approval gate and every guardrail stay on the
server side.

Run the agent first (port 8000), then:
    python ui/app_ui.py        ->  http://localhost:7860
"""
from __future__ import annotations

import os
import uuid

import gradio as gr
import httpx

APP_URL = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
DEFAULT_EMAIL = os.getenv("UI_DEFAULT_EMAIL", "tariq.hamdan@nakhla.example")
NO_CARD = (
    "### No draft to review\n"
    "If this issue needs a ticket, the assistant will prepare one here. "
    "Nothing is submitted without your approval."
)

APP_CSS = """
.gradio-container {
    --harbor: #123D3D;
    --palm: #33715B;
    --date: #B79B59;
    --mist: #EFF5F3;
    --paper: #FBFCFA;
    --ink: #1B2A28;
    max-width: 1500px;
    margin: 0 auto;
    padding: 28px clamp(16px, 4vw, 56px) 48px;
    color: var(--ink);
    background: var(--paper);
    font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
}

.gradio-container h1,
.gradio-container h2,
.gradio-container h3 {
    color: var(--harbor);
    letter-spacing: -0.035em;
}

.gradio-container .masthead {
    align-items: center;
    padding-bottom: 20px;
    margin-bottom: 34px;
    border-bottom: 1px solid #DCE7E2;
}

.gradio-container .brand-mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    border-radius: 4px 14px 4px 14px;
    background: var(--harbor);
    color: white;
    font-size: 22px;
    font-weight: 700;
}

.gradio-container .brand-mark p {
    margin: 0;
}

.gradio-container .brand-copy h2 {
    margin: 0;
    font-size: 21px;
    line-height: 1.15;
}

.gradio-container .brand-copy p {
    margin: 3px 0 0;
    color: #526661;
    font-size: 13px;
}

.gradio-container .intro {
    margin-bottom: 24px;
}

.gradio-container .intro h1 {
    margin: 0 0 8px;
    font-size: clamp(29px, 3.2vw, 42px);
    line-height: 1.08;
}

.gradio-container .intro p {
    max-width: 640px;
    color: #526661;
    line-height: 1.6;
}

.gradio-container .workspace {
    align-items: flex-start;
    gap: 28px;
}

.gradio-container .review-rail {
    padding: 20px;
    border: 1px solid #DCE7E2;
    border-top: 3px solid var(--palm);
    border-radius: 4px;
    background: #F5F8F6;
}

.gradio-container .review-rail h2,
.gradio-container .review-rail h3 {
    margin-top: 0;
}

.gradio-container #conversation {
    overflow: hidden;
    border: 1px solid #DCE7E2;
    border-radius: 4px;
    background: white;
}

.gradio-container #ticket-card {
    min-height: 126px;
    padding: 16px;
    border: 1px solid #DCE7E2;
    border-radius: 4px;
    background: white;
}

.gradio-container #ticket-card h3 {
    margin-top: 0;
    font-size: 17px;
}

.gradio-container #approve-ticket button {
    min-height: 46px;
    background: var(--palm);
    color: white;
}

.gradio-container #reject-ticket button {
    min-height: 46px;
    border: 1px solid #9AACA5;
    background: white;
    color: var(--ink);
}

.gradio-container button,
.gradio-container input,
.gradio-container textarea {
    border-radius: 4px;
}

.gradio-container button {
    transition: background-color 140ms ease, border-color 140ms ease;
}

.gradio-container button:focus-visible,
.gradio-container input:focus-visible,
.gradio-container textarea:focus-visible {
    outline: 3px solid var(--date);
    outline-offset: 2px;
}

.gradio-container .suggestions {
    margin-top: 10px;
}

.gradio-container .suggestions button {
    border: 1px solid #C9D9D2;
    background: var(--mist);
    color: var(--harbor);
}

.gradio-container .support-details {
    margin-top: 14px;
    border-top: 1px solid #DCE7E2;
}

.gradio-container code,
.gradio-container pre {
    font-family: "IBM Plex Mono", Consolas, monospace;
}

@media (max-width: 800px) {
    .gradio-container {
        padding: 18px 14px 32px;
    }

    .gradio-container .masthead {
        margin-bottom: 24px;
    }

    .gradio-container .workspace {
        flex-direction: column;
        gap: 18px;
    }

    .gradio-container .workspace > * {
        width: 100%;
    }

    .gradio-container .review-rail {
        padding: 16px;
    }
}

@media (prefers-reduced-motion: reduce) {
    .gradio-container *,
    .gradio-container *::before,
    .gradio-container *::after {
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
    }
}
"""

EXAMPLES = [
    ["NakhlaID says NID-423 when I try to sign in. What should I do?"],
    ["NakhlaConnect shows NX-781 and the old guide says version 4.8 is fine. What now?"],
    ["My VPN shows NX-503 and I cannot reach the office. Please help."],
    ["I'm in Finance and NakhlaConnect shows NX-403 on GW-3. Please open a ticket."],
    ["I left my phone at home. How do I sign in with MFA?"],
    ["The Nakhla Workspace VDI portal will not load. Please open a ticket."],
    ["Show me my ticket history."],
]


def call_app(path: str, body: dict) -> dict:
    try:
        response = httpx.post(f"{APP_URL}{path}", json=body, timeout=180)
    except httpx.HTTPError as exc:
        return {"reply": f"Cannot reach the agent at {APP_URL} ({exc.__class__.__name__}). Is it running?"}
    try:
        data = response.json()
    except ValueError:
        data = {"reply": response.text[:500]}
    if response.status_code >= 400:
        data = {"reply": f"The agent answered HTTP {response.status_code}: {str(data)[:400]}"}
    return data if isinstance(data, dict) else {"reply": str(data)}


def side_panels(data: dict) -> tuple[str, str, str]:
    citations = data.get("citations") or []
    sources = "### Sources\n" + (
        "\n".join(
            f"- **{c.get('id', '?')}** {c.get('title', '')}" if isinstance(c, dict) else f"- {c}"
            for c in citations
        )
        or "_No knowledge-base article cited in the last answer._"
    )

    calls = data.get("tool_calls") or []
    tools = "### Tool calls (last turn)\n" + (
        "\n".join(
            f"- `{t.get('tool', '?')}` {'ok' if t.get('ok') else 'FAILED'} · {t.get('ms', '?')} ms"
            for t in calls
            if isinstance(t, dict)
        )
        or "_none_"
    )

    result = data.get("result") or {}
    state = data.get("state")
    outcome = ""
    if result:
        outcome = "### Result\n" + "\n".join(f"- **{k}**: `{v}`" for k, v in result.items())
    if state:
        outcome += f"\n\n_Workflow state: `{state}`_"
    return sources, tools, outcome


def ticket_card(pending: dict | None) -> str:
    if not pending:
        return NO_CARD
    payload = pending.get("payload") or {}
    lines = [
        "### Ticket draft - waiting for your approval",
        f"**{pending.get('summary', '')}**",
        "",
    ]
    for key in ("subject", "category", "priority", "department", "requester_email", "affected_users"):
        if key in payload:
            lines.append(f"- **{key.replace('_', ' ')}**: {payload[key]}")
    if payload.get("attempted_steps"):
        lines.append("- **attempted steps**: " + "; ".join(map(str, payload["attempted_steps"])))
    if "kb_refs" in payload:
        lines.append("- **KB refs**: " + (", ".join(payload["kb_refs"]) or "none"))
    if payload.get("description"):
        lines += ["", f"> {payload['description']}"]
    lines += ["", f"_Action id: `{pending.get('action_id', '?')}` - nothing is written until you approve._"]
    return "\n".join(lines)


def show(data: dict, history: list):
    history = list(history) + [{"role": "assistant", "content": data.get("reply") or "(empty reply)"}]
    pending = data.get("pending_action") or None
    sources, tools, outcome = side_panels(data)
    enabled = gr.update(interactive=bool(pending))
    return history, pending, ticket_card(pending), enabled, enabled, sources, tools, outcome


def send(message: str, history: list, session_id: str, email: str):
    if not message.strip():
        return ("", history, *[gr.skip()] * 7)
    body = {"session_id": session_id, "message": message}
    if email.strip():
        body["user"] = {"email": email.strip().lower()}
    history = list(history) + [{"role": "user", "content": message}]
    return ("", *show(call_app("/api/chat", body), history))


def decide(approved: bool, history: list, session_id: str, pending: dict | None):
    if not pending:
        return show({"reply": "There is no draft to approve or reject."}, history)
    history = list(history) + [{"role": "user", "content": "Approve" if approved else "Reject"}]
    data = call_app(
        "/api/approve",
        {"session_id": session_id, "action_id": pending.get("action_id"), "approved": approved},
    )
    return show(data, history)


def reset(history: list):
    new_session = f"ui-{uuid.uuid4().hex[:10]}"
    return (
        new_session,
        [],
        None,
        NO_CARD,
        gr.update(interactive=False),
        gr.update(interactive=False),
        "### Sources\n_No answer yet._",
        "### Tool calls (last turn)\n_none_",
        "",
    )


with gr.Blocks(title="Nakhla IT Helpdesk") as demo:
    session = gr.State(lambda: f"ui-{uuid.uuid4().hex[:10]}")
    pending_state = gr.State(None)

    with gr.Row(elem_classes=["masthead"]):
        gr.Markdown("N", elem_classes=["brand-mark"])
        gr.Markdown("## Nakhla\nIT support", elem_classes=["brand-copy"])
        reset_btn = gr.Button("Start a new chat", scale=0)

    gr.Markdown(
        "# What is getting in the way?\n"
        "Describe the error or question. I’ll check current Nakhla guidance and show you "
        "any ticket draft before it is created.",
        elem_classes=["intro"],
    )

    with gr.Row(elem_classes=["workspace"]):
        with gr.Column(scale=3, min_width=340):
            with gr.Row():
                email = gr.Textbox(
                    label="Work email",
                    value=DEFAULT_EMAIL,
                    scale=4,
                    info="Used to check department guidance and your ticket history.",
                )
            chat = gr.Chatbot(label="Conversation", height=480, elem_id="conversation")
            msg = gr.Textbox(
                label="Message for IT support",
                placeholder="Describe the IT problem, e.g. NakhlaConnect shows NX-781",
                submit_btn=True,
            )
            with gr.Group(elem_classes=["suggestions"]):
                gr.Examples(
                    examples=EXAMPLES,
                    inputs=msg,
                    label="Start with a common issue",
                    examples_per_page=4,
                )
        with gr.Column(scale=2, min_width=300, elem_classes=["review-rail"]):
            gr.Markdown("### Ticket approval\nReview any proposed ticket before it is sent.")
            with gr.Group():
                card = gr.Markdown(NO_CARD, elem_id="ticket-card")
                with gr.Row():
                    approve_btn = gr.Button(
                        "Approve and create ticket",
                        variant="primary",
                        interactive=False,
                        elem_id="approve-ticket",
                    )
                    reject_btn = gr.Button(
                        "Reject draft",
                        variant="secondary",
                        interactive=False,
                        elem_id="reject-ticket",
                    )
            outcome = gr.Markdown("")
            with gr.Accordion("Sources", open=False, elem_classes=["support-details"]):
                sources = gr.Markdown("_No answer yet._")
            with gr.Accordion("System activity", open=False, elem_classes=["support-details"]):
                tools = gr.Markdown("_No activity yet._")

    outputs = [chat, pending_state, card, approve_btn, reject_btn, sources, tools, outcome]
    msg.submit(send, [msg, chat, session, email], [msg, *outputs])
    approve_btn.click(lambda h, s, p: decide(True, h, s, p), [chat, session, pending_state], outputs)
    reject_btn.click(lambda h, s, p: decide(False, h, s, p), [chat, session, pending_state], outputs)
    reset_btn.click(
        reset,
        [chat],
        [session, chat, pending_state, card, approve_btn, reject_btn, sources, tools, outcome],
    )

if __name__ == "__main__":
    demo.launch(
        server_name=os.getenv("UI_HOST", "127.0.0.1"),
        server_port=int(os.getenv("UI_PORT", "7860")),
        css=APP_CSS,
    )
