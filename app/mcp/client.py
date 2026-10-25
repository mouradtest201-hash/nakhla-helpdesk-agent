"""A small, long-lived MCP stdio client.

The MCP server is started as a child process exactly as the kit documents:

    python -m services.helpdesk_mcp

with the helpdesk API URL/key passed through the environment.  One session is
kept open for the app's lifetime; a reconnect is attempted if the child dies.

Writes (``create_ticket`` / ``add_ticket_note``) are deliberately callable only
from the approval service.  The workflow module never imports this client
directly for writes; it goes through ``ActionService``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import BASE_DIR, HELPDESK_API_KEY, HELPDESK_API_URL, HELPDESK_TIMEOUT_S

log = logging.getLogger(__name__)


@dataclass
class ToolOutcome:
    tool: str
    ok: bool
    ms: int
    data: dict[str, Any]

    @property
    def error(self) -> dict[str, Any] | None:
        if self.ok:
            return None
        error = self.data.get("error")
        if isinstance(error, dict):
            return error
        return {"code": "tool_failed", "message": str(self.data)[:300]}


def _server_env() -> dict[str, str]:
    env = dict(os.environ)
    env["HELPDESK_API_URL"] = HELPDESK_API_URL
    env["HELPDESK_API_KEY"] = HELPDESK_API_KEY
    env["HELPDESK_TIMEOUT_S"] = str(HELPDESK_TIMEOUT_S)
    env.setdefault("HELPDESK_MCP_LOG_LEVEL", "WARNING")
    env["PYTHONUNBUFFERED"] = "1"
    return env


class MCPClient:
    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()
        self.ready = False
        self.last_error: str | None = None

    async def start(self) -> None:
        async with self._lock:
            if self.ready:
                return
            await self._start_locked()

    async def _start_locked(self) -> None:
        try:
            stack = AsyncExitStack()
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "services.helpdesk_mcp"],
                cwd=str(BASE_DIR),
                env=_server_env(),
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=20)
            self._stack = stack
            self.session = session
            self.ready = True
            self.last_error = None
            log.info("MCP helpdesk server connected")
        except Exception as exc:  # noqa: BLE001
            self.ready = False
            self.last_error = f"{exc.__class__.__name__}: {exc}"
            log.warning("MCP server did not start: %s", self.last_error)

    async def stop(self) -> None:
        async with self._lock:
            self.ready = False
            self.session = None
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception:  # noqa: BLE001
                    pass
                self._stack = None

    async def ensure(self) -> bool:
        if self.ready and self.session is not None:
            return True
        await self.start()
        return self.ready and self.session is not None

    async def list_tools(self) -> list[str]:
        if not await self.ensure():
            return []
        try:
            result = await asyncio.wait_for(self.session.list_tools(), timeout=15)
            return [tool.name for tool in result.tools]
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return []

    async def call(self, tool: str, arguments: dict[str, Any], *, timeout: float | None = None) -> ToolOutcome:
        started = time.perf_counter()
        if not await self.ensure() or self.session is None:
            return ToolOutcome(tool, False, 0, {
                "ok": False,
                "error": {"code": "mcp_unavailable", "message": "The MCP helpdesk server is not available."},
            })
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(tool, arguments=arguments),
                timeout=timeout or max(HELPDESK_TIMEOUT_S + 5, 10),
            )
            data = self._decode(result)
            had_protocol_error = bool(getattr(result, "isError", False))
            ok = not had_protocol_error and data.get("ok", True) is not False
            return ToolOutcome(tool, ok, int((time.perf_counter() - started) * 1000), data)
        except asyncio.TimeoutError:
            return ToolOutcome(tool, False, int((time.perf_counter() - started) * 1000), {
                "ok": False,
                "error": {
                    "code": "timeout",
                    "message": "The helpdesk did not answer in time. Nothing is confirmed.",
                },
            })
        except Exception as exc:  # noqa: BLE001
            # A broken child can be restarted on the next call.
            self.ready = False
            self.last_error = f"{exc.__class__.__name__}: {exc}"
            return ToolOutcome(tool, False, int((time.perf_counter() - started) * 1000), {
                "ok": False,
                "error": {"code": "mcp_error", "message": str(exc)[:300]},
            })

    @staticmethod
    def _decode(result: Any) -> dict[str, Any]:
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and structured:
            # FastMCP may wrap a bare dict under "result".
            if set(structured.keys()) == {"result"} and isinstance(structured["result"], dict):
                return structured["result"]
            return structured
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if not text:
                continue
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
            except (TypeError, ValueError):
                return {"ok": False, "error": {"code": "bad_tool_output", "message": text[:300]}}
        return {"ok": False, "error": {"code": "empty_tool_output", "message": "The tool returned no data."}}


mcp_client = MCPClient()
