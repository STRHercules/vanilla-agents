"""The picture the UI draws. Folds events (from the database history, then the live feed) into one plain snapshot.
The UI never touches the database (DESIGN 7.5); this module is the only reader. Plain-language text comes from fixed templates."""
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

from ..events import derive
from ..events.eli5 import render
from ..events.feed import Feed
from ..storage import layout
from . import approvals, contests, zones, zones_api
from .verify_job import load_registry, slug_of, verify_task, _norm

_DB_STATUS = {"VERIFIED": "complete", "FAILED": "failed"}
_QUEST = re.compile(r"^([^:\s]+):q:")
QUIET = {"session_stats", "agent_place"}      # V-030: numbers only (every ~20 s per session); V-038: where a session runs. Folded into state, never a chat line
PLACE = ("session", "cwd", "root", "worktree", "branch")
TOKEN_SAMPLES = 24
_STATS = ("model", "ctx_pct", "cost_usd", "lines_added", "lines_removed", "duration_ms")



def _norm_path(p):
    """Comparable form of a path: forward slashes, no trailing slash, lower case when it looks like a Windows path."""
    p = str(p or "").replace("\\", "/").rstrip("/")
    return p.lower() if re.match(r"^[A-Za-z]:/", p) else p


def _age_s(ts):
    """Seconds since an ISO event time (or a number); very old when unknown."""
    import datetime
    try:
        if isinstance(ts, (int, float)):
            return time.time() - ts
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc))).total_seconds()
    except (ValueError, TypeError):
        return 1e9


