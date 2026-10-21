"""READ-ONLY watcher of Codex CLI session rollouts (JSONL under CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl).
Codex writes these files itself; nothing here edits Codex settings, sessions or project files. The watcher tails new records by byte
offset and appends bounded events to the existing feed, next to the Claude hooks and transcript watcher (V-053).
Rollout text is untrusted data: only session id, folder, model, turn start/end, tool names and output-token counts are kept. Prompts,
answers, reasoning, tool arguments and terminal output are ignored and never stored or logged. Never raises out of poll()."""
import json
import os
import re
import stat
import time
from pathlib import Path

from .worktree import locate

MAX_META = 1_000_000
READ_CHUNK = 1_000_000
MAX_LINE = 16_000_000
BASELINE_TAIL = 128 * 1024
BASELINE_EVENTS = 6
SESSION_RE = re.compile(r"^[0-9a-fA-F-]{8,64}\Z")
FILE_RE = re.compile(r"^rollout-.*\.jsonl\Z")
ROLE = "Codex"
DEFAULTS = {"enabled": True, "sessions_dir": "", "db_dir": "", "offsets_path": "~/.vanilla-agents/work/codex-offsets.json",
            "poll_seconds": 2, "idle_minutes": 10, "recent_minutes": 120, "max_agents": 8}


def settings(cfg=None):
    """The codex group of app.json with defaults filled in (cfg = the app settings, not the whole file tree)."""
    c = dict(DEFAULTS)
    group = cfg.get("codex") if isinstance(cfg, dict) and isinstance(cfg.get("codex"), dict) else {}
    c.update(group)
    return c


def enabled(cfg=None, env=os.environ):
    """VANILLA_CODEX_ENABLED=0 turns only the Codex watcher off; the app.json switch is the normal way."""
    flag = env.get("VANILLA_CODEX_ENABLED")
    if flag is not None:
        return flag.strip().lower() not in ("0", "false", "no")
    return bool(settings(cfg)["enabled"])


def sessions_root(cfg=None, env=os.environ):
    """VANILLA_CODEX_SESSIONS, else app.json codex.sessions_dir, else CODEX_HOME/sessions (usually ~/.codex/sessions)."""
    c = settings(cfg)
    root = env.get("VANILLA_CODEX_SESSIONS") or c["sessions_dir"] or (Path(env.get("CODEX_HOME") or Path.home() / ".codex") / "sessions")
    return Path(root).expanduser()


def offsets_file(cfg=None, env=os.environ):
    """Where read positions are kept, outside the project folder."""
    return Path(env.get("VANILLA_CODEX_OFFSETS") or settings(cfg)["offsets_path"]).expanduser()


def db_dir(cfg=None, env=os.environ):
    """The Codex home holding the app's SQLite history (state_*.sqlite, thread_history_*.sqlite)."""
    c = settings(cfg)
    return Path(env.get("VANILLA_CODEX_DB_DIR") or c["db_dir"] or (Path(env.get("CODEX_HOME") or Path.home() / ".codex"))).expanduser()


def _pathkey(path):
    """A project key that also works with Windows paths when tests run on Unix. Bad paths get '' (never a crash)."""
    try:
        s = str(path or "").replace("\\", "/").rstrip("/")
        if re.match(r"^[a-zA-Z]:/", s):
            return s.casefold()
        return os.path.normcase(os.path.abspath(s)).replace("\\", "/").rstrip("/")
    except (TypeError, ValueError):
        return ""


def _belongs(cwd, project):
    a, b = _pathkey(cwd), _pathkey(project)
    return bool(a and b and (a == b or a.startswith(b + "/")))


def _short(s, n=140):
    return s[:n] if isinstance(s, str) else ""


def _meta(path):
    """Read just the session header. Never persist source text or instructions."""
    try:
        with open(path, "rb") as fh:
            line = fh.readline(MAX_META + 1)
        if len(line) > MAX_META or not line.endswith(b"\n"):
            return None
        obj = json.loads(line)
        if not isinstance(obj, dict) or obj.get("type") != "session_meta" or not isinstance(obj.get("payload"), dict):
            return None
        p = obj["payload"]
        sid = p.get("id") or p.get("session_id")
        cwd = p.get("cwd")
        if not isinstance(sid, str) or not SESSION_RE.fullmatch(sid) or not isinstance(cwd, str) or not cwd:
            return None
        return sid, cwd
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _tool_name(payload):
    name = payload.get("name") or payload.get("tool") or "Codex tool"
    return _short(name, 80) or "Codex tool"


