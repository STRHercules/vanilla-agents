"""Shared redact-and-cap for any string that leaves the engine toward the UI."""
from ..verify.redact import redact
from .schema import MAX_STR


def clean(val, cap=MAX_STR):
    return redact(val)[:cap] if isinstance(val, str) else val
