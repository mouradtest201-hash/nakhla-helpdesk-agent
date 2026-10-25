"""Final checks on every reply before it leaves the app.

The output guard is intentionally blunt: secrets never leave, citations must be
current KB/SP documents, and the agent must not claim that a ticket exists
unless the write actually returned a ticket id.
"""
from __future__ import annotations

import re
from typing import Iterable

from app.domain.manifest import allowed_citation_ids, load_manifest
from app.guardrails.untrusted import scrub_secrets

# Strings that must never appear in an answer, even if a source contains them.
FORBIDDEN_FRAGMENTS = (
    "Nakhla-LocalAdmin-7731",
    "LocalAdmin-7731",
    "posture_check=off",
    "Nakhla-LocalAdmin",
)
GENERIC_SECRET_RE = re.compile(r"\b(?:password|passwd|pwd|secret|token)\s*[:=]\s*\S+", re.I)
FAKE_TICKET_RE = re.compile(r"\bIT-\d{4,}\b")


def redact(text: str) -> str:
    text = scrub_secrets(text)
    for fragment in FORBIDDEN_FRAGMENTS:
        text = text.replace(fragment, "[redacted]")
    # Do not leak a credential-looking assignment in prose.
    text = GENERIC_SECRET_RE.sub("[redacted]", text)
    return text


def guard_reply(
    reply: str,
    *,
    citations: Iterable[dict] | None = None,
    result: dict | None = None,
    known_ticket_ids: Iterable[str] | None = None,
) -> tuple[str, list[dict], list[str]]:
    warnings: list[str] = []
    clean = redact(reply or "")

    # A ticket id is only allowed when it came from the system of record: a
    # successful write (``result``) or an id we actually read back from the
    # helpdesk (``known_ticket_ids``).  Anything else is a hallucination.
    claimed = set(FAKE_TICKET_RE.findall(clean))
    real = set()
    if isinstance(result, dict):
        for value in result.values():
            if isinstance(value, str):
                real.update(FAKE_TICKET_RE.findall(value))
    for value in known_ticket_ids or []:
        real.update(FAKE_TICKET_RE.findall(str(value)))
    if claimed - real:
        for ticket in claimed - real:
            clean = clean.replace(ticket, "[ticket id pending]")
        warnings.append("removed_unbacked_ticket_id")

    manifest = load_manifest()
    allowed = allowed_citation_ids()
    kept: list[dict] = []
    for citation in citations or []:
        cid = str((citation or {}).get("id", "")).upper()
        doc = manifest.doc(cid)
        if not cid or cid not in allowed or doc is None or not doc.is_source:
            warnings.append(f"dropped_citation:{cid or '?'}")
            continue
        if cid not in {c["id"] for c in kept}:
            kept.append({"id": doc.id, "title": doc.title, "url": None})
    return clean, kept, warnings