def _events(record, session):
    """One decoded rollout record -> feed-ready events. Prompts, answers, reasoning and tool output give []."""
    if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
        return []
    kind, p = record.get("type"), record["payload"]
    sub = p.get("type")
    if kind == "turn_context":
        model = _short(p.get("model"), 100)
        if model and model != session.get("model"):
            session["model"] = model
            return [("session_stats", {"model": model})]
    if kind == "compacted":
        return [("agent_rested", {"phase": "done"})]
    if kind == "event_msg":
        if sub in ("task_started", "user_message"):
            return [("agent_working", {"activity": "working on a Codex request"})]
        if sub in ("task_complete", "turn_complete"):
            return [("agent_idle", {})]
        if sub == "turn_aborted":
            return [("agent_stunned", {"reason": _short(p.get("reason"), 60) or "turn aborted"})]
        if sub == "token_count":
            usage = p.get("info")
            if isinstance(usage, dict):
                total = usage.get("total_token_usage")
                if not isinstance(total, dict):
                    total = usage.get("last_token_usage")
                if isinstance(total, dict):
                    count = total.get("output_tokens")
                    if type(count) is int and 0 <= count and count != session.get("output_tokens"):
                        session["output_tokens"] = count
                        return [("session_stats", {"tokens_out": count})]
        if sub in ("context_compacted", "compaction"):
            return [("agent_rested", {"phase": "done"})]
        if sub == "mcp_tool_call_end":
            invocation = p.get("invocation")
            if isinstance(invocation, dict):
                return [("tool_use", {"tool": _tool_name(invocation)})]
    if kind == "response_item":
        if sub in ("function_call", "custom_tool_call"):
            return [("tool_use", {"tool": _tool_name(p)})]
        if sub in ("compaction", "context_compacted"):
            return [("agent_rested", {"phase": "done"})]
    return []


