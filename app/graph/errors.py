"""Errors raised by the agent graphs and translated by the API layer."""
from __future__ import annotations


class AgentError(Exception):
    """A request-level error that the API should return as a JSON envelope."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
