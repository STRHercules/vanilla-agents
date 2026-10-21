"""READ-ONLY view of the Codex desktop app's SQLite history (V-056). The app writes live turns to
<Codex home>/state_*.sqlite (threads) and <Codex home>/thread_history_*.sqlite (turns and items) and may stop appending to the
JSONL rollout until later. This watcher publishes the same bounded feed events as codex.py: thread id, folder, branch, model,
turn status and tool item TYPES. It never selects thread titles, previews, first messages, item_json, prompts or tool arguments.
Unknown or changed schemas are ignored. The JSONL watcher skips the sessions this watcher manages, so one helper is not shown twice."""
import os
import re
import sqlite3
import time
from pathlib import Path

from .codex import ROLE, SESSION_RE, _belongs, _short, db_dir as default_db_dir, settings as codex_settings
from .worktree import locate

TOOL_TYPES = {"commandExecution": "command", "fileChange": "file change", "mcpToolCall": "MCP tool", "sleep": "sleep",
              "webSearch": "web search", "imageView": "image view", "imageGeneration": "image generation",
              "subAgentActivity": "sub-agent", "collabAgentToolCall": "collab agent tool", "dynamicToolCall": "dynamic tool"}
STATUS = {"inProgress": "working", "completed": "idle", "failed": "stunned", "interrupted": "stunned"}


def clean_path(cwd):
    """Codex stores some cwds as Windows extended paths (\\\\?\\R:\\...)."""
    s = str(cwd or "")
    while s.startswith("\\\\?\\"):
        s = s[4:]
    return s


def _newest(folder, stem):
    """The newest state_*.sqlite / thread_history_*.sqlite in the Codex home, or None."""
    found = []
    for p in [folder / (stem + ".sqlite")] + list(folder.glob(stem + "_*.sqlite")):
        if p.is_file():
            m = re.search(r"_(\d+)\.sqlite$", p.name)
            found.append((int(m.group(1)) if m else 0, p))
    return max(found)[1] if found else None


def _connect(path):
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
        con.row_factory = sqlite3.Row
        return con
    except (OSError, sqlite3.Error):
        return None


