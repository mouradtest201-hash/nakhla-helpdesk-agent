"""Input checks that run before any model or tool sees the message."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import MAX_MESSAGE_CHARS
from app.guardrails.untrusted import has_injection, has_secret


@dataclass
class InputVerdict:
    ok: bool
    text: str
    flags: list[str] = field(default_factory=list)
    response: str | None = None


SECRET_REQUEST_RE = re.compile(
    r"\b(admin|administrator|local[- ]admin|service[- ]account|maintenance|root)\b.{0,35}"
    r"\b(password|credential|secret|token)\b"
    r"|\b(password|credential|secret)\b.{0,35}\b(admin|administrator|vault)\b",
    re.I,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
DIRECT_JAILBREAK_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b.{0,35}\b(?:previous|prior|above|system|developer|all)\b"
    r".{0,25}\b(?:instructions?|prompts?|rules?|guardrails?)\b"
    r"|\b(?:reveal|print|show|repeat|dump)\b.{0,30}\b(?:system|developer)\s+(?:prompt|message|instructions?)\b"
    r"|\b(?:system|developer)\s+prompt\b"
    r"|\b(?:act|behave)\s+as\b.{0,30}\b(?:admin|administrator|developer|root|unrestricted)\b"
    r"|\bpretend\b.{0,30}\b(?:you are|to be)\b.{0,30}\b(?:admin|approv\w*|system|unrestricted)\b"
    r"|\b(?:jailbreak|developer mode|dan mode)\b",
    re.I,
)
JAILBREAK_REFUSAL = (
    "I can't follow instructions that try to override the helpdesk rules, reveal internal prompts, or bypass "
    "the approval gate. Nothing was changed.\n\n"
    "I can still help with a real IT problem: tell me what is failing, the exact error code, and your work email."
)


def inspect(message: str) -> InputVerdict:
    text = CONTROL_RE.sub("", message or "").strip()
    flags: list[str] = []
    if not text:
        return InputVerdict(False, "", ["empty"], "Please describe the IT problem you need help with.")
    if len(text) > MAX_MESSAGE_CHARS:
        flags.append("too_long")
        text = text[:MAX_MESSAGE_CHARS]
    if DIRECT_JAILBREAK_RE.search(text):
        # A clear attempt to make the assistant change its rules.  Quoting a
        # suspicious page without an override command is not blocked.
        flags.append("jailbreak")
        return InputVerdict(False, text, flags, JAILBREAK_REFUSAL)
    if has_injection(text):
        # We do not block: the employee may be quoting a suspicious page.  We
        # simply never treat the text as an instruction.
        flags.append("prompt_injection")
    if SECRET_REQUEST_RE.search(text):
        flags.append("credential_request")
    if has_secret(text):
        flags.append("secret_in_input")
    return InputVerdict(True, text, flags)


def looks_like_approval(message: str) -> bool:
    low = re.sub(r"[^a-z ]", "", (message or "").lower()).strip()
    return low in {
        "yes", "yes please", "approve", "approved", "ok", "okay", "go ahead",
        "create it", "open it", "submit it", "do it", "confirm", "confirmed",
        "yes create the ticket", "approve the ticket",
    }


def looks_like_rejection(message: str) -> bool:
    low = re.sub(r"[^a-z ]", "", (message or "").lower()).strip()
    return low in {
        "no", "no thanks", "reject", "rejected", "cancel", "do not", "dont",
        "don't create it", "don't open it", "not now", "stop",
    }