class CodexWatcher:
    """Project-scoped Codex session poller. Safe to run on the same feed as Claude."""

    def __init__(self, feed, project_dir, cfg=None, clock=time.time, root=None, offsets_path=None, env=None, skip_sessions=None):
        c = settings(cfg)
        env = os.environ if env is None else env
        self.feed, self.project, self.clock = feed, str(project_dir or ""), clock
        self.skip_sessions = skip_sessions if skip_sessions is not None else set()   # V-056: the app's SQLite history owns these
        self.root = Path(root).expanduser() if root else sessions_root(cfg, env)
        self.offsets_path = Path(offsets_path).expanduser() if offsets_path else offsets_file(cfg, env)
        self.poll_seconds = max(1.0, float(c.get("poll_seconds", 2)))
        self.recent_seconds = max(60.0, float(c.get("recent_minutes", 120)) * 60)
        self.idle_seconds = max(60.0, float(c.get("idle_minutes", 10)) * 60)
        self.max_agents = max(1, int(c.get("max_agents", 8)))
        self.offsets = self._load()
        self.agents = {}  # keyed by rollout path: one agent per session

    def _load(self):
        try:
            obj = json.loads(self.offsets_path.read_text(encoding="utf-8"))
            return {k: v for k, v in obj.items() if isinstance(k, str) and type(v) is int and v >= 0} if isinstance(obj, dict) else {}
        except (OSError, UnicodeError, ValueError):
            return {}

    def _save(self):
        try:
            self.offsets_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.offsets_path.with_name(self.offsets_path.name + ".tmp")
            tmp.write_text(json.dumps(self.offsets, ensure_ascii=True), encoding="utf-8")
            os.replace(tmp, self.offsets_path)
        except OSError:
            pass

    def _candidates(self):
        now = self.clock()
        found = []
        if not self.root.is_dir():
            return found
        for folder, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if not FILE_RE.fullmatch(name):
                    continue
                path = os.path.abspath(os.path.join(folder, name))
                try:
                    st = os.stat(path)
                    if stat.S_ISREG(st.st_mode) and now - st.st_mtime <= self.recent_seconds:
                        found.append((st.st_mtime, path))
                except OSError:
                    continue
        found.sort(reverse=True)
        out = []
        for mtime, path in found:
            metadata = _meta(path)
            if metadata and metadata[0] not in self.skip_sessions and _belongs(metadata[1], self.project):
                out.append((path, mtime, *metadata))
                if len(out) >= self.max_agents:
                    break
        return out

    def _new_lines(self, path, size):
        """Return complete JSONL records with a restart-safe byte offset. A partial last line waits for the next poll."""
        old = self.offsets.get(path)
        baseline = old is None
        off = max(0, size - BASELINE_TAIL) if baseline else min(old, size)
        if not baseline and old > size:
            off, baseline = 0, True
        want = READ_CHUNK
        with open(path, "rb") as fh:
            while True:
                fh.seek(off)
                data = fh.read(want)
                cut = data.rfind(b"\n")
                if cut >= 0 or len(data) < want:
                    break
                if want >= MAX_LINE:   # one enormous record: skip it
                    return [], off + len(data), baseline
                want *= 4
        if cut < 0:
            return [], off, baseline
        lines = data[:cut].split(b"\n")
        if baseline and off > 0:
            lines = lines[1:]   # first line is cut in the middle
        return lines, off + cut + 1, baseline

    def _emit(self, agent, evs):
        n = 0
        for kind, data in evs:
            if kind == "agent_idle" and agent["state"] == "idle":
                continue
            self.feed.append(kind, agent=agent["id"], data=data)
            if kind in ("agent_working", "tool_use"):
                agent["state"] = "working"
            elif kind in ("agent_idle", "agent_stunned"):
                agent["state"] = "idle"
            n += 1
        return n

    def poll(self):
        """One nonthrowing pass. Return the number of events added to the shared feed."""
        count, dirty, seen = 0, False, set()
        try:
            candidates = self._candidates()
        except Exception:
            return 0
        for path, mtime, sid, cwd in candidates:
            seen.add(path)
            try:
                ag = self.agents.get(path)
                if ag is None:
                    ag = self.agents[path] = {"id": "codex-" + sid[:12], "state": "idle", "session": sid, "cwd": cwd}
                    self.feed.append("agent_started", agent=ag["id"], data={"role": ROLE})
                    place = {"session": sid, "cwd": _short(cwd, 500)}
                    w = locate(cwd)
                    place.update({k: _short(w[k], 500) for k in ("root", "worktree", "branch") if w.get(k)})
                    self.feed.append("agent_place", agent=ag["id"], data=place)
                    count += 2
                size = os.path.getsize(path)
                lines, off, baseline = self._new_lines(path, size)
                evs = []
                for line in lines:
                    try:
                        record = json.loads(line)
                        evs.extend(_events(record, ag))
                    except (ValueError, TypeError, UnicodeError):
                        continue
                if baseline:
                    evs = evs[-BASELINE_EVENTS:]
                count += self._emit(ag, evs)
                if off != self.offsets.get(path):
                    self.offsets[path] = off
                    dirty = True
                if ag["state"] == "working" and self.clock() - mtime > self.idle_seconds:
                    count += self._emit(ag, [("agent_idle", {"note": "no Codex activity recently"})])
            except Exception:
                continue
        for path, ag in list(self.agents.items()):
            if path not in seen and ag["state"] == "working":
                try:
                    count += self._emit(ag, [("agent_idle", {"note": "Codex session is inactive"})])
                except Exception:
                    continue
        for path in [p for p in self.offsets if p not in seen and not os.path.exists(p)]:
            del self.offsets[path]
            self.agents.pop(path, None)
            dirty = True
        if dirty:
            self._save()
        return count

    def run(self, stop):
        """Thread body: poll every poll_seconds until the threading.Event `stop` is set."""
        while not stop.is_set():
            self.poll()
            stop.wait(self.poll_seconds)
