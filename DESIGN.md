# Design: Nakhla IT Helpdesk Agent

This document explains why the system is built the way it is, what invariants it protects, and where
it would need to change for production.

## 1. Problem shape

The Scenario 2 brief is not mainly a chatbot problem. It is an **authorisation and precedence**
problem:

1. Answer only from current, authoritative Nakhla policy.
2. Resolve conflicts between security policies, current/deprecated KBs, department exceptions and
   ticket history in a predictable way.
3. Use the real systems (directory, status page, ticket archive) through MCP.
4. Draft a ticket, but write only after an explicit human approval.
5. Remember enough to be useful across sessions without inventing company policy.
6. Resist poisoning, prompt injection and secret exfiltration.
7. Prove all of that with repeatable state-based evals.

A model that “usually” follows the rules is not enough. The design therefore puts the irreversible
and the rule-bound parts in deterministic code, and uses the model only where language flexibility
helps.

## 2. Component map

| Layer | Modules | Responsibility |
|---|---|---|
| Contract | `app/main.py`, `app/models.py` | `GET /health`, `POST /api/chat`, `POST /api/approve`; pydantic validation; error envelopes. |
| Orchestration | `app/workflow.py` | Explicit state machine, MCP calls, memory, approval gate, response assembly. |
| Roles | `app/agents/intake.py`, `resolver.py`, `escalator.py` | Structured extraction → grounded reply → schema-valid draft. |
| Domain | `app/domain/manifest.py`, `playbooks.py`, `policy.py` | Current-document manifest, verified playbooks, precedence/category/priority/refusal rules. |
| Retrieval | `app/rag/*` | Markdown/PDF ingestion, 111 chunks, exact-code + BM25 + embeddings, metadata filters. |
| Guardrails | `app/guardrails/*` | Input inspection, untrusted-text scrubbing, output redaction/citation/ticket-ID checks. |
| Memory | `app/memory/*` | SQLite sessions, employee facts, atomic proposed/executing/executed actions. |
| Systems access | `app/mcp/client.py` + `services/helpdesk_mcp.py` | MCP stdio only; no direct REST calls from the agent. |
| Clients / proof | `web/`, `evals/*`, `tests/*` | Next.js client, shared-contract evals, unit tests. |

## 3. Workflow state machine

```
chat ──► input_guard ──► identity (email / directory) ──► long-term memory intents
             │                    │
             │                    ├─ 404 employee → explain, no write
             │                    └─ 5xx directory → honest “could not check”, no write
             ▼
      retrieval ──► policy_decision ──► refusal? ──► refused
             │            │
             │            ├─ active incident → incident reply, no duplicate ticket
             │            ├─ policy refusal → refused
             │            └─ normal → resolver (grounded answer)
             │
             └─ should draft? ──► escalator → pending_action (awaiting_approval)
                                      │
approve ──► atomic claim ──► MCP write ──► completed (result) / failed (retryable)
```

Public states used in responses: `answered`, `clarification`, `awaiting_approval`, `refused`,
`completed`, `cancelled`, `failed`, `error`.

Important invariants:

- `/api/chat` is **read-only** with respect to the ticket system.
- One pending action per session is the normal case; the session refuses to pile up drafts.
- `/api/approve` is the only code path that calls `create_ticket` or `add_ticket_note`.
- `pending_action.payload` is exactly what will be sent on approval, plus the idempotency key.
- `result` is `null` unless the system of record returned a ticket ID.

## 4. Precedence and policy

`app/domain/manifest.py` loads the dataset front matter and computes which documents are valid
sources. `app/domain/policy.py` then applies, in order:

1. **Security policies first.** `SP-001` and `SP-002` can veto KB guidance. For example, the old
   phone password-reset procedure in `KB-005` is obsolete; the current rule is self-service reset
   (`SP-001` §2, `KB-004`).
2. **Current documents only.** `KB-001` is deprecated, `KB-005` is superseded, `KB-018` is a draft.
   They are filtered from retrieval and from citations. `KB-018` is not even ingested, so its hidden
   credential and `posture_check=off` instruction cannot reach a prompt.
3. **Newer/authoritative wins.** The manifest says `KB-002` v5.4 supersedes `KB-001`; `KB-015` v3
   supersedes the old “5+ users” priority rule in `KB-017`.
4. **Department exceptions.** `KB-016` is applied after the employee's real department is read from
   the directory (never guessed from job title). Finance VPN → `security` / Security Operations;
   contractors → VDI only; Executive Office → P1.
5. **Documents beat history.** Historical tickets are context and duplicate awareness only. They
   never change a rule or priority.

Refusals are explicit objects with policy references: admin credentials, password read-outs,
MFA-device changes by the desk, SMS codes, contractor VPN, others' HR/personal records.

Error-code playbooks (`app/domain/playbooks.py`) contain the verified facts for every code in the
manifest. They make replies deterministic and testable, and they are the fallback if the model is
unavailable. Retrieval still decides which current documents are cited.

## 5. Retrieval design

- **Ingestion:** YAML front matter + Markdown body; PDF text for `KB-012`/`KB-013`; section-aware
  chunking with overlap; 111 chunks. Untrusted text is scrubbed before indexing.
- **Query:** exact error code lookup first (manifest index), then BM25-style keyword scoring and
  cosine similarity over embeddings. The top documents are filtered by status/supersedes/allowed
  citation IDs and by department when relevant.
- **Why hybrid:** error codes are highly discriminative (`NX-781`, `NID-423`, `PRN-07`), while
  natural-language complaints need semantic recall. Exact-code lookup also protects against a
  poisoned page outranking a correct one.
