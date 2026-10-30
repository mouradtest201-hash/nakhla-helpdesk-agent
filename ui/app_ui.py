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

SCENARIO_LABELS = [
    ("NakhlaID sign-in", "Error NID-423"),
    ("NakhlaConnect update", "Error NX-781"),
    ("VPN connection", "Error NX-503"),
    ("Finance VPN access", "Error NX-403"),
    ("MFA without your phone", "Sign-in options"),
    ("Workspace VDI", "Portal unavailable"),
    ("Ticket history", "Review past requests"),
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
        outcome += f"\n\n_Agent graph state: `{state}`_"
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


UI_CSS = """
:root {
  color-scheme: light;
  --page: oklch(1 0 0);
  --surface: oklch(0.985 0.002 286);
  --surface-raised: oklch(0.967 0.003 286);
  --surface-soft: oklch(0.985 0.002 286);
  --line: oklch(0.919 0.005 286);
  --line-soft: oklch(0.919 0.005 286);
  --line-strong: oklch(0.82 0.008 286);
  --text: oklch(0.145 0.005 286);
  --muted: oklch(0.5 0.016 286);
  --quiet: oklch(0.43 0.014 286);
  --accent: oklch(0.546 0.245 263);
  --accent-hover: oklch(0.488 0.243 264);
  --accent-soft: oklch(0.965 0.025 260);
  --accent-fg: oklch(1 0 0);
  --danger: oklch(0.577 0.245 27);
  --success: oklch(0.627 0.194 149);
  --font-ui: "Geist", "Segoe UI Variable", "Aptos", "Segoe UI", sans-serif;
  --font-mono: "Cascadia Code", "Consolas", monospace;
  font-family: var(--font-ui);
  font-optical-sizing: auto;
}

.dark {
  color-scheme: dark;
  --page: oklch(0.145 0.005 286);
  --surface: oklch(0.19 0.006 286);
  --surface-raised: oklch(0.245 0.006 286);
  --surface-soft: oklch(0.17 0.006 286);
  --line: oklch(0.3 0.01 286);
  --line-soft: oklch(0.255 0.007 286);
  --line-strong: oklch(0.42 0.013 286);
  --text: oklch(0.985 0.002 286);
  --muted: oklch(0.72 0.015 286);
  --quiet: oklch(0.57 0.014 286);
  --accent: oklch(0.637 0.237 260);
  --accent-hover: oklch(0.707 0.191 260);
  --accent-soft: oklch(0.25 0.06 260);
  --accent-fg: oklch(0.145 0.005 286);
  --danger: oklch(0.637 0.237 25);
  --success: oklch(0.723 0.219 149);
}

html, body, #root {
  min-height: 100%;
  background: var(--page) !important;
}

body, .gradio-container {
  color: var(--text) !important;
  font-family: var(--font-ui) !important;
}

.gradio-container {
  width: min(1440px, 100%) !important;
  max-width: 1440px !important;
  min-height: 100vh;
  margin: 0 auto !important;
  padding: 28px clamp(16px, 3vw, 42px) 48px !important;
  background: var(--page) !important;
}

footer {
  display: none !important;
}

#app-shell {
  width: 100%;
}

.app-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 0 2px 22px;
  border-bottom: 1px solid var(--line-soft);
}

#app-header-row {
  align-items: center;
  gap: 12px !important;
}

#app-header-row > .gradio-html {
  flex: 1 1 auto;
  min-width: 0;
}

#theme-toggle {
  flex: 0 0 auto;
}

.brand-lockup, .topbar-status, .brand-title {
  display: flex;
  align-items: center;
}

.brand-lockup { gap: 12px; }
.brand-mark {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border: 1px solid color-mix(in oklch, var(--accent) 38%, transparent);
  border-radius: 11px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.06em;
}

.brand-copy { display: grid; gap: 2px; }
.brand-title { gap: 8px; color: var(--text); font-size: 14px; font-weight: 650; }
.brand-divider { color: var(--quiet); font-weight: 400; }
.brand-subtitle { color: var(--muted); font-size: 12px; }
.topbar-status { gap: 8px; color: var(--muted); font-size: 12px; }
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in oklch, var(--accent) 15%, transparent);
}

#workspace {
  align-items: stretch;
  gap: 18px !important;
  margin-top: 22px;
}

#chat-pane, #support-rail {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 12px;
  background: var(--surface);
}

#requester-pane { padding: 20px !important; }
#chat-pane { padding: clamp(18px, 2.4vw, 30px) !important; }
#support-rail { padding: 20px !important; }

.section-kicker {
  margin: 0 0 9px;
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.chat-intro h1 {
  margin: 0;
  color: var(--text);
  font-size: clamp(24px, 3vw, 34px);
  font-weight: 620;
  letter-spacing: -0.045em;
  line-height: 1.15;
}

.chat-intro p {
  max-width: 64ch;
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.6;
}

#identity-row {
  align-items: end;
  gap: 12px !important;
  margin: 24px 0 14px;
}

#identity-email label, #message-composer label {
  color: var(--muted) !important;
  font-size: 12px !important;
  font-weight: 550 !important;
}

#identity-email textarea, #message-composer textarea {
  border: 1px solid var(--line-strong) !important;
  border-radius: 8px !important;
  background: var(--surface-soft) !important;
  color: var(--text) !important;
  transition: border-color 160ms ease-out, box-shadow 160ms ease-out;
}

#identity-email textarea { min-height: 42px !important; }
#identity-email textarea:focus, #message-composer textarea:focus {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px color-mix(in oklch, var(--accent) 14%, transparent) !important;
}

#new-session {
  min-height: 42px;
  border: 1px solid var(--line-strong) !important;
  border-radius: 8px !important;
  background: transparent !important;
  color: var(--muted) !important;
  transition: border-color 160ms ease-out, color 160ms ease-out, background 160ms ease-out;
}

#new-session:hover {
  border-color: var(--line) !important;
  background: var(--surface-raised) !important;
  color: var(--text) !important;
}

#chat-history {
  min-height: 380px;
  border: 1px solid var(--line-soft) !important;
  border-radius: 9px !important;
  background: var(--page) !important;
}

#chat-history [data-testid="bot"], #chat-history [data-testid="user"] {
  border-radius: 10px !important;
}

#message-composer { margin-top: 14px; }
#message-composer textarea {
  min-height: 94px !important;
  padding: 13px 15px !important;
  font-size: 14px !important;
  line-height: 1.55 !important;
}

#message-composer textarea::placeholder, #identity-email textarea::placeholder {
  color: var(--quiet) !important;
}

#message-composer button {
  border: 1px solid var(--accent) !important;
  border-radius: 7px !important;
  background: var(--accent) !important;
  color: var(--accent-fg) !important;
  font-weight: 650 !important;
  transition: background 160ms ease-out, transform 160ms ease-out;
}

#message-composer button:hover {
  border-color: var(--accent-hover) !important;
  background: var(--accent-hover) !important;
}

#message-composer button:active, #ticket-actions button:active, #new-session:active {
  transform: translateY(1px);
}

#quick-starts { margin-top: 18px; }
#quick-starts h3 {
  margin: 0 0 10px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}

#quick-starts button {
  width: 100%;
  border: 1px solid var(--line-soft) !important;
  border-radius: 7px !important;
  background: var(--surface-soft) !important;
  color: var(--muted) !important;
  text-align: left;
  transition: border-color 160ms ease-out, background 160ms ease-out, color 160ms ease-out;
}

#quick-starts button:hover {
  border-color: var(--line) !important;
  background: var(--surface-raised) !important;
  color: var(--text) !important;
}

#quick-starts button + button {
  margin-top: 5px;
}

.scenario-title {
  display: block;
  color: var(--text);
  font-size: 12px;
  font-weight: 560;
}

.scenario-hint {
  display: block;
  margin-top: 2px;
  color: var(--muted);
  font-size: 11px;
}

#theme-toggle {
  min-height: 36px;
  border: 1px solid var(--line-strong) !important;
  border-radius: 6px !important;
  background: transparent !important;
  color: var(--muted) !important;
  font-size: 12px !important;
}

#theme-toggle:hover {
  background: var(--surface-raised) !important;
  color: var(--text) !important;
}

.rail-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line-soft);
}

.rail-heading h2 {
  margin: 0;
  color: var(--text);
  font-size: 16px;
  font-weight: 620;
  letter-spacing: -0.02em;
}

.rail-heading p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}

.draft-label {
  flex: none;
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--quiet);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

#ticket-card {
  margin-top: 16px;
  padding: 15px !important;
  border: 1px solid var(--line-soft);
  border-radius: 9px;
  background: var(--page);
  color: var(--muted);
  font-size: 13px;
  line-height: 1.65;
}

#ticket-card h3 {
  margin: 0 0 10px;
  color: var(--text);
  font-size: 13px;
  font-weight: 620;
}

#ticket-card p { margin: 6px 0; }
#ticket-card strong { color: var(--text); }
#ticket-card code {
  color: var(--accent);
  font-family: var(--font-mono);
  overflow-wrap: anywhere;
}

.approval-note {
  margin: 12px 0 10px;
  padding-left: 10px;
  border-left: 2px solid var(--accent);
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
}

#approval-note {
  margin: 10px 0 12px;
  padding-left: 10px;
  border-left: 2px solid var(--accent);
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
}

#ticket-actions { gap: 8px !important; }
#ticket-actions button {
  min-height: 40px;
  border-radius: 7px !important;
  font-size: 12px !important;
  font-weight: 600 !important;
  transition: filter 160ms ease-out, transform 160ms ease-out;
}

#ticket-actions button.primary {
  border: 1px solid var(--accent) !important;
  background: var(--accent) !important;
  color: var(--accent-fg) !important;
}

#ticket-actions button.stop {
  border: 1px solid var(--line) !important;
  background: transparent !important;
  color: var(--muted) !important;
}

#ticket-actions button:disabled {
  opacity: 0.42 !important;
  cursor: not-allowed !important;
}

#ticket-actions button:not(:disabled):hover { filter: brightness(1.08); }

#sources-panel, #tools-panel {
  margin-top: 14px;
  border: 1px solid var(--line-soft) !important;
  border-radius: 8px !important;
  background: var(--surface-soft) !important;
}

#sources-panel summary, #tools-panel summary {
  padding: 12px 13px !important;
  color: var(--text) !important;
  font-size: 12px !important;
  font-weight: 580 !important;
}

#sources-panel > div, #tools-panel > div {
  padding: 0 13px 13px !important;
  color: var(--muted) !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
}

#sources-panel h3, #tools-panel h3, #panel-outcome h3 {
  margin: 0 0 8px;
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
}

#sources-panel a, #tools-panel code, #panel-outcome code {
  color: var(--accent);
  font-family: var(--font-mono);
}

#panel-outcome {
  margin-top: 14px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

button:focus-visible, a:focus-visible, summary:focus-visible, textarea:focus-visible, input:focus-visible {
  outline: 2px solid var(--accent) !important;
  outline-offset: 3px !important;
}

@media (max-width: 900px) {
  .gradio-container { padding: 18px 14px 32px !important; }
  #workspace { flex-direction: column !important; }
  #requester-pane, #chat-pane, #support-rail { width: 100% !important; flex: 1 1 auto !important; }
  #requester-pane { order: 2; }
  #chat-pane { order: 1; }
  #support-rail { order: 3; }
  #chat-history { min-height: 320px; }
}

@media (max-width: 560px) {
  .app-topbar { align-items: flex-start; }
  .topbar-status { max-width: 125px; text-align: right; line-height: 1.4; }
  .brand-subtitle { max-width: 220px; }
  #identity-row { align-items: stretch; flex-direction: column !important; }
  #identity-email, #new-session { width: 100% !important; }
  #ticket-actions { flex-direction: column !important; }
  #ticket-actions button { width: 100% !important; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}

@media (prefers-contrast: more) {
  :root, .dark {
    --line: oklch(0.48 0.01 286);
    --line-soft: oklch(0.39 0.01 286);
    --line-strong: oklch(0.54 0.01 286);
    --muted: oklch(0.4 0.01 286);
  }
  .dark { --muted: oklch(0.82 0.01 286); }
}
"""

with gr.Blocks(title="Nakhla IT Helpdesk Agent", fill_width=True) as demo:
    session = gr.State(lambda: f"ui-{uuid.uuid4().hex[:10]}")
    pending_state = gr.State(None)
    scenario_buttons = []

    with gr.Column(elem_id="app-shell"):
        with gr.Row(elem_id="app-header-row"):
            gr.HTML(
                '<header class="app-topbar">'
                '<div class="brand-lockup">'
                '<div class="brand-mark" aria-hidden="true">N</div>'
                '<div class="brand-copy">'
                '<div class="brand-title">Nakhla <span class="brand-divider">/</span> IT Support</div>'
                '<div class="brand-subtitle">Grounded answers. Human-approved tickets.</div>'
                '</div></div>'
                '<div class="topbar-status"><span class="status-dot" aria-hidden="true"></span>'
                '<span>Approval protected</span></div>'
                '</header>'
            )
            theme_btn = gr.Button("Toggle theme", elem_id="theme-toggle")
        with gr.Row(elem_id="workspace"):
            with gr.Column(scale=3, elem_id="requester-pane"):
                gr.HTML(
                    '<section class="chat-intro">'
                    '<p class="section-kicker">Requester</p>'
                    '<h1>Your support desk.</h1>'
                    '<p>Choose an email so the assistant can check the right directory and ticket context.</p>'
                    '</section>'
                )
                email = gr.Textbox(
                    label="Work email",
                    value=DEFAULT_EMAIL,
                    info="Used for department rules and ticket history.",
                    elem_id="identity-email",
                )
                reset_btn = gr.Button("New conversation", elem_id="new-session")
                with gr.Column(elem_id="quick-starts"):
                    gr.Markdown("### Try a scenario")
                    for title, hint in SCENARIO_LABELS:
                        scenario_buttons.append(
                            gr.Button(
                                f"{title}  ·  {hint}",
                                elem_classes=["scenario-button"],
                            )
                        )

            with gr.Column(scale=7, elem_id="chat-pane"):
                gr.HTML(
                    '<section class="chat-intro">'
                    '<p class="section-kicker">Conversation</p>'
                    '<h1>What can we help you with?</h1>'
                    '<p>Describe the problem and include any error code. Answers draw on current support '
                    'guidance. If a ticket is needed, review it before anything is sent.</p>'
                    '</section>'
                )
                chat = gr.Chatbot(label="Conversation", height=500, elem_id="chat-history")
                msg = gr.Textbox(
                    label="Describe your issue",
                    placeholder="Describe the problem, including any error code you see…",
                    submit_btn=True,
                    elem_id="message-composer",
                )

            with gr.Column(scale=4, elem_id="support-rail"):
                gr.HTML(
                    '<div class="rail-heading">'
                    '<div><h2>Ticket approval</h2>'
                    '<p>Review proposed changes before anything is written.</p></div>'
                    '<span class="draft-label">Human control</span>'
                    '</div>'
                )
                card = gr.Markdown(NO_CARD, elem_id="ticket-card")
                gr.Markdown(
                    "Nothing is sent to the helpdesk until you approve the draft.",
                    elem_id="approval-note",
                )
                with gr.Row(elem_id="ticket-actions"):
                    approve_btn = gr.Button("Approve ticket", variant="primary", interactive=False)
                    reject_btn = gr.Button("Reject draft", variant="stop", interactive=False)
                outcome = gr.Markdown("", elem_id="panel-outcome")
                with gr.Accordion("Sources used", open=False, elem_id="sources-panel"):
                    sources = gr.Markdown("### Sources\n_No answer yet._")
                with gr.Accordion("System activity", open=False, elem_id="tools-panel"):
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
    theme_btn.click(
        fn=None,
        inputs=None,
        outputs=None,
        js="() => { document.documentElement.classList.toggle('dark'); }",
        show_progress="hidden",
    )
    for button, (prompt,) in zip(scenario_buttons, EXAMPLES):
        button.click(
            fn=lambda text=prompt: gr.update(value=text),
            inputs=None,
            outputs=msg,
            show_progress="hidden",
        )

if __name__ == "__main__":
    demo.launch(
        server_name=os.getenv("UI_HOST", "127.0.0.1"),
        server_port=int(os.getenv("UI_PORT", "7860")),
        css=UI_CSS,
    )
