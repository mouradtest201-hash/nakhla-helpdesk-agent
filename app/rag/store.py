"""On-disk vector + keyword index, rebuilt when the KB changes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import EMBEDDING_MODEL, INDEX_DIR, MANIFEST_PATH, KB_DIR, ensure_data_dirs
from app.rag.embedder import get_embedder
from app.rag.ingest import Chunk, build_chunks

log = logging.getLogger(__name__)
CHUNKS_PATH = INDEX_DIR / "chunks.json"
VECTORS_PATH = INDEX_DIR / "vectors.npz"
META_PATH = INDEX_DIR / "meta.json"

_build_lock = asyncio.Lock()
_cached: "Index | None" = None


@dataclass
class Index:
    chunks: list[Chunk]
    vectors: np.ndarray | None
    embedding_model: str
    fingerprint: str
    built_at: float

    @property
    def has_vectors(self) -> bool:
        return self.vectors is not None and len(self.vectors) == len(self.chunks)


def fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(EMBEDDING_MODEL.encode())
    digest.update(MANIFEST_PATH.read_bytes())
    for path in sorted(KB_DIR.glob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def _load() -> Index | None:
    if not (CHUNKS_PATH.is_file() and META_PATH.is_file()):
        return None
    try:
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        if meta.get("fingerprint") != fingerprint():
            return None
        raw_chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
        chunks = [Chunk(**item) for item in raw_chunks]
        vectors = None
        if VECTORS_PATH.is_file():
            data = np.load(VECTORS_PATH)
            candidate = data["vectors"].astype(np.float32)
            if candidate.shape[0] == len(chunks):
                vectors = candidate
        return Index(chunks, vectors, meta.get("embedding_model", "none"),
                     meta["fingerprint"], float(meta.get("built_at", 0)))
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not load retrieval index: %s", exc)
        return None


async def build_index(*, force: bool = False) -> Index:
    ensure_data_dirs()
    async with _build_lock:
        global _cached
        if not force and _cached is not None and _cached.fingerprint == fingerprint():
            return _cached
        current = None if force else _load()
        if current is not None:
            _cached = current
            return current

        started = time.perf_counter()
        chunks = build_chunks()
        vectors: np.ndarray | None = None
        model = "none"
        embedder = get_embedder()
        if embedder.enabled and chunks:
            texts = [c.embedding_text for c in chunks]
            vectors = await embedder.embed_many(texts)
            if vectors is not None:
                model = embedder.model
        CHUNKS_PATH.write_text(
            json.dumps([c.as_dict() for c in chunks], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        if vectors is not None:
            np.savez_compressed(VECTORS_PATH, vectors=vectors)
        elif VECTORS_PATH.is_file():
            VECTORS_PATH.unlink(missing_ok=True)
        meta = {
            "fingerprint": fingerprint(),
            "embedding_model": model,
            "built_at": time.time(),
            "chunks": len(chunks),
            "vector_dim": int(vectors.shape[1]) if vectors is not None else 0,
            "build_seconds": round(time.perf_counter() - started, 2),
        }
        META_PATH.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        _cached = Index(chunks, vectors, model, meta["fingerprint"], meta["built_at"])
        log.info("Built index: %s chunks, vectors=%s, %.2fs",
                 len(chunks), vectors is not None, meta["build_seconds"])
        return _cached


async def ensure_index() -> Index:
    return await build_index(force=False)


def loaded_index() -> Index | None:
    return _cached


def index_status() -> dict:
    index = _cached
    return {
        "built": index is not None,
        "chunks": len(index.chunks) if index else 0,
        "vectors": bool(index and index.has_vectors),
        "embedding_model": index.embedding_model if index else None,
        "built_at": index.built_at if index else None,
    }