- **Fallback:** embeddings are optional. Without an API key or with a failed embedding call, the
  exact-code and keyword path still answers; replies fall back to verified templates.

## 6. Memory and idempotency

### Short term

`SessionState` persists the conversation, last intake/decision/citations, status cache, pending
action ID and created ticket IDs. It is cached in memory and written through to SQLite, so a process
restart does not lose an unapproved draft. TTL defaults to 180 minutes.

### Long term

Employee profile snapshots, “remember that…” preferences, last issue/draft and ticket facts are
stored per email. Recall is presented as the assistant's notes, never as company policy. Ticket
history is also fetched live from the helpdesk, which remains the system of record.

### Writes

`app/memory/actions.py` performs an atomic state transition:

```
proposed ──claim──► executing ──success──► executed
    │                   │
    └──reject──► rejected└──failure──► failed (retry allowed)
```

The action ID is the API idempotency key. A second click sees `executed` and returns the same
ticket; a concurrent second request sees `executing` and is told not to resend; a failed write can be
retried safely. The helpdesk API itself also enforces the same idempotency key.

## 7. Guardrails and threat model

| Attack / failure | Control |
|---|---|
| “Ignore previous instructions…” | Direct override pattern refused in `input_guard` before tools/model. Quoted suspicious content is not blocked but is never treated as instructions. |
| Poisoned wiki page | `KB-018` excluded at ingestion; untrusted instruction/secret patterns scrubbed; output redaction for the known credential fragments. |
| Ask for admin password / temp password / SMS code | Deterministic refusal with `SP-001`/`SP-002` citation; no tool call. |
| Ask for another employee's salary or medical record | HR/privacy refusal with `KB-016`; no directory enumeration. |
| Hallucinated ticket | Output guard allows `IT-####` only from a real write result or a real read (allow-list). |
| Deprecated citation | Citation allow-list drops `KB-001`, `KB-005`, `KB-018` regardless of model output. |
| Duplicate ticket | Single pending action, atomic action claim, idempotency key, “already created” reply. |
| 503 / timeout on write | `result: null`, honest failure, no ticket ID, draft retained for retry. |
| Directory outage | Distinct “could not check, nothing written” reply; cached profile used only when safe. |
| Unknown error code | No invented steps; offer an escalation draft with `kb_refs: []`. |

The model itself is not given tool bindings. It cannot call any MCP function, let alone a write. This
is stricter than filtering a tool list: there is no model-controlled tool surface to bypass.

## 8. Evaluation strategy

The eval runner is the provided contract runner; cases assert API state, not prose style alone:
citations, `pending_action`, `result`, ticket counts, categories, priorities and assigned teams.

- 10 provided cases: grounding, clarification, precedence conflict, unknown code, happy path,
  rejection, tool failure, guardrail, active incident, Finance exception.
- 20 additional cases: repeated lockout, phone-left policy, contractor VDI/VPN, VIP and 4-user
  priority, HR privacy, prompt injection, admin credentials, deprecated phone reset, PDF-only
  printing, onboarding P2/P3, long-term memory, double approval, lost-phone security, unknown code,
  Engineering Elevate and contractor software.
- 24 unit tests: policy decisions, guardrails, manifest/index properties, escalator schema and
  priority.

The initial extra run scored 13/18. The root cause of four failures was presentation, not retrieval:
the approval response dropped the draft's citations, and the draft card did not show the routing
team. The fix was to carry `kb_refs` through the approval response and include the assigned team in
the draft. The fifth failure was a stale running process; after restart the new security references
appeared. A separate real bug found during manual checks was that an unknown employee was reported as
a directory outage; the workflow now distinguishes a 404 from a 5xx. The suite now runs **20/20**,
including the two later department cases for Engineering Elevate and contractor software.

## 9. Trade-offs

| Choice | Why | Cost |
|---|---|---|
| Deterministic policy engine | Precedence and refusal are the highest-risk requirements; they must be unit-testable and stable. | New policy wording needs a code/playbook update, not just a prompt tweak. |
| LangGraph with a deterministic pipeline | The blueprint's stages are explicit nodes (guard, triage, precedence, retriever, resolver, escalation writer, verifier) and every reply exits through the verifier; the single-write gate stays outside the graph in `approve()`. | Node state is a shared dict; the policy gate runs before retrieval so refusals never retrieve. |
| SQLite + in-process cache | Zero infrastructure, easy one-command demo, durable drafts. | Single worker only; not horizontally scalable. |
| Hybrid retrieval | Exact codes and semantics fail in different ways; hybrid covers both. | More moving parts; embedding failures need a fallback. |
| Playbooks for every manifest code | Deterministic, grounded answers and a no-LLM fallback. | Must be kept in sync when the KB changes. |
| Model without tools | Removes an entire class of unsafe actions. | The orchestrator, not the model, decides when to call MCP; less “autonomous” tool selection. |

## 10. Production hardening (next steps)

1. Move sessions, actions and long-term memory to Postgres/Redis with transactional outbox for writes.
2. Authenticate users with SSO and derive email server-side; never trust `user.email` from the body.
3. Put the agent behind an API gateway with per-user rate limits, audit logs and request signing.
4. Replace the static playbook with a reviewed content pipeline that regenerates/validates it from
   the manifest on every dataset release.
5. Add observability: structured traces per turn, token/cost budgets, alerting on tool failures and
   on write/idempotency anomalies.
6. Add human review queues and SLA timers for P1 tickets, plus a policy for incident suppression.
7. Expand adversarial evals: multi-turn injection, indirect injection in ticket text, cross-user data
   access attempts, and concurrency tests for double approval.
