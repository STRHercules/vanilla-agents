"""READ-ONLY watcher of Claude Code session transcripts (JSONL under ~/.claude/projects/<project-dir>/). Needs no hooks and edits no
settings (D-200). Tails new lines by byte offset, maps records to the existing event schema and appends them to the feed (which redacts).
Transcript text is untrusted data: only tool names, truncated file/command, sub-agent labels and counts are kept; nothing else is stored or logged.
Never raises out of poll(): unknown records, bad JSON, vanished files are ignored."""
import json
import os
import re
import time
from pathlib import Path


CHUNK = 1_000_000          # bytes read per file per poll
MAX_LINE = 16_000_000      # a single record bigger than this is skipped
BASELINE_TAIL = 128 * 1024  # first sight of a file: look only at this much of its end
BASELINE_EVENTS = 6         # ...and keep only the last few events from it
FILE_CAP, CMD_CAP = 160, 200
DEFAULTS = {"transcripts_dir": "", "transcript_offsets_path": "~/.vanilla-agents/work/transcript-offsets.json", "transcript_poll_seconds": 2,
            "transcript_idle_minutes": 10, "transcript_recent_minutes": 120, "transcript_max_agents": 8, "transcript_context_tokens": 150000}
_NONALNUM = re.compile(r"[^A-Za-z0-9]")


def claude_dir_name(project_path):
    """Claude's rule (checked against the real folder names): every character that is not a letter or digit becomes '-'."""
    return _NONALNUM.sub("-", str(project_path))


def find_project_dir(project_path, root):
    """The existing Claude projects sub-folder for a project path (case-insensitive on Windows), or None."""
    root = Path(root)
    want = {claude_dir_name(project_path).lower(), claude_dir_name(os.path.abspath(str(project_path))).lower()}
    try:
        for d in root.iterdir():
            if d.name.lower() in want and d.is_dir():
                return d
    except OSError:
        pass
    return None


def default_root():
    return Path.home() / ".claude" / "projects"


def _cap(s, n):
    return s[:n] if isinstance(s, str) else ""


def _tool_event(block):
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    d = {"tool": _cap(block.get("name"), 80) or "a tool"}
    f = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    if isinstance(f, str) and f:
        d["file"] = f[:FILE_CAP]
    c = inp.get("command") or (inp.get("description") if d["tool"] in ("Agent", "Task") else None)
    if isinstance(c, str) and c:
        d["command"] = c[:CMD_CAP]
    return d


class _Agent:
    """Per-transcript-file state (bounded: the tool-name memo is capped)."""

    def __init__(self, name, role=""):
        self.name, self.role, self.state, self.pending = name, role, None, {}
        self.started = False
        self.last_pct = None
        self.placed = False                      # V-038/V-039: agent_place sent (session id, folder, checkout)
        self.tok_total, self.tok_msg, self.tok_msg_n = 0, None, 0     # output tokens seen (one message spans several records: counted once)
        self.tok_sent, self.tok_at = 0, 0.0

    def remember(self, tid, name):
        if len(self.pending) > 200:
            self.pending.pop(next(iter(self.pending)))
        self.pending[tid] = name


TOKENS_EVERY = 15      # seconds between two token reports for one transcript


def count_tokens(rec, ag):
    """Add an assistant record's output tokens to ag.tok_total. Records of the same message repeat its usage: only the growth counts."""
    msg = rec.get("message") if isinstance(rec, dict) else None
    if not isinstance(msg, dict) or rec.get("type") != "assistant" or not isinstance(msg.get("usage"), dict):
        return
    n = msg["usage"].get("output_tokens")
    if isinstance(n, bool) or not isinstance(n, (int, float)) or n < 0:
        return
    mid = msg.get("id")
    if mid is not None and mid == ag.tok_msg:
        ag.tok_total += max(0, int(n) - ag.tok_msg_n)
        ag.tok_msg_n = max(ag.tok_msg_n, int(n))
    else:
        ag.tok_total += int(n)
        ag.tok_msg, ag.tok_msg_n = mid, int(n)