class Folder:
    """Pure: apply(event) updates tasks/agents/leases/usage and returns the chat line for it."""

    def __init__(self, templates, metaphors, chat_max=200, files_max=40, require_approval=False, records=None, scope=None, contest_records=None):
        self.t, self.m = templates, metaphors["events"]
        self.contests = dict(contest_records or {})      # V-045: the owner's competitions (contests.json); ONLY these make a competition or a winner real
        self.props = {}         # V-045: quest -> {helper: proposal} from fix_proposed / fix_checked
        self.rec = dict(records or {})       # D-370/D-380: the stored decisions (who, why, vetoed) from approvals.py's file; ONLY these make a quest approved, a feed line alone never does
        self.scope = scope       # D-382: (quest-id prefix, verify task id) of the open project; None = no filtering. Other projects' quests share the feed but are not shown here
        self.require_approval = require_approval      # D-312
        self.file_tools, self.files_max = set(metaphors.get("work_products", {}).get("file_tools", [])), files_max
        self.files = []     # backpack: files agents changed, newest first, no repeats (D-243)
        self.tasks, self.agents, self.leases, self.lease_exp = {}, {}, set(), {}
        self.editors = {}       # (checkout root, path inside it) -> {agent: ts}  (V-038: the same name in another worktree is another file)
        self.roots = set()      # checkout roots helpers reported (agent_place)
        self.departed = {}      # V-039: helpers whose session ended: id -> where it ran (session, cwd, branch...), so Resurrect can bring it back
        self.usage = {"used_pct": None, "resets_at": ""}
        self.chat = deque(maxlen=chat_max)
        self.messages = deque(maxlen=60)      # V-013: whispers both ways, for the talk window

    def _task(self, ev):
        key = ev.task or ev.data.get("title")
        if not key or key.startswith("idea:") or not self._mine(key):       # D-345: 'idea:' ids are Idea approvals, never quests
            return None
        t = self.tasks.setdefault(key, {"id": key, "title": ev.data.get("title") or key, "status": "new", "reason": "", "objectives": [], "agent": None, "route": None, "approved": self._ok(key), "sent_back": [],
                                                "approved_by": self._who(key), "approved_why": self.rec.get(key, {}).get("why", ""), "vetoed": bool(self.rec.get(key, {}).get("vetoed"))})
        if ev.data.get("title"):
            t["title"] = ev.data["title"]
        for k in ("details", "why", "size", "risk"):       # D-360: plain-language fields from a seeded quest
            if ev.data.get(k):
                t[k] = ev.data[k]
        if ev.type == "task_created":
            t["needs_plain"] = bool(ev.data.get("needs_plain"))
        if ev.agent and not t["agent"]:
            t["agent"] = ev.agent
        return t

    def _mine(self, key):
        """D-382: a quest id made for another project (`<slug>:q:...`) or another project's verify task is not this project's."""
        if self.scope is None:
            return True
        m = _QUEST.match(key)
        return key == self.scope[1] if key.startswith("verify-") else (m.group(1) == self.scope[0] if m else True)

    def _ok(self, key):
        r = self.rec.get(key)
        return bool(r) and not r.get("vetoed")

    def _who(self, key):
        w = self.rec.get(key, {}).get("who") if self._ok(key) else None
        return w or ("you" if self._ok(key) else None)

    def _agent(self, ev):
        if not ev.agent:
            return None
        if ev.agent not in self.agents and ev.agent in self.departed and ev.type != "agent_left":     # back again (resumed): it keeps where it ran
            gone = self.departed.pop(ev.agent)
            self.agents[ev.agent] = {"id": ev.agent, "role": gone.get("role", ""), "state": "idle", "activity": "", "task": None, "actions": 0,
                                     "tools": {}, "passed": 0, "failed": 0, "back": ev.ts, **{k: gone[k] for k in PLACE if gone.get(k)}}
        return self.agents.setdefault(ev.agent, {"id": ev.agent, "role": "", "state": "idle", "activity": "", "task": None, "actions": 0,
                                                 "tools": {}, "passed": 0, "failed": 0})

    def apply(self, ev, source="feed"):
        rule = self.m.get(ev.type, {})
        task, agent, d = self._task(ev), self._agent(ev), ev.data
        if agent is not None:
            agent["state"] = rule.get("agent_state", agent["state"])
            agent["seen"] = ev.ts      # last sign of life: the workshop shows long-quiet helpers as ghosts
            # V-026: "needs you" (a permission prompt, a question, waiting for your input) and "stunned" (the service refused the turn)
            # last until the helper does something again
            if ev.type == "agent_waiting":
                agent["needs"], agent["needs_note"] = d.get("reason", "input"), d.get("note", "")
            elif ev.type == "agent_stunned":
                agent["stunned"] = d.get("reason", "unknown")
            elif ev.type == "agent_rested" and d.get("phase") == "done":
                agent["mana"] = 100             # compaction: memory freed (the statusline / transcript corrects it on the next reading)
            elif ev.type == "todo_update":       # V-028: Claude's own to-do list, newest last
                todo = [x for x in agent.get("todo", []) if x["text"] != d["text"]] + [{"text": d["text"], "done": d.get("done") == "yes"}]
                agent["todo"] = todo[-8:]
            elif ev.type == "tool_failed":
                agent["failed_step"] = ev.ts
            elif ev.type in ("agent_working", "tool_use", "agent_started", "agent_idle"):
                agent.pop("needs", None)
                agent.pop("needs_note", None)
                agent.pop("stunned", None)
            agent["role"] = d.get("role") or agent["role"]
            if ev.type in ("agent_working", "tool_use"):
                agent["activity"] = d.get("activity") or " ".join(x for x in (d.get("tool"), d.get("file") or d.get("command")) if x)
                agent["actions"] += 1
                agent["task"] = ev.task or agent["task"]
            if ev.type == "agent_place":
                agent.update({k: d[k] for k in PLACE if d.get(k)})
                if not d.get("root"):           # moved out of a checkout: forget the old one
                    for k in ("root", "worktree", "branch"):
                        agent.pop(k, None)
                if d.get("root"):
                    self.roots.add(_norm_path(d["root"]))
                self.departed.pop(ev.agent, None)
            if ev.type == "tool_use" and d.get("file") and d["tool"] in self.file_tools:
                self.editors.setdefault(self._checkout_key(agent, d["file"]), {})[ev.agent] = ev.ts      # V-028: who changed this file, and when (same-file warning)
                self.files = [f for f in self.files if f["file"] != d["file"]]
                self.files.insert(0, {"file": d["file"], "tool": d["tool"], "agent": ev.agent, "ts": ev.ts})
                del self.files[self.files_max:]
            if ev.type == "tool_use":
                agent["tools"][d["tool"]] = agent["tools"].get(d["tool"], 0) + 1
            if agent["state"] == "idle":
                agent["activity"] = ""
        if ev.type == "agent_left" and ev.agent in self.agents:      # the session ended: the helper goes home (back when it does something)
            gone = self.agents.pop(ev.agent)
            self.departed[ev.agent] = {"id": ev.agent, "role": gone.get("role", ""), "left": ev.ts, **{k: gone[k] for k in PLACE if gone.get(k)}}
            while len(self.departed) > 8:
                self.departed.pop(next(iter(self.departed)))
            agent = None
        if task is not None:
            new = rule.get("task_status")
            if new and (not rule.get("only_from") or task["status"] in rule["only_from"]):
                task["status"] = new
                if new in ("complete", "failed", "blocked"):
                    task["reason"] = d.get("reason", "")
                owner = self.agents.get(task["agent"]) if task["agent"] else None
                if owner and new in ("complete", "failed"):
                    owner["passed" if new == "complete" else "failed"] += 1
            if ev.type == "task_ready":
                task["sent_back"] = []       # a new hand-in starts clean (D-324)
            if ev.type == "sent_back":
                task["sent_back"] = (task["sent_back"] + [{"check": d["check"], "evidence": d["evidence"]}])[-8:]
            if ev.type in ("plan_approved", "quest_auto_approved") and self._ok(task["id"]):       # D-380: only if the approvals file says so
                task.update(approved=True, vetoed=False, approved_by=self._who(task["id"]), approved_why=self.rec[task["id"]].get("why", ""))
            if ev.type == "done_elsewhere":
                task["done_elsewhere"] = d.get("note", "")       # shown in the quest log and put in the next helper's text so work is not repeated
            if ev.type == "explain_requested":
                task["explain_pending"] = True
            if ev.type == "explain_answered":
                task.update(explain_pending=False, answer=d.get("text", ""))
            if ev.type == "change_requested":
                task["change_pending"] = True
            if ev.type == "change_answered":
                task["change_pending"] = False
            if ev.type == "quest_vetoed":
                task.update(approved=False, vetoed=True, approved_by=None)
            if ev.type == "route_chosen":
                task["route"] = {"route": d["route"], "why": d["why"]}
            if ev.type == "check_started":       # V-043: a re-run starts the objective again (verdict None = running; the inn sends its helper to the anvil)
                o = next((o for o in task["objectives"] if o["check"] == d["check"]), None)
                if o is None:
                    task["objectives"].append({"check": d["check"], "verdict": None})
                else:
                    o["verdict"] = None
            if ev.type == "check_finished":
                o = next((o for o in task["objectives"] if o["check"] == d["check"]), None)
                if o is None:
                    o = {"check": d["check"], "verdict": None}
                    task["objectives"].append(o)
                o["verdict"] = d["verdict"]
        if ev.type in ("fix_proposed", "fix_checked") and task is not None and ev.agent:      # V-045: a helper's proposal for a competing quest
            mine = self.props.setdefault(task["id"], {})
            if ev.type == "fix_proposed":
                mine[ev.agent] = contests.proposal_of(ev)
            elif ev.agent in mine and d.get("branch") in (None, mine[ev.agent]["branch"]):
                mine[ev.agent].update(verdict=d["verdict"], reason=d.get("reason", ""))
        if ev.type == "quest_claimed" and task is not None and ev.agent:
            task["agent"] = ev.agent
        if ev.type in ("message_sent", "message_answered"):
            self.messages.append({"ts": ev.ts, "to": d.get("to") if ev.type == "message_sent" else "you", "from": "you" if ev.type == "message_sent" else ev.agent, "text": d.get("text", "")})
        if ev.type == "lease_acquired":
            self.leases.add(d["resource"])
            if isinstance(d.get("expires_at"), (int, float)):
                self.lease_exp[d["resource"]] = d["expires_at"]
        elif ev.type == "lease_expired":
            self.leases.discard(d["resource"])
            self.lease_exp.pop(d["resource"], None)
        elif ev.type == "usage_update":
            self.usage.update(used_pct=d["used_pct"], resets_at=d.get("resets_at", ""))
            if agent is not None and "ctx_pct" not in agent:      # per-helper mana from its own transcript, unless Claude Code reported the real figure
                agent["mana"] = max(0, min(100, 100 - int(d["used_pct"] or 0)))
        elif ev.type == "session_stats":      # V-030: Claude Code's own numbers (statusline bridge)
            if agent is not None:
                agent.update({k: d[k] for k in _STATS if k in d})
                if "tokens_out" in d:         # tokens Claude wrote so far (from its transcript): samples for the damage meter's tokens per minute
                    tok = [x for x in agent.get("tok", []) if x[1] <= d["tokens_out"]]
                    agent["tok"] = (tok + [[ev.ts, d["tokens_out"]]])[-TOKEN_SAMPLES:]
                    agent["tokens_out"] = d["tokens_out"]
                if "ctx_pct" in d:
                    agent["mana"] = max(0, min(100, 100 - int(round(d["ctx_pct"]))))
            for w in ("five_hour", "seven_day"):       # plan limits are account-wide: the newest report wins
                if w + "_pct" in d:
                    self.usage[w] = {"used_pct": d[w + "_pct"], "resets_at": d.get(w + "_resets"), "ts": ev.ts}
        line = {"id": "%s%d" % (source[0], ev.id), "n": ev.id if source == "feed" else 0, "ts": ev.ts, "type": ev.type, "channel": rule.get("channel", "system"),
                "text": render(ev, self.t), "toast": rule.get("toast"), "title": (task or {}).get("title", ""),
                "agent": ev.agent or (task or {}).get("agent") or ""}     # whose line it is: the chat's per-helper filter (V-034)
        if ev.type not in QUIET:
            self.chat.append(line)
        return line

    def _checkout_key(self, agent, path):
        """V-038: (checkout root, path inside it) for a changed file. The root is the helper's own checkout when the file is in it,
        else the longest checkout root any helper reported that holds the file, else '' (the path as given)."""
        f = _norm_path(path)
        mine = _norm_path(agent.get("root")) if agent else ""
        absolute = f.startswith("/") or bool(re.match(r"^[a-z]:/", f, re.I))
        if mine and not absolute:
            return (mine, f[2:] if f.startswith("./") else f)
        for r in ([mine] if mine else []) + sorted(self.roots, key=len, reverse=True):
            if r and f.startswith(r + "/"):
                return (r, f[len(r) + 1:])
        return ("", f)

    def snapshot(self):
        for t in self.tasks.values():      # D-313: with approval required, finished work counts only once the owner approved the quest
            t["counts"] = t["status"] == "complete" and (t["approved"] or not self.require_approval)
        done = sum(1 for t in self.tasks.values() if t["counts"])
        # V-028: two helpers changed the same file within 15 minutes (they share one checkout): a warning, not a lock
        # V-038: only inside ONE checkout: the same path in two git worktrees is two different files
        clashes = []
        for (root, path), by in list(self.editors.items())[-200:]:
            who = sorted(a for a, ts in by.items() if a and _age_s(ts) < 900)
            if len(who) > 1:
                clashes.append({"file": path, "agents": who, "root": root})
        for a in self.agents.values():     # handed in and waiting for you: the workshop sends them to the quest board
            t = self.tasks.get(a["task"]) if a.get("task") else None
            a["waiting"] = bool(t and t["status"] == "ready")
            a.pop("contest", None)
        for qid, t in self.tasks.items():      # V-045: competing fixes, from the owner's file plus the proposals sent since it opened
            rec = self.contests.get(qid)
            if not rec:
                t.pop("contest", None)
                continue
            props = sorted((p for p in self.props.get(qid, {}).values() if p["ts"] >= rec.get("ts", 0)), key=lambda p: p["ts"])
            t["contest"] = {"tries": rec.get("tries", 2), "base": rec.get("base", ""), "open": contests.is_open(rec), "winner": rec.get("winner"),
                            "closed": bool(rec.get("closed")), "ts": rec.get("ts", 0), "proposals": [dict(p, files=list(p["files"])) for p in props]}
            if contests.is_open(rec):        # V-047: the helper on the quest competes too (before it sends a fix): the inn seats them all at one table
                for a in [p["agent"] for p in props] + [t.get("agent")]:
                    if a in self.agents:
                        self.agents[a]["contest"] = qid
        return {"tasks": list(self.tasks.values()), "agents": list(self.agents.values()), "leases": sorted(self.leases), "lease_expires": sorted(self.lease_exp.values()), "usage": self.usage,
                "chat": list(self.chat), "files": list(self.files), "counts": {"done": done, "total": len(self.tasks)}, "messages": list(self.messages), "clashes": clashes,
                "departed": [dict(v) for v in self.departed.values() if v["id"] not in self.agents]}


