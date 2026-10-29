# Nakhla IT Helpdesk Agent

A production-shaped **agentic IT support assistant** for the fictional Nakhla Systems dataset.
It retrieves current knowledge-base policy, routes and prioritises tickets with a deterministic
policy engine, calls the company systems through **MCP**, remembers employees across sessions, and
**never writes to the system of record without a separate human approval**.

Built for the Agentic AI Bootcamp Scenario 2 brief. The simulated helpdesk API, MCP server and
dataset are the provided kit and are **not modified**; everything under `app/`, `tests/`, `evals/`
and `ui/` is the implementation.

```
employee ── POST /api/chat ──► chat LangGraph
                                ├─ input guard / identity / long-term memory
                                ├─ intake ──► service status ──► policy engine
                                ├─ refusal / incident / clarification branches
                                └─ retrieval ──► resolver or ticket draft

          ◄── reply + citations + pending_action ──┘

employee ── POST /api/approve ──► approval LangGraph
                                   └─ atomic claim ──► MCP create_ticket
                                                      (idempotency_key = action_id)
```

## What it does

| Requirement | Implementation |
|---|---|
| **LangGraph agent graph, 2+ roles** | Three explicit roles: **intake** (structured facts), **resolver** (grounded answer), **escalator** (schema-valid ticket draft). A compiled LangGraph `StateGraph` coordinates them with explicit nodes and conditional edges, and can run without the LLM. |
| **MCP + a real API** | The app talks MCP stdio to `services/helpdesk_mcp.py`: `lookup_employee`, `get_service_status`, `search_tickets`, `get_ticket`, `create_ticket`, `add_ticket_note`. The last two are **only** reachable from the approval gate. |
| **Short- and long-term memory** | Short term: persisted session turns, last intake/decision, pending action and status cache (SQLite + in-process cache). Long term: employee profile cache, saved preferences, draft/ticket facts, and ticket history fetched from the helpdesk. |
| **Guardrails + approval gate** | Input checks, untrusted-source scrubbing, prompt-injection handling, secret redaction, citation allow-list, no unbacked ticket IDs, strict policy refusals, and a single write path. |
| **10+ evals, scorecard, fixed failure** | 10 provided cases **10/10**, 20 additional cases **20/20**, 27 unit tests, and eight targeted hardening fixes (see [Evaluation](#evaluation)). |
| **Contract + one-command start + UI** | `GET /health`, `POST /api/chat`, `POST /api/approve`; `.\run.ps1`, `./run.sh`, or `docker compose up --build`; Next.js client with sources, tool calls and a draft-approval card. |

## Quick start

Requirements: Python 3.12, Node.js 22.13+, npm, an OpenRouter API key, and (optionally) Docker.

```powershell
# Windows
copy .env.example .env        # put OPENROUTER_API_KEY=... in .env
.\run.ps1
```

```bash
# macOS / Linux
cp .env.example .env
./run.sh
```

Or inside an existing venv:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Push-Location web; npm ci; Pop-Location
python run.py
```

The runner starts the helpdesk API on **:8102**, the agent on **:8000**, and the Next.js UI on
**:3000**. Node.js 22.13+ and npm are required for the web UI; `run.ps1` and `run.sh` install its
dependencies on first use. `Ctrl+C` stops all three. `--no-ui` skips the web client;
`--no-helpdesk` uses an API that is already running.

Docker (starts helpdesk, agent and UI in one command):

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| Agent API docs | http://localhost:8000/docs |
| Web UI | http://localhost:3000 |
| Simulated helpdesk docs | http://localhost:8102/docs |

The first start builds a 111-chunk index from the Markdown **and PDF** knowledge base. Embeddings
are optional: if OpenRouter is unavailable the retriever falls back to exact-code + keyword search,
and replies fall back to verified deterministic templates.

## API contract

### `GET /health`

```json
{"status": "ok", "service": "nakhla-helpdesk-agent", "version": "1.0.0"}
```

### `POST /api/chat`

```json
{
  "session_id": "demo-1",
  "message": "NakhlaConnect shows NX-112 and I cannot work. Please open a ticket.",
  "user": {"email": "sara.mikhail@nakhla.example"}
}
```

```json
{
  "session_id": "demo-1",
  "reply": "**Ticket draft - not created yet.** ...",
  "state": "awaiting_approval",
  "pending_action": {
    "action_id": "act_7f3a...",
    "type": "create_ticket",
    "summary": "Create network_access P2 ticket for sara.mikhail@nakhla.example: ...",
    "payload": {"subject": "...", "category": "network_access", "priority": "P2", "...": "..."}
  },
  "result": null,
  "citations": [{"id": "KB-002", "title": "NakhlaConnect VPN: error codes and fixes", "url": null}],
  "tool_calls": [{"tool": "lookup_employee", "ok": true, "ms": 41}],
  "usage": {"prompt_tokens": 1203, "completion_tokens": 94, "cost_usd": 0.000631}
}
```

### `POST /api/approve`

```json
{"session_id": "demo-1", "action_id": "act_7f3a...", "approved": true}
```

Only this endpoint can execute the proposed write. It sends exactly the draft payload over MCP with
`idempotency_key = action_id`, and copies the real `ticket_id` from the response into `result`.
`action_id` may be omitted to approve the latest pending action for the session. `approved: false`
writes nothing. Re-approving the same action returns the same ticket and never creates a duplicate.

The app also implements the contract's extra state fields: `result` stays `null` unless a write
really succeeded, and `citations` never include deprecated or draft documents.

## Policy precedence, in code

The model does not decide policy. `app/domain/policy.py` applies the manifest rules before a reply is
written; the resolver receives the decision as facts.

| Rule from the dataset | Where it shows up |
|---|---|
| Security policies (`SP-`) override KB articles | `SP-001` forbids phone password resets and admin-credential disclosure even when an old KB guide says otherwise. |
| Only current documents are sources | `KB-001` (deprecated) and `KB-005` (superseded) are never cited; `KB-018` (draft/wiki) is never ingested. |
| Authoritative + newer wins | `KB-002` v5.4 beats `KB-001` v3; `KB-015` v3's “more than 3 users = P1” beats the old 5+ rule in `KB-017`. |
| Department exceptions override general rules | Finance VPN → `security`, Security Operations, P2 (P1 above 3 users); contractors get VDI, not VPN; Executive Office is always P1. |
| Documents beat ticket history | A solved historical ticket cannot override a current KB/SP rule; history is used only for context and duplicate awareness. |

Priority is deterministic too: VIP/Executive Office → P1, more than three affected users → P1,
Finance VPN → P2 (or P1 with more than three users), repeated lockouts → security P2,
onboarding without a laptop → P2, missing onboarding account/access → P3.

## Retrieval

`app/rag/ingest.py` parses YAML front matter and both PDFs (`KB-012`, `KB-013`), scrubs untrusted
text, and splits the knowledge base into 111 overlapping chunks. `app/rag/retriever.py` combines:

1. an exact error-code index from `dataset/manifest.json` (e.g. `NX-781` → `KB-002`),
2. BM25-style keyword scoring,
3. cosine similarity over `openai/text-embedding-3-small` vectors when available,
4. metadata + precedence filters (status, supersedes, department, allowed citations).

An armed fault on the directory or status tool produces an honest “could not check” reply and never
a guessed department or a write. A `503`/timeout on `create_ticket` returns a failure with
`result: null`, no ticket ID, and leaves the draft available for a retry.

## Memory and approval

- **Sessions** (`app/memory/sessions.py`) persist turns, last intake/decision, citations and the
  pending action ID, so a restart does not lose a draft. Sessions expire after 180 minutes.
- **Long-term memory** (`app/memory/long_term.py`) stores employee profile snapshots, “remember
  that…” preferences, last issue/draft, and ticket facts. Recall is always labelled *“my notes, not
  company policy”*. A second session for the same employee can recall a saved preference.
- **Actions** (`app/memory/actions.py`) use an atomic `proposed → executing` claim in SQLite.
  That is what makes double clicks, repeated approvals and retried HTTP requests safe.
- **Writes** are impossible from `/api/chat`: chat messages or a typed “yes” only point the user to
  `/api/approve`. The LLM has no tool bindings at all; the orchestrator calls MCP, and the write
  functions are reachable only inside the approval code path.

## Guardrails

| Threat | Defence |
|---|---|
| Prompt injection / jailbreak | Direct override attempts are refused before any model or tool call. Quoted suspicious text is treated as data, never as instructions. |
| Poisoned wiki page (`KB-018`) | Draft documents are excluded at ingestion and citation time. The embedded secret and `posture_check=off` are scrubbed on input/output and never appear in the index. |
| Secret disclosure | `SP-001`: admin credentials, password read-outs, MFA device changes by the desk and SMS codes are refused with the correct policy citation. |
| HR/privacy | Salary, medical, personal files and another employee's records are refused (`KB-016`). |
| Hallucinated ticket IDs | An `IT-####` string is only shown if it came from a real write result or a real helpdesk read. |
| Wrong/outdated citations | Output guard drops deprecated/draft IDs and anything outside the manifest allow-list. |
| Duplicate writes | Per-action idempotency key + atomic claim + “already created” reply. |
| Looping proposals | The session caps pending drafts and asks the user to approve or reject one first. |

## Evaluation

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe evals/run_evals.py
.\.venv\Scripts\python.exe evals/run_evals.py --cases evals/cases.extra.jsonl --verbose
```

Final local results (one run, no judging):

| Suite | Result |
|---|---|
| Provided sample cases | **10/10 (100%)** - avg turn 2.84 s, p95 4.42 s |
| Additional cases (`evals/cases.extra.jsonl`) | **20/20 (100%)** - avg turn 2.84 s, p95 4.35 s |
| Unit tests | **27 passed** |

The extra cases cover precedence, department exceptions, VIP/4-user priority, PDF-only KB pages,
contractor refusals, HR privacy, prompt injection, admin credentials, onboarding priority,
long-term memory recall, double approval, an unknown error code, Engineering Elevate and the
contractor software rule.

**Fixed failure worth calling out.** The first extra run was 13/18. The failure was not a broken
retrieval score; it was missing evidence in the approval response and a draft that did not name the
routing team. I fixed the approval path to carry the draft's `kb_refs` into `citations`, added the
assigned team to the draft card, and kept the security-policy references on security drafts. That
took the suite to 18/18 at that point; two later department cases (Engineering Elevate and
contractor software) bring the current total to **20/20**. During the same hardening pass I also
fixed a real distinction between “employee not found” (404) and “directory unavailable” (5xx),
which previously produced the wrong message for unknown users.

## Repository map

```
app/
  main.py                 FastAPI contract + background warm-up
  graph/
    agent.py              compiled chat + approval LangGraph state graphs
    state.py              typed graph state
    chat.py               read-only conversation/intake/policy/retrieval nodes
    approval.py           atomic claim + write nodes (the only write path)
    common.py             response/guard/citation helpers shared by nodes
    errors.py             request-level agent errors
  models.py               Chat/Approve/TicketDraft/Employee models
  config.py, llm.py       OpenRouter client, graceful no-key fallback
  agents/                 intake, resolver, escalator (three roles)
  domain/                 manifest, playbooks, deterministic policy engine
  guardrails/             input, untrusted-source scrubbing, output guard
  rag/                     ingest, embedder, hybrid store, retriever
  memory/                  SQLite sessions, long-term facts, atomic actions
  mcp/client.py            MCP stdio client
dataset/, services/        provided kit (unchanged)
evals/                     shared runner, 10 sample + 20 extra cases
tests/                     unit tests (policy, guardrails, knowledge base, graph)
web/                       Next.js client (chat, requester, review and approval panels)
ui/app_ui.py               optional legacy Gradio client
run.py, run.ps1, run.sh    one-command local start
Dockerfile                 app image; docker-compose.yml starts helpdesk + app + UI
DESIGN.md                  architecture, trade-offs and failure analysis
docs/KIT.md                original kit documentation
```

## Design notes

- **Deterministic policy, model for language.** The highest-risk requirement is precedence, so it is
  testable code, not a prompt. The model may extract nuance and polish prose, but it cannot change a
  category, priority, route, refusal or citation. The app still works with no API key.
- **LangGraph orchestrates the agent.** `app/graph/chat.py` and `app/graph/approval.py` are separate compiled `StateGraph`s. Routing is expressed as conditional edges (guard → identity → intake → policy → incident/clarification → retrieval → draft/resolve), not as a procedural dispatcher. The approval graph is the only graph with write nodes, so chat cannot reach `create_ticket` or `add_ticket_note`.
- **MCP is the only path to the system of record.** The app does not call the helpdesk REST API
  directly, and the model never receives a write tool.
- **Honest failure over confident nonsense.** Tool failures, unknown codes, not-found employees and
  write errors all get distinct replies with `result: null` and no invented ticket ID.

More detail, including the state table and the threat model, is in [DESIGN.md](DESIGN.md).

## Known limitations

- This is a single-process demo: SQLite and in-memory session caches assume one app worker. A
  production deployment would move sessions/actions to Postgres or Redis.
- There is no end-user authentication in front of the demo UI; the work email is supplied by the
  client, as the course contract specifies. A real deployment must derive identity from SSO.
- The deterministic playbooks intentionally cover every error code in the manifest. A brand-new code
  is escalated honestly rather than answered speculatively, until the KB is updated.
- The web UI is a demo client, not a production helpdesk console; it does not provide end-user authentication.

## License / dataset

The Nakhla Systems dataset and simulated services are course material for the Agentic AI Bootcamp
(Jerusalem High-Tech Foundry / COMCEC). This repository contains the student implementation around
that kit. No real company data or credentials are included; `.env` is git-ignored.
