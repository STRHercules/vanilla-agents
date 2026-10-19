"""Redact secrets and personal strings from captured output before it is stored as evidence (same ideas as the repo guard)."""
import re

PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b\s*[:=]\s*(?:(?:bearer|basic)\s+)?\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\b[0-9a-fA-F]{40,}\b"), "[hex]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com|example\.com|localhost)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    (re.compile(r"(?i)([A-Z]:\\Users\\)[^\\\s]+"), r"\1[user]"),
    (re.compile(r"(?i)(/Users/|/home/)[^/\s]+"), r"\1[user]"),
]


def redact(text):
    for rx, repl in PATTERNS:
        text = rx.sub(repl, text)
    return text