class AppState:
    """One project's live state: database history (if the folder has one) plus the feed from its start."""

    def __init__(self, cfg, feed_path, project=None):
        self.cfg, self.feed, self.project, self.lock = cfg, Feed(feed_path), project, threading.Lock()
        self.problem, self._sig = "", None
        self.scope = self._scope()
        m = cfg.get("map")      # zone touches (D-261): only for a git project, so tests and non-git folders never write the activity file
        self.tracker = zones.Tracker(project, m, zones.Activity(m["activity_path"], m), lambda root: zones_api.same_repo({"root": root}, project)) if m and project and (Path(project) / ".git").exists() else None
        self.load()

    def _scope(self):
        """D-382: (this project's quest-id prefix, its verify task id), or None with no project open. A project that is not set up owns no quests."""
        if not self.project:
            return None
        a = self.cfg["app"]
        reg = load_registry(os.environ.get("VANILLA_PROJECTS") or a["projects_path"])
        e = next((v for k, v in reg.items() if isinstance(v, dict) and _norm(k) == _norm(self.project)), None)
        return (slug_of(e), verify_task(e)) if e else ("", "")

    def load(self):
        c = self.cfg
        self.f = Folder(c["strings"]["eli5"], c["metaphors"], c["app"]["chat_max_lines"], c.get("panels", {}).get("backpack", {}).get("options", {}).get("max_files", 40),
                        c["app"].get("plan_approval_required", False), approvals.load(c["app"].get("approvals_path"), self.project), self.scope,
                        contests.load(contests.path_for(c["app"])))
        self.cursor = 0
        self.problem = ""
        db = layout(self.project)["db"] if self.project else None
        if db is not None and Path(db).is_file():
            try:
                self._seed(db)
            except Exception as e:   # an unreadable/older database must not blank the window; say so in plain words
                self.problem = "The project's database could not be read (%s). Showing only live events." % e
        self.refresh()

    def _seed(self, db):
        st = derive.current_state(db)
        for t in st["tasks"]:
            status = _DB_STATUS.get(t["verification"]) or ("blocked" if t["execution"] == "BLOCKED" else "ready" if t["execution"] == "QUEUED" else "new")
            self.f.tasks[t["task_id"]] = {"id": t["task_id"], "title": t["title"], "status": status, "reason": t["blocked_reason"] or "", "objectives": [], "agent": None, "route": None, "approved": self.f._ok(t["task_id"]), "sent_back": []}
        for w in st["workers"]:
            self.f.agents[w["worker_id"]] = {"id": w["worker_id"], "role": "", "state": "idle", "activity": "", "task": None, "actions": 0, "tools": {}, "passed": 0, "failed": 0}
        self.f.leases.update(l["resource"] for l in st["leases"])
        self.f.lease_exp.update({l["resource"]: l["expires_at"] for l in st["leases"] if l.get("expires_at")})
        for ev in derive.derive_events(db):
            self.f.apply(ev, source="db")

    def refresh(self):
        """Fold feed events after the cursor. Returns the new chat lines (the caller decides whether to push)."""
        with self.lock:
            try:
                st = self.feed.path.stat()
                sig = (st.st_mtime_ns, st.st_size)
            except OSError:
                sig = None
            if sig is not None and sig == self._sig:
                return []
            self._sig = sig
            self.f.rec = approvals.load(self.cfg["app"].get("approvals_path"), self.project)       # D-380: the file is the truth, read fresh before any approval line is folded
            self.f.contests = contests.load(contests.path_for(self.cfg["app"]))       # V-045: the same for competitions and their winners
            new = []
            for ev in self.feed.read_since(self.cursor, 100000):
                new.append(self.f.apply(ev))
                if self.tracker and ev.type == "tool_use":
                    self.tracker.on_tool(ev, self.f.agents.get(ev.agent, {}).get("root"))      # V-047: its own worktree counts as the project
            if self.tracker:
                self.tracker.act.flush()
            if new:
                self.cursor = max(self.cursor, int(new[-1]["id"][1:]))
            return new

    def approve(self, task_id, who="you"):
        """Mark a quest's plan approved (D-310): saved in the approvals file, then announced in the feed so the audit log and every window show it."""
        self.refresh()
        with self.lock:
            t = self.f.tasks.get(task_id)
        if t is None:
            raise ValueError("there is no quest called %s" % task_id)
        a = self.cfg["app"]
        if approvals.add(a["approvals_path"], self.project, task_id, t["title"], time.time(), a["approvals_max_tasks"], a["approvals_max_projects"], who=who, why="approved by " + who):
            self.feed.append("plan_approved", task=task_id, data={"title": t["title"], "who": who})
        self.refresh()

    def mark_done_elsewhere(self, task_id, note="finished in a normal Claude Code session", who="you"):
        """A normal session finished this quest's work: record it so the quest shows done and nobody repeats it."""
        self.refresh()
        with self.lock:
            t = self.f.tasks.get(task_id)
        if t is None:
            raise ValueError("there is no quest called %s" % task_id)
        self.feed.append("done_elsewhere", task=task_id, data={"title": t["title"], "who": who, "note": note})
        self.refresh()

    def snapshot(self):
        with self.lock:
            s = self.f.snapshot()
            s.update(cursor=self.cursor, project={"name": Path(self.project).name, "path": str(self.project)} if self.project else None, problem=self.problem)
            return s