class CodexDbWatcher:
    """Project-scoped poller for Codex Desktop's SQLite history. Never raises out of poll()."""

    def __init__(self, feed, project_dir, cfg=None, clock=time.time, db_dir=None, env=None, managed=None):
        c = codex_settings(cfg)
        env = os.environ if env is None else env
        self.feed, self.project, self.clock = feed, str(project_dir or ""), clock
        self.root = Path(db_dir).expanduser() if db_dir else default_db_dir(cfg, env)
        self.poll_seconds = max(1.0, float(c.get("poll_seconds", 2)))
        self.recent_seconds = max(60.0, float(c.get("recent_minutes", 120)) * 60)
        self.idle_seconds = max(60.0, float(c.get("idle_minutes", 10)) * 60)
        self.max_agents = max(1, int(c.get("max_agents", 8)))
        self.managed = managed if managed is not None else set()   # thread ids also owned by this watcher (shared with codex.py)
        self.agents = {}

    def _threads(self, now):
        """Recent, unarchived threads for this project, newest first, or None when the database cannot be read."""
        state = _newest(self.root, "state")
        if state is None:
            return None
        con = _connect(state)
        if con is None:
            return None
        try:
            cutoff = now - self.recent_seconds
            try:
                rows = list(con.execute("select id, cwd, updated_at, model, git_branch from threads "
                                        "where archived=0 and updated_at >= ? order by updated_at desc limit 200", (cutoff,)))
            except sqlite3.Error:
                rows = list(con.execute("select id, cwd, updated_at, NULL as model, NULL as git_branch from threads "
                                        "where archived=0 and updated_at >= ? order by updated_at desc limit 200", (cutoff,)))
            out = []
            for r in rows:
                cwd = clean_path(r["cwd"])
                if _belongs(cwd, self.project):
                    out.append({"id": r["id"], "cwd": cwd, "updated_at": r["updated_at"], "model": r["model"],
                                "branch": r["git_branch"]})
                    if len(out) >= self.max_agents:
                        break
            return out
        except sqlite3.Error:
            return None
        finally:
            con.close()

    def refresh_ids(self):
        """Populate the shared managed set before the JSONL watcher starts, so the same session is not announced twice."""
        threads = self._threads(self.clock())
        if threads is not None:
            self.managed.clear()
            self.managed.update(t["id"] for t in threads if isinstance(t.get("id"), str))
        return threads

    @staticmethod
    def _turns(con, ids):
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        try:
            rows = con.execute("select thread_id, status from thread_turns where thread_id in (%s) "
                               "order by started_at desc, rollout_ordinal desc" % marks, ids)
            out = {}
            for r in rows:
                out.setdefault(r["thread_id"], r["status"])
            return out
        except sqlite3.Error:
            return {}

    @staticmethod
    def _items(con, tid, after, first):
        """(item_type, ordinal) rows to report and the new last ordinal. Never reads item_json."""
        if first:
            try:
                last = con.execute("select max(rollout_ordinal) from thread_items where thread_id=?", (tid,)).fetchone()[0] or 0
                rows = list(con.execute("select item_type, rollout_ordinal from thread_items where thread_id=? "
                                        "order by rollout_ordinal desc limit 40", (tid,)))
            except sqlite3.Error:
                return [], after
            rows.reverse()
            keep = [r for r in rows if r["item_type"] in TOOL_TYPES or r["item_type"] == "contextCompaction"]
            return keep[-6:], last
        try:
            rows = list(con.execute("select item_type, rollout_ordinal from thread_items where thread_id=? and rollout_ordinal>? "
                                    "order by rollout_ordinal asc limit 100", (tid, after)))
        except sqlite3.Error:
            return [], after
        last = max([r["rollout_ordinal"] for r in rows], default=after)
        return rows, last

    def _place(self, tid, thread):
        cwd = clean_path(thread.get("cwd"))
        place = {"session": tid, "cwd": _short(cwd, 500)}
        w = locate(cwd)
        place.update({k: _short(w[k], 500) for k in ("root", "worktree", "branch") if w.get(k)})
        branch = _short(thread.get("branch"), 500)
        if branch:
            place["branch"] = branch
        return place

    def _emit(self, ag, kind, data):
        self.feed.append(kind, agent=ag["id"], data=data)
        if kind in ("agent_working", "tool_use"):
            ag["state"] = "working"
        elif kind in ("agent_idle", "agent_stunned"):
            ag["state"] = "idle"

    def poll(self):
        """One nonthrowing pass. Returns the number of events added to the shared feed."""
        count = 0
        now = self.clock()
        threads = self._threads(now)
        if threads is None:
            return 0
        self.managed.clear()
        self.managed.update(t["id"] for t in threads if isinstance(t.get("id"), str))
        hpath = _newest(self.root, "thread_history")
        hcon = _connect(hpath) if hpath else None
        turns = self._turns(hcon, [t["id"] for t in threads]) if hcon else {}
        seen = set()
        for thread in threads:
            tid = thread.get("id")
            if not isinstance(tid, str) or not SESSION_RE.fullmatch(tid):
                continue
            seen.add(tid)
            try:
                ag = self.agents.get(tid)
                first = ag is None
                if first:
                    ag = self.agents[tid] = {"id": "codex-" + tid[:12], "state": "idle", "last_ordinal": 0,
                                             "last_status": None, "model": ""}
                    self.feed.append("agent_started", agent=ag["id"], data={"role": ROLE})
                    self.feed.append("agent_place", agent=ag["id"], data=self._place(tid, thread))
                    count += 2
                model = _short(thread.get("model"), 100)
                if model and model != ag.get("model"):
                    ag["model"] = model
                    self._emit(ag, "session_stats", {"model": model})
                    count += 1
                if hcon:
                    rows, last = self._items(hcon, tid, ag["last_ordinal"], first)
                    for row in rows:
                        typ = row["item_type"]
                        if typ == "contextCompaction":
                            self._emit(ag, "agent_rested", {"phase": "done"})
                            count += 1
                        elif typ in TOOL_TYPES:
                            self._emit(ag, "tool_use", {"tool": TOOL_TYPES[typ]})
                            count += 1
                    ag["last_ordinal"] = last
                status = turns.get(tid)
                if status:
                    kind = STATUS.get(status)
                    if kind == "working":
                        if first or ag.get("last_status") != "inProgress":
                            self._emit(ag, "agent_working", {"activity": "working on a Codex request"})
                            count += 1
                    elif kind == "idle":
                        if first or ag.get("last_status") == "inProgress":
                            self._emit(ag, "agent_idle", {})
                            count += 1
                    elif kind == "stunned":
                        if first or ag.get("last_status") != status:
                            self._emit(ag, "agent_stunned", {"reason": "turn " + status})
                            count += 1
                    ag["last_status"] = status
            except Exception:
                continue
        for tid, ag in list(self.agents.items()):
            if tid not in seen and ag["state"] == "working":
                try:
                    self._emit(ag, "agent_idle", {"note": "Codex session is inactive"})
                    count += 1
                except Exception:
                    continue
        if hcon:
            hcon.close()
        return count

    def run(self, stop):
        while not stop.is_set():
            self.poll()
            stop.wait(self.poll_seconds)
