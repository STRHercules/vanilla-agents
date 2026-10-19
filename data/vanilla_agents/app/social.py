"""Owner <-> helper talk and the extras the window asks the server for (V-013):
whispers (to one helper or everyone), the hearthstone (pause the checker and tell everyone to stop), owner-made quests,
a file diff for the Inspect window, and a replay snapshot of the feed up to an event id. Everything that changes state is an
event in the feed, so hooks, MCP sessions and every window see the same thing."""
import os
import re
import subprocess
import time
from pathlib import Path

EVERYONE = "everyone"
MAX_TEXT = 1500
HEARTH_TEXT = "Hearthstone: please stop where you are, save your work and wait for the owner before doing more."
_GIT = ["git", "-c", "core.fsmonitor=false", "-c", "diff.external=", "-c", "core.hooksPath=" + os.devnull]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "quest"


def new_quest_id(title, prefix="", clock=time.time):
    return prefix + slug(title) + "-" + format(int(clock() * 1000) % 0xFFFFF, "05x")


def project_prefix(cfg, project):
    """'<slug>:q:' for a set-up project (so the window of that project shows the quest), '' otherwise."""
    if not project:
        return ""
    from ..onboard import setup
    from .verify_job import slug_of
    _, e = setup.find(setup.load_registry(os.environ.get("VANILLA_PROJECTS") or cfg["app"]["projects_path"]), str(project))
    return slug_of(e) + ":q:" if e else ""


def whisper(feed, to, text, who="you"):
    to = (to or "").strip() or EVERYONE
    text = (text or "").strip()
    if not text:
        raise ValueError("say something first")
    return feed.append("message_sent", agent=None if to == EVERYONE else to, data={"to": to, "text": text[:MAX_TEXT], "who": who})


def pending_messages(feed, agent=None):
    """Whispers still waiting for `agent` (to it or to everyone, sent after its last reply); all open ones when agent is None.
    [(event id, to, text)]"""
    last_reply, sent = {}, []
    for ev in feed.read_since(0, 100000):
        if ev.type == "message_answered" and ev.agent:
            last_reply[ev.agent] = ev.id
        elif ev.type == "message_sent":
            sent.append((ev.id, ev.data.get("to", EVERYONE), ev.data.get("text", "")))
    out = []
    for eid, to, text in sent:
        if agent is None:
            replied = last_reply.get(to, 0) > eid if to != EVERYONE else False
        else:
            if to not in (agent, EVERYONE):
                continue
            replied = last_reply.get(agent, 0) > eid
        if not replied:
            out.append((eid, to, text))
    return out[-10:]


def reply(feed, agent, text):
    text = (text or "").strip()
    if not text:
        raise ValueError("a reply needs text")
    return feed.append("message_answered", agent=agent or "claude", data={"text": text[:MAX_TEXT]})


def owner_quest(feed, cfg, project, title, why=""):
    title = (title or "").strip()
    if not title:
        raise ValueError("give the quest a title")
    qid = new_quest_id(title, project_prefix(cfg, project))
    data = {"title": title[:200]}
    if why and why.strip():
        data["why"] = why.strip()[:600]
    feed.append("task_created", task=qid, data=data)
    return qid


def diff(project, file=None, timeout=20, max_bytes=200000):
    """Changed files of the open project (no file) or one file's diff against the last commit. Read-only git; paths stay inside the repo."""
    if not project or not (Path(project) / ".git").exists():
        return {"error": "Open a project that is a git folder to inspect its changes."}
    repo = Path(project).resolve()
    if file is None:
        r = subprocess.run(_GIT + ["-C", str(repo), "status", "--porcelain=v1", "-uall"], capture_output=True, timeout=timeout)
        files = []
        for ln in r.stdout.decode("utf-8", "replace").splitlines():
            if len(ln) > 3:
                files.append({"status": ln[:2].strip() or "?", "file": ln[3:].split(" -> ")[-1]})
        n = subprocess.run(_GIT + ["-C", str(repo), "diff", "--numstat", "--no-ext-diff", "HEAD"], capture_output=True, timeout=timeout)
        stats = {}
        for ln in n.stdout.decode("utf-8", "replace").splitlines():
            parts = ln.split("\t")
            if len(parts) == 3:
                stats[parts[2]] = {"added": parts[0], "removed": parts[1]}
        for f in files:
            f.update(stats.get(f["file"], {}))
        return {"files": files[:200]}
    target = (repo / file).resolve()
    if repo not in target.parents and target != repo:
        return {"error": "That file is outside the project."}
    rel = target.relative_to(repo).as_posix()
    r = subprocess.run(_GIT + ["-C", str(repo), "diff", "--no-ext-diff", "--no-color", "HEAD", "--", rel], capture_output=True, timeout=timeout)
    text = r.stdout[:max_bytes].decode("utf-8", "replace")
    if not text and target.is_file():        # a new file git does not track yet: show it whole
        text = "".join("+" + ln + "\n" for ln in target.read_text(encoding="utf-8", errors="replace")[:max_bytes].splitlines())
    return {"file": rel, "diff": text, "truncated": len(r.stdout) > max_bytes}


