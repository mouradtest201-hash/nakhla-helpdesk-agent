"""The dataset manifest is the source of truth for precedence.

Everything in this module is deliberately deterministic.  The LLM never gets to
decide whether a document is current, whether it applies to a department, or
which article wins a conflict: those are properties of the client's dataset and
are enforced here, in code.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from app.config import KB_DIR, MANIFEST_PATH


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    file: str
    format: str
    version: str
    last_updated: str
    status: str
    authoritative: bool
    owner: str
    supersedes: str | None
    applies_to: tuple[str, ...]
    tags: tuple[str, ...]
    error_codes: tuple[str, ...]
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def is_policy(self) -> bool:
        return self.id.startswith("SP-")

    @property
    def is_source(self) -> bool:
        """Only current documents may be used for instructions or citations."""
        return self.status == "current"

    @property
    def path(self) -> Path:
        return KB_DIR / Path(self.file).name

    def applies_to_department(self, department: str | None) -> bool:
        if not department:
            return True
        return "all" in self.applies_to or department in self.applies_to


@dataclass(frozen=True)
class Manifest:
    raw: dict[str, Any]
    documents: dict[str, Document]
    error_index: dict[str, tuple[str, ...]]
    deprecations: dict[str, str]
    department_exceptions: dict[str, dict[str, Any]]
    routing: dict[str, str]

    # ── selection helpers ────────────────────────────────────────────────
    def doc(self, doc_id: str) -> Document | None:
        return self.documents.get(doc_id)

    def sources_for_code(self, code: str | None) -> list[Document]:
        if not code:
            return []
        ids = self.error_index.get(code.upper(), ())
        docs = [self.documents[i] for i in ids if i in self.documents]
        return self.precedence(docs)

    def superseded_by(self, doc_id: str) -> str | None:
        return self.deprecations.get(doc_id)

    def precedence_key(self, doc: Document) -> tuple:
        """Sort key: later/higher wins (rule 1, then 2, then 3)."""
        version = tuple(int(x) for x in re.findall(r"\d+", doc.version)[:3])
        return (
            1 if doc.is_policy else 0,          # SP- over KB-
            1 if doc.is_source else 0,          # current beats everything else
            1 if doc.authoritative else 0,
            doc.last_updated,
            version,
        )

    def precedence(self, docs: Iterable[Document]) -> list[Document]:
        """Current sources only, best first, superseded ids removed.

        Deprecated and draft documents are never returned as sources.  When a
        current document names an older id in ``supersedes`` the old id is
        dropped even if it is also marked current by mistake.
        """
        current = [d for d in docs if d.is_source]
        superseded = {d.supersedes for d in current if d.supersedes}
        current = [d for d in current if d.id not in superseded]
        return sorted(current, key=self.precedence_key, reverse=True)

    def department_rule(self, department: str | None) -> dict[str, Any] | None:
        if not department:
            return None
        return self.department_exceptions.get(department)

    def route_for(self, category: str) -> str:
        return self.routing.get(category, "IT Service Desk")

    def citation(self, doc_id: str) -> dict[str, Any] | None:
        doc = self.doc(doc_id)
        if doc is None or not doc.is_source:
            return None
        return {"id": doc.id, "title": doc.title, "url": None}


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip()
    return text


@lru_cache(maxsize=1)
def load_manifest() -> Manifest:
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    docs: dict[str, Document] = {}
    for entry in raw.get("documents", []):
        docs[entry["id"]] = Document(
            id=entry["id"],
            title=entry.get("title", entry["id"]),
            file=entry.get("file", ""),
            format=entry.get("format", "md"),
            version=str(entry.get("version", "0")),
            last_updated=entry.get("last_updated", ""),
            status=entry.get("status", "draft"),
            authoritative=bool(entry.get("authoritative", False)),
            owner=entry.get("owner", ""),
            supersedes=entry.get("supersedes"),
            applies_to=tuple(entry.get("applies_to") or ("all",)),
            tags=tuple(entry.get("tags") or ()),
            error_codes=tuple(entry.get("error_codes") or ()),
            extra=entry,
        )
    deprecations = {d["id"]: d["superseded_by"] for d in raw.get("deprecations", [])}
    exceptions = {e["department"]: e for e in raw.get("department_exceptions", [])}
    return Manifest(
        raw=raw,
        documents=docs,
        error_index={k.upper(): tuple(v) for k, v in raw.get("error_code_index", {}).items()},
        deprecations=deprecations,
        department_exceptions=exceptions,
        routing=raw.get("routing", {}),
    )


def document_text(doc: Document) -> str:
    """Full text of a document, with front matter removed.

    PDF text is extracted with PyMuPDF when it is installed.  The index builder
    caches the result, so runtime retrieval normally reads the cache instead.
    """
    path = doc.path
    if not path.is_file():
        return ""
    if doc.format == "pdf" or path.suffix.lower() == ".pdf":
        try:
            import fitz  # type: ignore

            with fitz.open(path) as pdf:
                text = "\n\n".join(page.get_text("text") for page in pdf)
            return _strip_front_matter(text)
        except Exception:
            return ""
    return _strip_front_matter(path.read_text(encoding="utf-8", errors="replace"))


def allowed_citation_ids() -> set[str]:
    return {d.id for d in load_manifest().documents.values() if d.is_source}


def known_error_codes() -> set[str]:
    return set(load_manifest().error_index.keys())
