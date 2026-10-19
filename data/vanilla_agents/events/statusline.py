"""Statusline bridge (V-030). Claude Code runs a status line command with the session's own numbers as JSON on stdin (model,
context_window.used_percentage, cost.*, rate_limits.five_hour / seven_day; https://code.claude.com/docs/en/statusline). This turns them
into a `session_stats` feed event for that helper (same agent id as the hooks), at most once per session every THROTTLE_SECONDS, and
prints a status line: the one you had before (run with the same stdin, STATUS_TIMEOUT seconds at most) or a short default.
Like the hooks it must never break Claude: every error is swallowed and the exit code is always 0. Every input field may be missing."""
import json
import math
import os
import subprocess
import sys
import threading
import time

from .feed import Feed
from .hooks import _agent, default_feed_path

THROTTLE_SECONDS = 20
STATUS_TIMEOUT = 2
FEED_WAIT = 1.0
MAX_STDIN = 1_000_000
STATE_NAME = "statusline-state.json"      # next to the feed: {session_id: last event time}
STORE_NAME = "statusline.json"            # next to the feed: {"previous": <your old statusLine setting or null>} (install_statusline.py)
MARK = "statusline.py"
DEFAULT_LINE = "⚔ {model}{ctx}{five}"


def _num(v, top=None):
    """A finite number >= 0 (capped at top), or None for anything else (missing, null, text, bool)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return min(v, top) if top is not None else v


def _get(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def to_data(h):
    """Statusline JSON -> session_stats data (only the fields that are present and sane)."""
    out = {}
    model = _get(h, "model", "display_name") or _get(h, "model", "id")
    if isinstance(model, str) and model.strip():
        out["model"] = model.strip()[:80]
    for key, path, top in (("ctx_pct", ("context_window", "used_percentage"), 100), ("cost_usd", ("cost", "total_cost_usd"), None),
                           ("lines_added", ("cost", "total_lines_added"), None), ("lines_removed", ("cost", "total_lines_removed"), None),
                           ("duration_ms", ("cost", "total_duration_ms"), None),
                           ("five_hour_pct", ("rate_limits", "five_hour", "used_percentage"), 100), ("five_hour_resets", ("rate_limits", "five_hour", "resets_at"), None),
                           ("seven_day_pct", ("rate_limits", "seven_day", "used_percentage"), 100), ("seven_day_resets", ("rate_limits", "seven_day", "resets_at"), None)):
        v = _num(_get(h, *path), top)
        if v is not None and v < 1e15:
            out[key] = round(v, 6) if isinstance(v, float) else v
    return out


def default_line(h):
    d = to_data(h)
    ctx = " · ctx %d%%" % round(d["ctx_pct"]) if "ctx_pct" in d else ""
    five = " · 5h %d%%" % round(d["five_hour_pct"]) if "five_hour_pct" in d else ""
    return DEFAULT_LINE.format(model=d.get("model", "Claude"), ctx=ctx, five=five)


def due(state_path, session, now, throttle=THROTTLE_SECONDS):
    """True (and remembers now) when this session has not sent an event in the last `throttle` seconds. Old sessions are forgotten."""
    try:
        with open(state_path, encoding="utf-8") as f:
            st = json.load(f)
        st = st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        st = {}
    last = st.get(session)
    if isinstance(last, (int, float)) and not isinstance(last, bool) and 0 <= now - last < throttle:
        return False
    st = {k: v for k, v in st.items() if isinstance(v, (int, float)) and now - v < 86400}
    st[session] = now
    try:
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        tmp = "%s.%d.tmp" % (state_path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, state_path)
    except OSError:
        pass
    return True


def previous_command(store_path):
    """Your old status line command saved at install time, or None. Never our own script (no loop)."""
    try:
        with open(store_path, encoding="utf-8") as f:
            prev = (json.load(f) or {}).get("previous")
    except (OSError, ValueError, AttributeError):
        return None
    cmd = prev.get("command") if isinstance(prev, dict) else None
    return cmd if isinstance(cmd, str) and cmd.strip() and MARK not in cmd else None


def run_previous(cmd, raw, run=subprocess.run, timeout=STATUS_TIMEOUT):
    """Output of your old status line command fed the same stdin, or None when it fails or is slow."""
    try:
        r = run(cmd, shell=True, input=raw, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
    return out.rstrip("\r\n") if r.returncode == 0 and out.strip() else None


def record(raw, feed_path, now=None):
    """Append a session_stats event when due. Returns the event or None."""
    h = json.loads(raw)
    if not isinstance(h, dict) or not h.get("session_id"):
        return None
    data = to_data(h)
    if not data:
        return None
    state = os.path.join(os.path.dirname(os.path.abspath(feed_path)), STATE_NAME)
    if not due(state, str(h["session_id"]), time.time() if now is None else now):
        return None
    return Feed(feed_path).append("session_stats", agent=_agent(h), data=data)


def main(stdin=None, stdout=None, feed_path=None, run=subprocess.run):
    """Entry point for scripts/statusline.py. Always returns 0."""
    out = stdout or getattr(sys.stdout, "buffer", sys.stdout)
    raw, h = b"", {}
    try:
        raw = (stdin or sys.stdin.buffer).read(MAX_STDIN)
        h = json.loads(raw) if raw.strip() else {}
        h = h if isinstance(h, dict) else {}
    except BaseException:
        pass
    feed_path = feed_path or default_feed_path()
    try:     # the feed write gets FEED_WAIT seconds at most (a held lock must not freeze the status line)
        t = threading.Thread(target=lambda: _quiet(record, raw, feed_path), daemon=True)
        t.start()
        t.join(FEED_WAIT)
    except BaseException:
        pass
    line = None
    try:
        cmd = previous_command(os.path.join(os.path.dirname(os.path.abspath(feed_path)), STORE_NAME))
        line = run_previous(cmd, raw, run) if cmd else None
        if line is None:
            line = default_line(h)
    except BaseException:
        line = "⚔ Claude"
    try:
        out.write(line.encode("utf-8") if not hasattr(out, "encoding") else line)
        out.flush()
    except BaseException:
        pass
    return 0


def _quiet(fn, *a):
    try:
        fn(*a)
    except BaseException:
        pass