def history(cfg, feed, project, until=None):
    """The window's snapshot as it was right after event `until` (None = now), plus the feed's id range, for the replay slider."""
    from .state import Folder, AppState
    st = AppState.__new__(AppState)        # the same folding as the live state, but stop at `until`
    st.cfg, st.feed, st.project = cfg, feed, project
    import threading
    st.lock, st.problem, st._sig, st.tracker = threading.Lock(), "", None, None
    st.scope = AppState._scope(st)
    c = cfg
    from . import approvals
    st.f = Folder(c["strings"]["eli5"], c["metaphors"], c["app"]["chat_max_lines"], c.get("panels", {}).get("backpack", {}).get("options", {}).get("max_files", 40),
                  c["app"].get("plan_approval_required", False), approvals.load(c["app"].get("approvals_path"), project), st.scope)
    first = last = 0
    for ev in feed.read_since(0, 1000000):
        first = first or ev.id
        last = ev.id
        if until is None or ev.id <= until:
            st.f.apply(ev)
    snap = st.f.snapshot()
    snap["range"] = {"first": first, "last": last, "at": min(until, last) if until is not None else last}
    return snap


# ---- daily quests (app.json "daily", off by default): owner-written quests the window posts once a day at a set time --------------------

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def due_dailies(daily, made, now):
    """Pure: the daily quests to post now. daily = app.json "daily"; made = {quest index: "YYYY-MM-DD" last posted}; now = a datetime.
    A quest is due on its days (none listed = every day) once the clock passes its "at" (HH:MM), and only once that day."""
    if not daily or not daily.get("enabled"):
        return []
    today, out = now.strftime("%Y-%m-%d"), []
    for i, q in enumerate(daily.get("quests") or []):
        days = [d.lower()[:3] for d in q.get("days") or []]
        if days and DAYS[now.weekday()] not in days:
            continue
        try:
            hh, mm = (int(x) for x in (q.get("at") or "00:00").split(":"))
        except ValueError:
            continue
        if (now.hour, now.minute) >= (hh, mm) and made.get(str(i)) != today and (q.get("title") or "").strip():
            out.append((i, q))
    return out


def post_dailies(feed, cfg, project, made_path, now=None):
    """Post the due dailies as owner quests; remembers what it posted in made_path (JSON). Returns the new quest ids."""
    import datetime
    import json
    now = now or datetime.datetime.now()
    try:
        with open(made_path, encoding="utf-8") as f:
            made = json.load(f)
    except (OSError, ValueError):
        made = {}
    ids = []
    for i, q in due_dailies(cfg["app"].get("daily"), made, now):
        ids.append(owner_quest(feed, cfg, project, q["title"], q.get("why", "")))
        made[str(i)] = now.strftime("%Y-%m-%d")
    if ids:
        tmp = made_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(made, f)
        os.replace(tmp, made_path)
    return ids


# ---- your own office building (docs/OFFICE.md) --------------------------------------------------------------------------------------------

def office_buildings(assets_dir):
    """Every <assets>/office/*.glb as a building for the Options picker. An optional <name>.json beside it may set "label", "music"
    (words matched against the baked music), "start" ([x, z] in metres) and "desk_y" ([low, high] floor heights for automatic desks)."""
    import json
    d = Path(assets_dir) / "office"
    out = []
    if not d.is_dir():
        return out
    for glb in sorted(d.glob("*.glb")):
        b = {"key": "custom:" + glb.stem, "label": glb.stem.replace("_", " ").title() + " (yours)", "glb": "office/" + glb.name, "custom": True, "music": ["TavernAlliance"]}
        side = glb.with_suffix(".json")
        if side.is_file():
            try:
                with open(side, encoding="utf-8") as f:
                    extra = json.load(f)
            except (OSError, ValueError):
                extra = {}
            if isinstance(extra, dict):
                for k in ("label", "music", "start", "desk_y"):
                    if k in extra:
                        b[k] = extra[k]
        out.append(b)
    return out
