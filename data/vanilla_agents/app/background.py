"""Background session discovery (V-032). When a `claude` command exists, `claude agents --json` lists the Claude Code sessions on this
PC (https://code.claude.com/docs/en/agent-view: cwd, kind, startedAt always; sessionId, name, pid, status busy|waiting|idle,
waitingFor, and for background sessions id + state working|blocked|done|failed|stopped). A daemon thread asks every few seconds
(with a timeout) and keeps the last answer; the request threads only read that copy. Sessions the hooks have not reported yet appear
as helpers with role "background". Older Claude Code versions without the command (or any error) simply give no sessions."""
import json
import shutil
import subprocess
import threading
import time

ROLE = "background"
# waitingFor (docs) -> the window's "needs you" words (V-026)
NEEDS = {"permission prompt": "permission", "sandbox request": "permission", "input needed": "input", "dialog open": "question", "worker request": "question"}


def agent_id(s):
    """Same id as the hooks give the session (events/hooks.py _agent: 'claude-' + first 8 of the session id); 'bg-<id>' without one."""
    sid = s.get("sessionId")
    if isinstance(sid, str) and sid:
        return "claude-" + sid[:8]
    short = s.get("id")
    return "bg-" + short[:16] if isinstance(short, str) and short else None


def parse(text):
    """`claude agents --json` output -> [{id, state, needs?, needs_note?, stunned?, cwd, name, kind}]. Bad output -> []."""
    try:
        rows = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out, seen = [], set()
    for s in rows:
        if not isinstance(s, dict):
            continue
        aid = agent_id(s)
        state, status = str(s.get("state") or ""), str(s.get("status") or "")
        if not aid or aid in seen or state == "stopped":
            continue
        seen.add(aid)
        a = {"id": aid, "state": "working" if status == "busy" or (state == "working" and status != "waiting") else "idle",
             "cwd": str(s.get("cwd") or ""), "name": str(s.get("name") or "")[:80], "kind": str(s.get("kind") or "")}
        sid = s.get("sessionId")
        if isinstance(sid, str) and 8 <= len(sid) <= 64 and all(c in "0123456789abcdefABCDEF-" for c in sid):
            a["session"] = sid          # V-039: Resurrect resumes this session
        wf = str(s.get("waitingFor") or "")
        if status == "waiting" or state == "blocked":
            a["needs"], a["needs_note"] = NEEDS.get(wf, "input"), wf
        if state == "failed":
            a["stunned"] = "failed"
        out.append(a)
    return out


def merge(agents, sessions, now=None):
    """The snapshot's agents plus discovered sessions that have no hook/feed activity yet (as idle-style 'background' helpers)."""
    have = {a.get("id") for a in agents}
    now = time.time() if now is None else now
    extra = []
    for s in sessions:
        if s["id"] in have:
            continue
        a = {"id": s["id"], "role": ROLE, "state": s["state"], "activity": "", "task": None, "actions": 0, "tools": {}, "passed": 0, "failed": 0,
             "seen": now, "background": True, "name": s["name"], "cwd": s["cwd"]}
        for k in ("needs", "needs_note", "stunned", "session"):
            if k in s:
                a[k] = s[k]
        extra.append(a)
    return list(agents) + extra


class Discovery:
    """Polls `claude agents --json` on its own daemon thread. sessions() never blocks on the command."""

    def __init__(self, interval=15, timeout=10, run=subprocess.run, which=shutil.which):
        self.interval, self.timeout, self.run, self.which = interval, timeout, run, which
        self.lock = threading.Lock()
        self._sessions, self.version, self.thread = [], 0, None

    def sessions(self):
        with self.lock:
            return list(self._sessions)

    def poll_once(self):
        """One ask. Returns the sessions now held (unchanged on any failure except a missing command, which clears them)."""
        exe = self.which("claude")
        if not exe:
            new = []
        else:
            try:
                r = self.run([exe, "agents", "--json"], capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError, ValueError):
                return self.sessions()
            new = parse(r.stdout) if r.returncode == 0 else []     # an old version prints usage / an error: nothing to show
        with self.lock:
            if new != self._sessions:
                self._sessions = new
                self.version += 1
            return list(self._sessions)

    def start(self, stop, first_delay=2):
        def loop():
            if stop.wait(first_delay):
                return
            while True:
                try:
                    self.poll_once()
                except Exception:
                    pass         # discovery is a convenience; it must never break the window
                if stop.wait(self.interval):
                    return
        self.thread = threading.Thread(target=loop, name="claude-agents", daemon=True)
        self.thread.start()
        return self.thread
