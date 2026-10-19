"""vanilla-agents, Stage 1: deterministic state, authority, evidence and audit foundation (standard library only)."""
from pathlib import Path as _P
try:
    __version__ = (_P(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    __version__ = "dev"
