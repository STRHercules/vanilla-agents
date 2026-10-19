"""Vanilla Agents as an MCP server (stdio, JSON-RPC 2.0, standard library only), so Claude can use the program as tools:
Claude Code (`claude mcp add`), the Claude desktop app (claude_desktop_config.json), and cloud sessions linked to this PC.

What an agent may do here: read status and quests, start a quest, report progress, hand a quest in, read the owner's
Change / Investigate requests and answer them, say it is done for now. What it may NOT do: approve, verify, pause,
mark work done, open a competition or pick the winning fix - those stay the owner's (buttons in the window, or scripts/decide.py on the owner's word), so an agent can
never sign off its own work (D-380). Everything an agent sends goes into the same event feed the hooks write."""
import json
import os
import re
import sys
import time

from .app.config import load_all
from .events.feed import Feed
from .events.hooks import pending_full

PROTOCOL = "2025-06-18"
INSTRUCTIONS = """Vanilla Agents shows your work to the owner as a WoW-style inn: you are a hero at a desk, tasks are quests.
Working agreement:
1. At the start of a task call list_quests; if the work is not already a quest, call start_quest with a plain-language title (no jargon) and a one-line why.
2. While working, call report_progress with a short plain line now and then (what you are doing, for a non-coder).
3. When the work is finished, call quest_ready with the quest id, and put the quest id in your commit message.
4. Call pending_requests when you start and after each step: the owner may have asked you to change something, explain it more, or whispered to you; do it, then call answer_request or reply.
5. Never claim a quest is verified or approved: only the owner and the checker can do that.
6. If the owner plays in a party with other people (party_plan says so), work only on the plan items marked ours; suggest new ones with party_propose.
7. A quest with "competition" in list_quests lets several helpers try: do it on your own branch (best in your own git worktree), commit, then call propose_fix with a short summary and your branch or worktree folder. The owner compares the fixes and picks one; if yours is picked you are told to merge it."""

_SIZES, _RISKS = ("small", "medium", "big"), ("low", "medium", "high")


def _text(obj):
    return {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=1, ensure_ascii=False)}]}


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "quest"


