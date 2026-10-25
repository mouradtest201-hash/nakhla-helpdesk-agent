"""Embeddings through OpenRouter, with an honest keyword-only fallback."""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

import numpy as np
from openai import AsyncOpenAI

from app.config import (
    EMBEDDING_MODEL,
    EMBEDDING_MODE,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)

log = logging.getLogger(__name__)


class Embedder:
    def __init__(self) -> None:
        self.enabled = EMBEDDING_MODE != "off" and bool(OPENROUTER_API_KEY)
        self.model = EMBEDDING_MODEL
        self._client: AsyncOpenAI | None = None
        self._cache: dict[str, np.ndarray] = {}

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL,
                timeout=45,
                max_retries=1,
            )
        return self._client

    async def embed_many(self, texts: list[str]) -> np.ndarray | None:
        if not self.enabled or not texts:
            return None
        missing = [t for t in texts if t not in self._cache]
        # Unique, bounded batches.
        unique: list[str] = []
        seen: set[str] = set()
        for text in missing:
            if text not in seen:
                seen.add(text)
                unique.append(text)
        try:
            for start in range(0, len(unique), 48):
                batch = unique[start:start + 48]
                response = await self.client.embeddings.create(model=self.model, input=batch)
                for text, item in zip(batch, response.data):
                    self._cache[text] = np.asarray(item.embedding, dtype=np.float32)
        except Exception as exc:  # noqa: BLE001
            log.warning("Embeddings unavailable (%s); using keyword + code index only.", exc)
            self.enabled = False
            return None
        if not self._cache:
            return None
        return np.vstack([self._cache[t] for t in texts])

    async def embed_one(self, text: str) -> np.ndarray | None:
        vectors = await self.embed_many([text])
        return vectors[0] if vectors is not None else None


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()
