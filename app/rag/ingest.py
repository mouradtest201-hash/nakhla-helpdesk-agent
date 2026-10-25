"""Turn the 18 Markdown articles and 2 PDFs into retrieval chunks.

Both PDFs matter: KB-012 is onboarding/offboarding and KB-013 is the only source
for PRN-07/21/33.  Ignoring PDFs would lose whole categories, so the ingester
parses them with PyMuPDF and keeps the document-control front matter out of the
chunks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.manifest import Document, document_text, load_manifest


@dataclass
class Chunk:
    doc_id: str
    title: str
    section: str
    text: str
    position: int

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "section": self.section,
            "text": self.text,
            "position": self.position,
        }

    @property
    def embedding_text(self) -> str:
        return f"{self.doc_id} {self.title} / {self.section}\n{self.text}"


HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.M)
PDF_CONTROL_RE = re.compile(r"^DOCUMENT CONTROL\s*\n---\s*\n.*?\n---\s*\n", re.S | re.I)


def _clean_pdf(text: str) -> str:
    """Drop the PDF cover/front-matter block and page footers."""
    text = PDF_CONTROL_RE.sub("", text, count=1)
    text = re.sub(r"Nakhla Systems \(fictional\).{0,120}?page \d+ of \d+", " ", text, flags=re.I)
    text = re.sub(r"\n\s*\d+\s*\n", "\n", text)
    return text.strip()


def _sections_markdown(doc: Document, text: str) -> list[tuple[str, str]]:
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [("Overview", text)]
    sections: list[tuple[str, str]] = []
    # Text before the first heading still belongs to the document title.
    preface = text[: matches[0].start()].strip()
    if preface:
        sections.append(("Overview", preface))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = match.group(2).strip()
        body = text[start:end].strip()
        if body:
            sections.append((heading, body))
    return sections


def _sections_pdf(doc: Document, text: str) -> list[tuple[str, str]]:
    """PDFs have numbered headings rather than Markdown ones.

    We keep short paragraphs together and split on blank lines, which preserves
    the tables well enough for exact term matching.
    """
    text = _clean_pdf(text)
    lines = [line.rstrip() for line in text.splitlines()]
    sections: list[tuple[str, str]] = []
    current_title = "Overview"
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append((current_title, body))

    heading_re = re.compile(r"^(\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{2,80}|[A-Z][A-Za-z /:-]{3,60})$")
    for line in lines:
        stripped = line.strip()
        if not stripped:
            buffer.append("")
            continue
        if heading_re.match(stripped) and not stripped.lower().startswith(("printer ", "code ")):
            flush()
            buffer.clear()
            current_title = stripped
            continue
        buffer.append(line)
    flush()
    if not sections:
        sections = [("Overview", text)]
    return sections


def _split_long(text: str, limit: int = 850, overlap: int = 120) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    paragraphs = re.split(r"\n\s*\n", text)
    out: list[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > limit:
            # Hard split very long tables/paragraphs.
            while paragraph:
                take = paragraph[: limit - len(current)]
                current = f"{current}\n{take}".strip()
                if len(current) >= limit:
                    out.append(current)
                    current = current[-overlap:] if overlap else ""
                paragraph = paragraph[len(take):]
            continue
        if len(current) + len(paragraph) + 2 <= limit:
            current = f"{current}\n\n{paragraph}".strip()
        else:
            if current:
                out.append(current)
            current = paragraph
    if current:
        out.append(current)
    # Add a small tail overlap so a fix split across chunks is still visible.
    if overlap and len(out) > 1:
        out = [out[0]] + [out[i - 1][-overlap:] + "\n" + out[i] for i in range(1, len(out))]
    return out


def build_chunks() -> list[Chunk]:
    manifest = load_manifest()
    chunks: list[Chunk] = []
    for doc in manifest.documents.values():
        if doc.id == "KB-018":
            # Never index the poisoned draft.  It cannot be a source and must
            # not be retrievable by the resolver either.
            continue
        if not doc.is_source:
            continue
        text = document_text(doc)
        if not text:
            continue
        sections = _sections_markdown(doc, text) if doc.format != "pdf" else _sections_pdf(doc, text)
        position = 0
        for heading, body in sections:
            for part in _split_long(body):
                chunks.append(
                    Chunk(
                        doc_id=doc.id,
                        title=doc.title,
                        section=heading[:120],
                        text=part.strip(),
                        position=position,
                    )
                )
                position += 1
    return chunks
