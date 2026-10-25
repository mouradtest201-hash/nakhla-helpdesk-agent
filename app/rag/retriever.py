"""Hybrid retrieval: exact error-code index + BM25 + embeddings + metadata.

Order of operations is the precedence contract:

1. An error code is looked up in the manifest's code index.  Only current
   documents are kept (KB-001 is deprecated, KB-018 is a draft).
2. Department metadata is applied as a filter/penalty.
3. Keyword and vector scores rank the chunks inside the allowed set.
4. Draft/deprecated documents are never returned, whatever their similarity.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from app.domain.manifest import Document, Manifest, load_manifest
from app.domain.playbooks import GENERIC_CODE_RE, NON_ERROR_CODE_RE, playbook_for, topic_for_code
from app.guardrails.untrusted import sanitize_untrusted
from app.rag.embedder import get_embedder
from app.rag.store import Index, ensure_index

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")
STOPWORDS = {
    "the", "and", "for", "with", "my", "is", "a", "an", "to", "of", "on", "in", "it",
    "i", "at", "not", "can", "cannot", "cant", "does", "doesnt", "when", "this", "that",
    "from", "me", "how", "what", "after", "please", "help", "need", "have", "has", "was",
    "are", "you", "your", "we", "our", "us", "but", "so", "if", "do", "did", "be", "been",
    "there", "their", "they", "he", "she", "his", "her", "them", "will", "would", "should",
    "could", "about", "into", "than", "then", "also", "just", "get", "got", "like", "one",
    "now", "still", "all", "any", "some", "more", "very", "up", "out", "no", "yes",
}
ALIASES = (
    (re.compile(r"\bnakhla[ -]?connect\b", re.I), "nakhlaconnect"),
    (re.compile(r"\bwi[ -]?fi\b", re.I), "wifi"),
    (re.compile(r"\bsign[ -]?in\b", re.I), "signin"),
    (re.compile(r"\blog[ -]?in\b", re.I), "login"),
    (re.compile(r"\btwo[ -]?factor\b", re.I), "mfa"),
    (re.compile(r"\b2fa\b", re.I), "mfa"),
)


def normalize(text: str) -> str:
    for pattern, replacement in ALIASES:
        text = pattern.sub(replacement, text)
    return text.lower()


def tokenize(text: str) -> list[str]:
    text = normalize(text)
    raw = TOKEN_RE.findall(text)
    tokens: list[str] = []
    for token in raw:
        if len(token) < 2:
            continue
        if token in STOPWORDS:
            continue
        tokens.append(token)
        # Add the parts of hyphenated tokens (nx-781 also matches nx / 781),
        # but keep the full code as well.
        if "-" in token and not token[0].isdigit():
            tokens.extend(part for part in token.split("-") if len(part) >= 2 and part not in STOPWORDS)
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            tokens.append(token[:-1])
    return tokens


@dataclass
class RetrievedDoc:
    doc_id: str
    title: str
    score: float
    excerpts: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    docs: list[RetrievedDoc] = field(default_factory=list)
    unknown_code: bool = False
    code: str | None = None
    max_keyword: float = 0.0
    query_terms: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def doc_ids(self) -> list[str]:
        return [d.doc_id for d in self.docs]

    @property
    def best(self) -> RetrievedDoc | None:
        return self.docs[0] if self.docs else None

    def excerpts(self, limit: int = 4) -> list[str]:
        out: list[str] = []
        for doc in self.docs:
            for excerpt in doc.excerpts:
                if excerpt not in out:
                    out.append(excerpt)
                if len(out) >= limit:
                    return out
        return out


@dataclass
class _Stats:
    doc_freq: Counter
    lengths: list[int]
    avg_len: float
    term_counts: list[Counter]
    chunk_tokens: list[set[str]]


_stats_cache: dict[int, _Stats] = {}
_query_vector_cache: dict[str, np.ndarray | None] = {}


def _stats(index: Index) -> _Stats:
    key = id(index)
    cached = _stats_cache.get(key)
    if cached is not None:
        return cached
    term_counts: list[Counter] = []
    doc_freq: Counter = Counter()
    lengths: list[int] = []
    chunk_tokens: list[set[str]] = []
    for chunk in index.chunks:
        tokens = tokenize(f"{chunk.title} {chunk.section} {chunk.text}")
        counts = Counter(tokens)
        term_counts.append(counts)
        doc_freq.update(counts.keys())
        lengths.append(max(1, len(tokens)))
        chunk_tokens.append(set(tokens))
    avg = sum(lengths) / max(1, len(lengths))
    stats = _Stats(doc_freq, lengths, avg, term_counts, chunk_tokens)
    _stats_cache[key] = stats
    return stats


def _bm25(query_tokens: list[str], index: Index, stats: _Stats) -> list[float]:
    n = len(index.chunks)
    k1, b = 1.5, 0.75
    scores: list[float] = []
    idf = {
        term: math.log((n - df + 0.5) / (df + 0.5) + 1)
        for term, df in stats.doc_freq.items()
    }
    for counts, length in zip(stats.term_counts, stats.lengths):
        score = 0.0
        for term in set(query_tokens):
            tf = counts.get(term, 0)
            if not tf:
                continue
            score += idf.get(term, 0.0) * (tf * (k1 + 1)) / (
                tf + k1 * (1 - b + b * length / stats.avg_len)
            )
        scores.append(score)
    return scores


def _cosine(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query / (np.linalg.norm(query) + 1e-9)
    m = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    return m @ q


def _metadata_boost(chunk, doc: Document, query_terms: set[str], code: str | None) -> float:
    text = normalize(f"{doc.title} {chunk.section} {' '.join(doc.tags)}")
    hits = sum(1 for term in query_terms if term in text)
    boost = min(0.55, 0.12 * hits)
    if code and code.lower() in text:
        boost += 0.25
    if code and code in doc.error_codes:
        boost += 0.25
    return boost


async def _query_vector(query: str) -> np.ndarray | None:
    if query in _query_vector_cache:
        return _query_vector_cache[query]
    embedder = get_embedder()
    vector = await embedder.embed_one(query) if embedder.enabled else None
    _query_vector_cache[query] = vector
    return vector


def _mode(code: str | None, hits: list[float]) -> str:
    if code:
        return "code"
    return "hybrid" if hits else "keyword"


async def retrieve(
    query: str,
    *,
    code: str | None = None,
    department: str | None = None,
    force_docs: Iterable[str] = (),
    manifest: Manifest | None = None,
    top_docs: int = 3,
    top_chunks: int = 2,
) -> RetrievalResult:
    manifest = manifest or load_manifest()
    index = await ensure_index()
    result = RetrievalResult(code=code)

    # ── 1. Exact error-code index ────────────────────────────────────────
    code = (code or "").upper() or None
    allowed_docs: set[str] | None = None
    if code:
        if code in manifest.error_index:
            allowed_docs = {d.id for d in manifest.sources_for_code(code)}
            if not allowed_docs:
                result.note = "error code exists but every document for it is deprecated or draft"
        elif GENERIC_CODE_RE.fullmatch(code) and not NON_ERROR_CODE_RE.match(code):
            result.unknown_code = True
            result.note = "error code is not covered by the knowledge base"
            return result

    # ── 2. Query tokens and BM25 ─────────────────────────────────────────
    query_terms = tokenize(query)
    if code:
        query_terms = [code.lower()] + [t for t in query_terms if t != code.lower()]
    result.query_terms = query_terms[:24]
    stats = _stats(index)
    keyword_scores = _bm25(query_terms, index, stats)

    # ── 3. Embeddings (optional, never required) ─────────────────────────
    cosines: np.ndarray | None = None
    query_vec = await _query_vector(query)
    if query_vec is not None and index.has_vectors:
        try:
            cosines = _cosine(query_vec, index.vectors)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            cosines = None

    raw_keyword = max(keyword_scores) if keyword_scores else 0.0
    result.max_keyword = raw_keyword
    if allowed_docs is None and raw_keyword <= 0.9 and cosines is None:
        result.note = "no meaningful lexical match"
        return result

    # Normalize keyword by the best score, cosine is already comparable.
    max_kw = max(keyword_scores) or 1.0
    query_set = set(query_terms)

    candidates: list[tuple[float, int, Document, str]] = []
    for position, chunk in enumerate(index.chunks):
        doc = manifest.doc(chunk.doc_id)
        if doc is None or not doc.is_source:
            continue
        if allowed_docs is not None and doc.id not in allowed_docs:
            continue
        if code and code in doc.error_codes:
            code_hit = 1.0
        else:
            code_hit = 1.0 if code and code.lower() in chunk.text.lower() else 0.0
        kw = keyword_scores[position] / max_kw if max_kw else 0.0
        vec = float(cosines[position]) if cosines is not None else 0.0
        vec = max(0.0, vec)
        meta = _metadata_boost(chunk, doc, query_set, code)
        score = 0.48 * kw + 0.32 * vec + 0.20 * meta
        if code_hit:
            score += 0.45
        if allowed_docs is not None:
            score += 0.10
        if doc.applies_to_department(department):
            score += 0.03
        elif department:
            score -= 0.08
        candidates.append((score, position, doc, chunk.text))

    if not candidates:
        result.note = result.note or "no current document matched the query"
        return result

    # Force-include documents that the policy decision names (for example
    # KB-016 for a Finance VPN exception, or SP-002 for a refusal).
    forced = {d.upper() for d in force_docs}
    for doc_id in forced:
        doc = manifest.doc(doc_id)
        if doc is None or not doc.is_source:
            continue
        if any(d.id == doc.id for _, _, d, _ in candidates):
            continue
        for position, chunk in enumerate(index.chunks):
            if chunk.doc_id == doc.id:
                candidates.append((0.55, position, doc, chunk.text))
                break

    # ── 4. Group by document and keep the best chunks ────────────────────
    by_doc: dict[str, list[tuple[float, int, str]]] = {}
    doc_ref: dict[str, Document] = {}
    for score, position, doc, text in candidates:
        by_doc.setdefault(doc.id, []).append((score, position, text))
        doc_ref[doc.id] = doc

    ranked: list[RetrievedDoc] = []
    for doc_id, items in by_doc.items():
        items.sort(key=lambda item: item[0], reverse=True)
        top = items[0][0]
        second = items[1][0] if len(items) > 1 else 0.0
        doc_score = top + 0.08 * second
        # Precedence breaks near-ties: SP- > current > authoritative > newer.
        doc = doc_ref[doc_id]
        if doc.is_policy:
            doc_score += 0.02
        excerpts = []
        sections = []
        for _, _, text in items[:top_chunks]:
            clean = sanitize_untrusted(text, max_chars=1400)
            if clean:
                excerpts.append(clean)
                sections.append(doc.title)
        ranked.append(RetrievedDoc(doc_id, doc.title, round(doc_score, 4), excerpts, sections))

    ranked.sort(key=lambda d: (d.score, manifest.precedence_key(doc_ref[d.doc_id])), reverse=True)
    result.docs = ranked[:top_docs]
    if not result.docs:
        result.note = result.note or "no current document matched the query"
    return result


async def retrieve_chunks(
    query: str,
    doc_id: str,
    *,
    limit: int = 3,
) -> list[str]:
    """Best chunks from one specific document (used to ground a known code)."""
    index = await ensure_index()
    query_terms = set(tokenize(query))
    scored: list[tuple[float, str]] = []
    for chunk in index.chunks:
        if chunk.doc_id != doc_id:
            continue
        tokens = set(tokenize(chunk.text))
        overlap = len(tokens & query_terms)
        scored.append((overlap + len(chunk.text) / 100000.0, sanitize_untrusted(chunk.text, max_chars=1200)))
    scored.sort(reverse=True)
    return [text for _, text in scored[:limit] if text]
