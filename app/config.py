"""Configuration and paths.

The kit is copied to the repository root, so `dataset/` and `services/` sit next
to `app/`.  Everything the agent writes lives under `data/` and is git-ignored.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
APP_NAME = "nakhla-helpdesk-agent"
APP_VERSION = "1.0.0"

# Load the local .env once.  Do not override variables the process already has
# (Docker Compose sets them explicitly).
load_dotenv(BASE_DIR / ".env", override=False)

# ── The client's simulated world (never modified) ────────────────────────────
DATASET_DIR = BASE_DIR / "dataset"
KB_DIR = DATASET_DIR / "knowledge_base"
MANIFEST_PATH = DATASET_DIR / "manifest.json"
TICKET_SCHEMA_PATH = DATASET_DIR / "schemas" / "ticket.schema.json"
HISTORICAL_TICKETS_PATH = DATASET_DIR / "tickets" / "historical_tickets.json"

# ── Our generated state ──────────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("APP_DATA_DIR") or (BASE_DIR / "data"))
INDEX_DIR = DATA_DIR / "index"
MEMORY_DB = DATA_DIR / "memory.db"

# ── LLM / embeddings (OpenRouter) ────────────────────────────────────────────
OPENROUTER_API_KEY = (os.getenv("OPENROUTER_API_KEY") or "").strip()
OPENROUTER_BASE_URL = (os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
MODEL = os.getenv("MODEL") or "openai/gpt-4.1-mini"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or "openai/text-embedding-3-small"
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S") or 60)
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS") or 1200)
EMBEDDING_MODE = (os.getenv("INDEX_EMBEDDING_MODE") or "auto").lower()  # auto | off

# ── Simulated helpdesk API / MCP ─────────────────────────────────────────────
HELPDESK_API_URL = (os.getenv("HELPDESK_API_URL") or "http://localhost:8102").rstrip("/")
HELPDESK_API_KEY = os.getenv("HELPDESK_API_KEY") or "dev-key-change-me"
HELPDESK_TIMEOUT_S = float(os.getenv("HELPDESK_TIMEOUT_S") or 20)

# ── Service limits ───────────────────────────────────────────────────────────
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS") or 4000)
MAX_CONVERSATION_MESSAGES = int(os.getenv("MAX_CONVERSATION_MESSAGES") or 16)  # Optimized: reduced from 24
MAX_ACTIONS_PER_SESSION = int(os.getenv("MAX_ACTIONS_PER_SESSION") or 20)
SESSION_TTL_MINUTES = int(os.getenv("SESSION_TTL_MINUTES") or 180)
CONTEXT_WINDOW_MESSAGES = int(os.getenv("CONTEXT_WINDOW_MESSAGES") or 12)  # For LLM context

# ── Security settings ─────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:7860,http://127.0.0.1:7860")
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS") or 60)
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW") or 60)  # seconds
ENABLE_AUDIT_LOG = os.getenv("ENABLE_AUDIT_LOG", "true").lower() == "true"


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)


def has_llm() -> bool:
    return bool(OPENROUTER_API_KEY)


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """A small, honest estimate for the scorecard (gpt-4.1-mini pricing)."""
    return round((prompt_tokens / 1_000_000) * 0.40 + (completion_tokens / 1_000_000) * 1.60, 6)
