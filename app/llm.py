"""Thin OpenRouter client with usage accounting and a no-LLM fallback.

The model is an assistant, not an authority.  Every caller supplies grounded
facts; this module only returns text/JSON.  If the key is missing, the request
fails, or the model returns nonsense, callers fall back to deterministic
templates.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.config import (
    LLM_MAX_TOKENS,
    LLM_TIMEOUT_S,
    MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    cost_usd,
    has_llm,
)

log = logging.getLogger(__name__)


@dataclass
class UsageMeter:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += max(0, int(prompt or 0))
        self.completion_tokens += max(0, int(completion or 0))

    def public(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": cost_usd(self.prompt_tokens, self.completion_tokens),
        }


@dataclass
class LLMText:
    text: str
    ok: bool
    error: str | None = None


_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            timeout=LLM_TIMEOUT_S,
            max_retries=1,
            default_headers={
                "HTTP-Referer": "https://github.com/nakhla-helpdesk-agent",
                "X-Title": "Nakhla IT Helpdesk Agent",
            },
        )
    return _client


async def complete(
    system: str,
    user: str,
    *,
    meter: UsageMeter | None = None,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> LLMText:
    if not has_llm():
        return LLMText("", False, "no API key")
    try:
        kwargs: dict[str, Any] = {
            "model": MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens or LLM_MAX_TOKENS,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await client().chat.completions.create(**kwargs)
        text = (response.choices[0].message.content or "").strip()
        if meter is not None and response.usage is not None:
            meter.add(response.usage.prompt_tokens, response.usage.completion_tokens)
        return LLMText(text, bool(text), None if text else "empty completion")
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM call failed: %s", exc)
        return LLMText("", False, str(exc)[:300])


async def complete_json(
    system: str,
    user: str,
    *,
    meter: UsageMeter | None = None,
    max_tokens: int | None = 700,
) -> dict[str, Any] | None:
    result = await complete(system, user, meter=meter, json_mode=True, max_tokens=max_tokens)
    if not result.ok:
        return None
    try:
        data = json.loads(result.text)
        return data if isinstance(data, dict) else None
    except ValueError:
        return None
