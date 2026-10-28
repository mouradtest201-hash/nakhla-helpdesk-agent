"""FastAPI entry point for the Nakhla IT Helpdesk Agent.

The public surface is the course contract and nothing else:

    GET  /health        -> {"status": "ok"}
    POST /api/chat      -> ChatResponse
    POST /api/approve   -> ChatResponse

Everything else (retrieval, policy, MCP, memory, approval gate) lives behind
these three endpoints.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import APP_NAME, APP_VERSION
from app.mcp.client import mcp_client
from app.memory.db import init_db
from app.models import ApproveRequest, ChatRequest, ChatResponse
from app.rag.store import ensure_index, index_status
from app.graph import AgentError, helpdesk_agent

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nakhla.app")

# Simple in-memory rate limiter
_rate_limit_store = defaultdict(list)
_RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
_RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds


async def rate_limit_middleware(request: Request, call_next):
    """Basic rate limiting for API endpoints."""
    if request.url.path.startswith("/api/"):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        # Clean old entries
        _rate_limit_store[client_ip] = [
            req_time for req_time in _rate_limit_store[client_ip]
            if now - req_time < _RATE_LIMIT_WINDOW
        ]
        if len(_rate_limit_store[client_ip]) >= _RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
            )
        _rate_limit_store[client_ip].append(now)
    response = await call_next(request)
    # Add security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


async def _warmup() -> None:
    """Build retrieval and connect MCP without blocking /health.

    The first chat request awaits the same locks/connections, so it is safe if
    a user arrives before warm-up finishes.  Doing it in the background keeps
    the app's health check fast, which matters for the eval runner.
    """
    try:
        index = await ensure_index()
        log.info("Retrieval ready: %s chunks, vectors=%s", len(index.chunks), index.has_vectors)
    except Exception as exc:  # noqa: BLE001
        log.warning("Retrieval warm-up failed: %s", exc)
    try:
        await mcp_client.start()
        if mcp_client.ready:
            log.info("MCP helpdesk tools ready")
        else:
            log.warning("MCP helpdesk server unavailable: %s", mcp_client.last_error)
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP warm-up failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    warm = asyncio.create_task(_warmup(), name="warmup")
    app.state.warmup = warm
    log.info("Nakhla IT Helpdesk Agent %s starting", APP_VERSION)
    try:
        yield
    finally:
        if not warm.done():
            warm.cancel()
        await mcp_client.stop()
        log.info("Nakhla IT Helpdesk Agent stopped")


app = FastAPI(
    title="Nakhla IT Helpdesk Agent",
    description=(
        "Grounded IT support agent for the fictional Nakhla Systems dataset. "
        "Reads through MCP; writes only after explicit approval."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)

# Security: Restrict CORS origins (use environment variable for flexibility)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:7860,http://127.0.0.1:7860").split(",")
ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.middleware("http")(rate_limit_middleware)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": APP_NAME, "version": APP_VERSION}


@app.get("/api/status")
async def status() -> dict:
    """Non-contract diagnostics for the demo (no chat data, no secrets)."""
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "index": index_status(),
        "mcp_ready": mcp_client.ready,
        "mcp_error": mcp_client.last_error,
        "tools": await mcp_client.list_tools() if mcp_client.ready else [],
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse | JSONResponse:
    try:
        return await helpdesk_agent.chat(
            session_id=request.session_id,
            message=request.message,
            user=request.user,
        )
    except AgentError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=ChatResponse(
                session_id=request.session_id,
                reply=exc.message,
                state="error",
            ).model_dump(),
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("chat failed")
        return JSONResponse(
            status_code=500,
            content=ChatResponse(
                session_id=request.session_id,
                reply=(
                    "I hit an internal error before anything was written. "
                    "No ticket was created. Please try again."
                ),
                state="error",
            ).model_dump(),
        )


@app.post("/api/approve", response_model=ChatResponse)
async def approve(request: ApproveRequest) -> ChatResponse | JSONResponse:
    try:
        return await helpdesk_agent.approve(
            session_id=request.session_id,
            action_id=request.action_id,
            approved=request.approved,
        )
    except AgentError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=ChatResponse(
                session_id=request.session_id,
                reply=exc.message,
                state="error",
            ).model_dump(),
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("approval failed")
        return JSONResponse(
            status_code=500,
            content=ChatResponse(
                session_id=request.session_id,
                reply=(
                    "I could not confirm the approval, so I am treating it as not written. "
                    "Check the ticket list before retrying."
                ),
                state="error",
            ).model_dump(),
        )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "internal error", "path": request.url.path},
    )
