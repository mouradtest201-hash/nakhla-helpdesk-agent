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
NO_CARD = "_No ticket draft waiting for approval._"

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


with gr.Blocks(title="Nakhla IT Helpdesk Agent") as demo:
    session = gr.State(lambda: f"ui-{uuid.uuid4().hex[:10]}")
    pending_state = gr.State(None)

    gr.Markdown(
        "## Nakhla IT Helpdesk\n"
        "Grounded IT support for Nakhla Systems (a fictional company). Answers cite current KB/SP "
        "documents; any ticket is **drafted first** and only written after you approve it.\n\n"
        f"`{APP_URL}`"
    )
    with gr.Row():
        with gr.Column(scale=3):
            with gr.Row():
                email = gr.Textbox(
                    label="Your work email",
                    value=DEFAULT_EMAIL,
                    scale=4,
                    info="Used for department rules, VIP priority and ticket history.",
                )
                reset_btn = gr.Button("New session", scale=1)
            chat = gr.Chatbot(label="Chat", height=430)
            msg = gr.Textbox(
                label="Message",
                placeholder="Describe the IT problem, e.g. NakhlaConnect shows NX-781",
                submit_btn=True,
            )
            gr.Examples(examples=EXAMPLES, inputs=msg)
        with gr.Column(scale=2):
            with gr.Group():
                card = gr.Markdown(NO_CARD, padding=True)
                with gr.Row():
                    approve_btn = gr.Button("Approve write", variant="primary", interactive=False)
                    reject_btn = gr.Button("Reject", variant="stop", interactive=False)
            outcome = gr.Markdown("")
            sources = gr.Markdown("### Sources\n_No answer yet._")
            tools = gr.Markdown("### Tool calls (last turn)\n_none_")

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
    )