class Tools:
    def __init__(self, cfg=None, feed_path=None, clock=time.time):
        self.cfg = cfg or load_all()
        from .app.server import default_feed
        self.feed = Feed(feed_path or default_feed(self.cfg["app"]))
        self.clock = clock

    # ---- reading ----
    def _snapshot(self, project=None):
        from .app.state import AppState
        st = AppState(self.cfg, str(self.feed.path), self._repo(project))
        return st.snapshot()

    def _repo(self, project):
        if not project:
            return None
        from .onboard import setup
        _, e = setup.find(setup.load_registry(os.environ.get("VANILLA_PROJECTS") or self.cfg["app"]["projects_path"]), project)
        if not e:
            raise ValueError("%s is not a set-up project (add it with Esc > Add project, or scripts/onboard.py)" % project)
        return e["repo"]

    def status(self, project=None):
        s = self._snapshot(project)
        return {"project": s.get("project"), "agents": [{"id": a["id"], "state": a["state"], "doing": a["activity"], "quest": a["task"], "actions": a["actions"]} for a in s["agents"]],
                "quests": {"total": s["counts"]["total"], "done": s["counts"]["done"], "by_status": _count(t["status"] for t in s["tasks"])},
                "usage_pct": s["usage"].get("used_pct"), "problem": s.get("problem") or None}

    def list_quests(self, project=None, status=None):
        s = self._snapshot(project)
        out = []
        for t in s["tasks"]:
            if status and t["status"] != status:
                continue
            out.append({"id": t["id"], "title": t["title"], "status": t["status"], "approved": t["approved"], "agent": t["agent"],
                        "objectives": [{"check": o.get("check"), "verdict": o.get("verdict")} for o in t["objectives"]],
                        **{k: t[k] for k in ("why", "size", "risk", "reason", "answer") if t.get(k)},
                        **({"sent_back": t["sent_back"]} if t.get("sent_back") else {}),     # the checker's own finding: what to fix
                        **({"competition": _competition(t["contest"])} if t.get("contest") else {})})
        return out

    def propose_fix(self, quest, summary, branch=None, worktree=None, agent=None):
        """V-045: a proposal for a competing quest. Proposals only: picking the winner is the owner's (no tool can)."""
        from .app import contests
        for name, v in (("branch", branch), ("worktree", worktree), ("summary", summary)):
            if v is not None and not isinstance(v, str):
                raise ValueError("%s must be text" % name)
        r = contests.propose(self.feed, contests.path_for(self.cfg["app"]), quest, agent or "claude", summary, branch or None, worktree or None)
        return {**r, "note": "Sent. The owner compares the fixes and picks one; you will get a whisper either way. Do not merge until you are told."}

    def pending_requests(self, agent=None):
        from .app.social import pending_messages
        rows = [{"kind": k, "quest": q, "title": title, "note": note} for k, q, title, note in pending_full(self.feed)]
        rows += [{"kind": "message", "to": to, "text": text, "note": "answer with reply"} for _eid, to, text in pending_messages(self.feed, agent or "claude")]
        return rows

    def reply(self, text, agent=None):
        from .app.social import reply
        reply(self.feed, agent or "claude", text)
        return {"ok": True}

    def claim_quest(self, quest, agent=None):
        self.feed.append("quest_claimed", task=quest, agent=agent or "claude", data={"title": self._title(quest)})
        return {"ok": True, "note": "The quest is yours on the board. It still needs the owner's approval before the work counts."}

    # ---- writing (feed events only) ----
    def start_quest(self, title, why=None, details=None, size=None, risk=None, project=None, agent=None):
        if not title or not title.strip():
            raise ValueError("give the quest a plain-language title")
        if size and size not in _SIZES or risk and risk not in _RISKS:
            raise ValueError("size is small/medium/big and risk is low/medium/high")
        prefix = ""
        if project:
            from .app.verify_job import slug_of
            from .onboard import setup
            _, e = setup.find(setup.load_registry(os.environ.get("VANILLA_PROJECTS") or self.cfg["app"]["projects_path"]), project)
            if not e:
                raise ValueError("%s is not a set-up project" % project)
            prefix = slug_of(e) + ":q:"
        qid = prefix + _slug(title) + "-" + format(int(self.clock() * 1000) % 0xFFFFF, "05x")
        data = {"title": title.strip()[:200]}
        data.update({k: v.strip()[:600] for k, v in (("why", why), ("details", details)) if v})
        data.update({k: v for k, v in (("size", size), ("risk", risk)) if v})
        self.feed.append("task_created", task=qid, agent=agent or None, data=data)
        return {"quest": qid, "note": "Added to the quest log. The owner decides whether it may go ahead; it is not approved yet."}

    def report_progress(self, activity, quest=None, agent=None):
        a = agent or "claude"
        if not any(ev.agent == a for ev in self.feed.read_since(max(0, self.feed.last_id() - 500), 500)):
            self.feed.append("agent_started", task=quest, agent=a, data={})
        self.feed.append("agent_working", task=quest, agent=a, data={"activity": activity.strip()[:200]})
        return {"ok": True}

    def quest_ready(self, quest, agent=None):
        title = self._title(quest)
        self.feed.append("task_ready", task=quest, agent=agent or None, data={"title": title})
        return {"ok": True, "note": "Handed in. The checker runs when the owner presses Verify (or on its schedule); only a passing check completes it."}

    def answer_request(self, quest, text, changed=False):
        title = self._title(quest)
        if changed:
            self.feed.append("change_answered", task=quest, data={"title": title, **({"text": text.strip()[:1500]} if text else {})})
        else:
            if not text or not text.strip():
                raise ValueError("an explanation needs text")
            self.feed.append("explain_answered", task=quest, data={"title": title, "text": text.strip()[:1500]})
        return {"ok": True}

    # ---- party (several people + their Claudes, vanilla_agents/party): read the shared plan, suggest items; agreeing stays with people ----
    def _party_files(self):
        from .app import settings as app_settings
        work = os.path.dirname(os.path.abspath(app_settings.settings_path(self.cfg["app"])))
        def load(name):
            try:
                with open(os.path.join(work, name), encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                return None
        return load("party-member.json"), load("party-state.json")

    def party_plan(self):
        member, cache = self._party_files()
        if not member or not member.get("key"):
            return {"party": None, "note": "The owner is not in a party. Nothing to split."}
        st = (cache or {}).get("state") or {}
        me = st.get("me") or member.get("member")
        names = {m["id"]: m["name"] for m in st.get("members", [])}
        items = [{"item": i["id"], "title": i["title"], "why": i.get("why", ""), "size": i.get("size"), "status": i.get("status"),
                  "owner": names.get(i.get("owner"), None), "ours": i.get("owner") == me, "quest": member.get("quests", {}).get(i["id"])} for i in st.get("items", [])]
        return {"party": st.get("party"), "plan_version": st.get("version"), "agreed_and_split": st.get("locked", False),
                "members": [{"name": m["name"], "agrees": m["agrees"], "helpers": len(m["agents"])} for m in st.get("members", [])], "items": items,
                "note": "Work only on items marked ours; each one is (or becomes) a quest in list_quests once the plan is agreed. Agreeing is for the people."}

    def party_propose(self, title, why="", size="medium", agent=None):
        member, _ = self._party_files()
        if not member or not member.get("key"):
            raise ValueError("the owner is not in a party")
        from .party.client import PartyClient, PartyError
        c = PartyClient.__new__(PartyClient)
        c.m = member
        try:
            it = c._call("/party/propose", {"title": title, "why": why, "size": size, "by_agent": agent or "Claude"})
        except PartyError as e:
            raise ValueError(str(e))
        return {"item": it.get("id"), "note": "Proposed to the party. Everyone has to agree to the plan again before it is split."}

    def done_for_now(self, note=None, agent=None):
        self.feed.append("agent_idle", agent=agent or "claude", data={"note": note.strip()[:200]} if note else {})
        return {"ok": True}

    def _title(self, quest):
        title = quest
        for ev in self.feed.read_since(0, 100000):
            if ev.task == quest and ev.data.get("title") and ev.type not in ("explain_answered", "change_answered"):
                title = ev.data["title"]
        return title


def _competition(c):
    return {"open": c["open"], "tries": c["tries"], "base": c["base"], "proposals": len(c["proposals"]), "by": [p["agent"] for p in c["proposals"]],
            **({"winner": c["winner"]} if c.get("winner") else {})}


def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


S, N = {"type": "string"}, {"type": "boolean"}
TOOLS = [
    ("status", "What the program sees right now: helpers, what each is doing, quest counts, usage.", {"project": S}, []),
    ("list_quests", "The quests (tasks) in the quest log with status, approval and check results. status = new/progress/ready/complete/failed/blocked.", {"project": S, "status": S}, []),
    ("start_quest", "Add a quest for the work you are about to do. Plain-language title and why; size small/medium/big, risk low/medium/high. Returns the quest id. It still needs the owner's approval.",
     {"title": S, "why": S, "details": S, "size": S, "risk": S, "project": S, "agent": S}, ["title"]),
    ("report_progress", "Tell the owner what you are doing now, in one short plain line. Shows on your hero's screen and in the chat.", {"activity": S, "quest": S, "agent": S}, ["activity"]),
    ("quest_ready", "Hand a finished quest in (ready for the checker). Put the quest id in your commit message too.", {"quest": S, "agent": S}, ["quest"]),
    ("pending_requests", "The owner's open Change / Investigate (explain) requests and whispers (kind message). Do them, then answer_request or reply.", {"agent": S}, []),
    ("reply", "Answer the owner's whisper (kind message from pending_requests) in a few plain words; it shows as your hero's reply.", {"text": S, "agent": S}, ["text"]),
    ("claim_quest", "Take a quest from the board (an unassigned one from list_quests) so the owner sees who is on it.", {"quest": S, "agent": S}, ["quest"]),
    ("answer_request", "Answer an Investigate request with a plain explanation (max 4 short sentences), or with changed=true record that the requested change is made.",
     {"quest": S, "text": S, "changed": N}, ["quest"]),
    ("done_for_now", "Your hero stands up: you finished or are waiting for the owner.", {"note": S, "agent": S}, []),
    ("propose_fix", "When several helpers may try a quest (list_quests shows competition): send your fix as a proposal: a short plain summary and your branch (or your git worktree folder). The owner picks the winner; you cannot.",
     {"quest": S, "summary": S, "branch": S, "worktree": S, "agent": S}, ["quest", "summary"]),
    ("party_plan", "When the owner plays in a party with other people and their Claudes: the shared plan, who agrees, and which items are ours (work only on those).", {}, []),
    ("party_propose", "Suggest an item for the party's shared plan (plain title, why, size small/medium/big). The people agree to the plan; you cannot.",
     {"title": S, "why": S, "size": S, "agent": S}, ["title"]),
]


def _version():
    from .app.diagnostics import version
    return version()


def tool_list():
    ro = {"status", "list_quests", "pending_requests", "party_plan"}       # read-only: Claude Code may run these without asking
    return [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r},
             "annotations": {"readOnlyHint": n in ro, "destructiveHint": False, "openWorldHint": n == "party_propose"}} for n, d, p, r in TOOLS]


def handle(tools, msg):
    """One JSON-RPC message -> response dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    try:
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "vanilla-agents", "version": _version()}, "instructions": INSTRUCTIONS}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": tool_list()}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in {t[0] for t in TOOLS}:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool %s" % name}}
            try:
                res = _text(getattr(tools, name)(**args))
            except (ValueError, TypeError, OSError) as e:      # a bad call is the agent's to fix: say so as a tool result
                res = dict(_text("Not done: %s" % e), isError=True)
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}
    except Exception as e:          # never crash the session; report and carry on
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def serve(stdin=sys.stdin, stdout=sys.stdout, tools=None):
    tools = tools or Tools()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            out = [r for r in (handle(tools, m) for m in msg) if r] if isinstance(msg, list) else handle(tools, msg)
        if out:
            stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