def place_of(rec, session):
    """agent_place data from a record's working folder (Claude Code writes `cwd` on its records), or None."""
    cwd = rec.get("cwd") if isinstance(rec, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return None
    from .worktree import locate
    d = {"cwd": cwd[:500]}
    if session and 8 <= len(session) <= 64 and all(c in "0123456789abcdefABCDEF-" for c in session):
        d["session"] = session
    w = locate(cwd)
    d.update({k: str(w[k])[:500] for k in ("root", "worktree", "branch") if w.get(k)})
    return d


def map_record(rec, ag, main, window):
    """One decoded record -> [(type, data)]. Unknown shapes give []."""
    if not isinstance(rec, dict):
        return []
    t, msg, out = rec.get("type"), rec.get("message"), []
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if t == "user":
        if rec.get("isMeta"):
            return []
        if isinstance(content, str):
            return [("agent_working", {"activity": "working on a new request"})]
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error") is True:
                name = _cap(ag.pending.get(b.get("tool_use_id")), 60) or "a tool"
                out.append(("tool_use", {"tool": name + " (it hit an error)"}))
        return out
    if t == "assistant":
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                ev = _tool_event(b)
                if isinstance(b.get("id"), str):
                    ag.remember(b["id"], ev["tool"])
                out.append(("tool_use", ev))
        u = msg.get("usage")
        if main and isinstance(u, dict) and window > 0:
            n = sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens") if isinstance(u.get(k), (int, float)))
            pct = min(100, round(100.0 * n / window))
            if n and pct != ag.last_pct:
                ag.last_pct = pct
                out.append(("usage_update", {"used_pct": pct, "resets_at": "(this is how full the conversation memory is, not the plan limit)"}))
        if msg.get("stop_reason") == "end_turn":
            out.append(("agent_idle", {}))
    return out


class TranscriptWatcher:
    def __init__(self, feed, project_dir, cfg=None, clock=time.time):
        c = dict(DEFAULTS)
        c.update(cfg or {})
        self.feed, self.dir, self.c, self.clock = feed, Path(project_dir), c, clock
        self.offsets_path = Path(c["transcript_offsets_path"]).expanduser()
        self.offsets = self._load()
        self.agents = {}

    # offsets -------------------------------------------------------------
    def _load(self):
        try:
            d = json.loads(self.offsets_path.read_text(encoding="utf-8"))
            return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, int) and v >= 0} if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            self.offsets_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.offsets_path.with_name(self.offsets_path.name + ".tmp")
            tmp.write_text(json.dumps(self.offsets), encoding="utf-8")
            os.replace(tmp, self.offsets_path)
        except OSError:
            pass

    # discovery -----------------------------------------------------------
    def _candidates(self):
        """(path, mtime, agent name, role) of recently touched transcripts, newest first, capped. Main sessions and their sub-agents."""
        now, horizon, found = self.clock(), self.c["transcript_recent_minutes"] * 60, []
        try:
            entries = list(os.scandir(self.dir))
        except OSError:
            return []
        for e in entries:
            try:
                if e.is_file() and e.name.endswith(".jsonl"):
                    found.append((e.path, e.stat().st_mtime, "Claude session " + e.name[:4], "", e.name[:-6]))
                elif e.is_dir() and (Path(e.path) / "subagents").is_dir():
                    for s in os.scandir(Path(e.path) / "subagents"):
                        if s.is_file() and s.name.endswith(".jsonl"):
                            typ, desc = self._meta(s.path)
                            found.append((s.path, s.stat().st_mtime, "%s %s" % (typ, s.name[-10:-6]), desc, s.name))
            except OSError:
                continue
        found = [f for f in found if now - f[1] <= horizon]
        found.sort(key=lambda f: -f[1])
        return found[: self.c["transcript_max_agents"]]

    @staticmethod
    def _meta(jsonl_path):
        try:
            m = json.loads(Path(jsonl_path[:-6] + ".meta.json").read_text(encoding="utf-8"))
            return (_cap(m.get("agentType"), 40) or "helper"), _cap(m.get("description"), 80)
        except (OSError, ValueError, AttributeError):
            return "helper", ""

    # tailing -------------------------------------------------------------
    def _read_new(self, path, size):
        """(list of raw complete lines, new offset, baseline?). Partial last line is left for the next poll."""
        off = self.offsets.get(path)
        baseline = off is None
        if baseline:
            off = max(0, size - BASELINE_TAIL)
        elif off > size:   # rotated or replaced: start over
            off = 0
        want = CHUNK
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

    def _emit(self, ag, name, evs):
        for typ, data in evs:
            if typ == "agent_idle" and ag.state == "idle":
                continue
            ag.state = "idle" if typ == "agent_idle" else "working" if typ in ("agent_working", "tool_use") else ag.state
            self.feed.append(typ, agent=name, data=data)

    def poll(self):
        """One pass. Returns the number of events appended. Never raises."""
        n, changed, seen = 0, False, set()
        try:
            cands = self._candidates()
        except Exception:
            return 0
        for path, mtime, name, role, fid in cands:
            seen.add(path)
            try:
                main = path.endswith(".jsonl") and os.path.dirname(path) == str(self.dir)
                ag = self.agents.setdefault(path, _Agent(name, role))
                if not ag.started:
                    ag.started = True
                    self.feed.append("agent_started", agent=name, data={"role": role or "main session"})
                    n += 1
                size = os.path.getsize(path)
                lines, new_off, baseline = self._read_new(path, size)
                evs = []
                for ln in lines:
                    try:
                        rec = json.loads(ln)
                        count_tokens(rec, ag)
                        if main and not ag.placed:
                            pl = place_of(rec, fid[:-6] if fid.endswith(".jsonl") else fid)
                            if pl:
                                ag.placed = True
                                self.feed.append("agent_place", agent=name, data=pl)
                        evs.extend(map_record(rec, ag, main, self.c["transcript_context_tokens"]))
                    except Exception:
                        continue
                if baseline:
                    evs = evs[-BASELINE_EVENTS:]
                before = len(evs)
                self._emit(ag, name, evs)
                n += before
                if ag.tok_total != ag.tok_sent and (self.clock() - ag.tok_at >= TOKENS_EVERY or ag.state == "idle"):
                    ag.tok_sent, ag.tok_at = ag.tok_total, self.clock()
                    self.feed.append("session_stats", agent=name, data={"tokens_out": ag.tok_total})
                    n += 1
                if self.offsets.get(path) != new_off:
                    self.offsets[path] = new_off
                    changed = True
                if ag.state == "working" and self.clock() - mtime > self.c["transcript_idle_minutes"] * 60:
                    self._emit(ag, name, [("agent_idle", {"note": "no activity for a while, probably away"})])
                    n += 1
            except Exception:
                continue
        for p, ag in self.agents.items():   # fell out of the recent window or the top-N cap: shown as away, not stuck "working"
            if p not in seen and ag.state == "working":
                try:
                    self._emit(ag, ag.name, [("agent_idle", {"note": "no activity for a while, probably away"})])
                    n += 1
                except Exception:
                    pass
        for p in [p for p in self.offsets if p not in seen and not os.path.exists(p)]:   # removed files
            del self.offsets[p]
            self.agents.pop(p, None)
            changed = True
        if changed:
            self._save()
        return n

    def run(self, stop):
        """Thread body: sleep-based loop (poll interval >= 1 s), ends when `stop` (threading.Event) is set."""
        while not stop.is_set():
            self.poll()
            stop.wait(max(1.0, float(self.c["transcript_poll_seconds"])))
