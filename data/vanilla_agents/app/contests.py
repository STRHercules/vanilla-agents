"""V-045: competing fixes ("several helpers try, you roll for the winner").
The owner opens a competition on a quest (2-4 tries); helpers send proposals through MCP (propose_fix: a summary and their branch or
worktree); the owner can run the checker on one proposal, look at its changes, and pick the winner. Picking is the owner's alone:
there is no MCP tool for it, and a feed line alone never makes a winner. Like approvals (D-380), the owner's decisions live in one
small JSON file outside git (contests.json next to app.json approvals_path): {"quests": {quest id: {ts, title, tries, repo, base,
winner?, picked_ts?, closed?}}}. Vanilla Agents never merges: the winner is told to merge it into the base branch.
Git is only read, always with argument lists and a timeout; branch names must match a strict pattern before they reach git."""
import json
import os
import re
import subprocess
import threading
import time

from ..events import worktree
from .approvals import _filelock

TRIES = (2, 3, 4)
MAX_KEEP = 60
MAX_SUMMARY = 300
MAX_FILES = 40
TIMEOUT = 20
_LOCK = threading.Lock()
# a branch name we pass to git: starts with a letter or digit, then letters, digits and . _ / -; no '..', '//', '@{', no '.lock' or '/' at the end
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}\Z")      # \Z: "$" would let a trailing newline through (V-048)
_GIT = ["git", "-c", "core.fsmonitor=false", "-c", "diff.external=", "-c", "core.hooksPath=" + os.devnull]


class ContestError(ValueError):
    """A plain-language reason why a competition step cannot happen (shown to the owner or the helper as is)."""


def path_for(app_cfg):
    """contests.json lives next to the approvals file (same trust: outside git, the owner's decisions)."""
    a = app_cfg.get("approvals_path") or os.path.expanduser("~/.vanilla-agents/work/approvals.json")
    return os.path.join(os.path.dirname(os.path.abspath(a)), "contests.json")


def valid_ref(name):
    """True for a plain local branch name we are willing to give git (or the word HEAD)."""
    if not isinstance(name, str) or not REF.match(name):
        return False
    return not (".." in name or "//" in name or "@{" in name or name.endswith((".lock", "/", ".")) or "/." in name)


# ---- the owner's file ---------------------------------------------------------------------------------------------------------------

def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f).get("quests")
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(k): v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _write(path, d):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    keep = sorted(d, key=lambda k: d[k].get("ts", 0))[-MAX_KEEP:]
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"quests": {k: d[k] for k in keep}}, f, separators=(",", ":"))
    os.replace(tmp, path)


def is_open(rec):
    return bool(rec) and not rec.get("winner") and not rec.get("closed")


def open_contest(path, quest, title, tries, repo, base, clock=time.time):
    if tries not in TRIES:
        raise ContestError("How many helpers may try: 2, 3 or 4.")
    with _LOCK, _filelock(path):
        d = load(path)
        if is_open(d.get(quest)):
            raise ContestError("Several helpers are already trying this quest.")
        d[quest] = {"ts": clock(), "title": str(title)[:200], "tries": tries, "repo": str(repo), "base": base}
        _write(path, d)
        return d[quest]


def close(path, quest, winner=None, clock=time.time):
    """Close an open competition, with a winner (the owner's pick) or without (the owner stopped it). Returns the record."""
    with _LOCK, _filelock(path):
        d = load(path)
        rec = d.get(quest)
        if not is_open(rec):
            raise ContestError("This quest has no open competition.")
        if winner:
            rec.update(winner=str(winner)[:200], picked_ts=clock())
        else:
            rec["closed"] = clock()
        _write(path, d)
        return rec


# ---- proposals (folded from the feed) -------------------------------------------------------------------------------------------------

def proposals(feed, quest, since):
    """{agent: proposal} for one quest from fix_proposed / fix_checked events after the competition opened (a newer proposal replaces older)."""
    out = {}
    for ev in feed.read_since(0, 1000000):
        if ev.task != quest or ev.ts < since or not ev.agent:
            continue
        if ev.type == "fix_proposed":
            out[ev.agent] = proposal_of(ev)
        elif ev.type == "fix_checked" and ev.agent in out and ev.data.get("branch") in (None, out[ev.agent]["branch"]):
            out[ev.agent].update(verdict=ev.data["verdict"], reason=ev.data.get("reason", ""))
    return out


def proposal_of(ev):
    d = ev.data
    files = [f for f in (d.get("files") or "").split("\n") if f]
    return {"agent": ev.agent, "ts": ev.ts, "summary": d.get("summary", ""), "branch": d.get("branch", ""), "worktree": d.get("worktree", ""),
            "files": files, "added": d.get("lines_added", 0), "removed": d.get("lines_removed", 0), "verdict": None, "reason": ""}


# ---- git (read-only) --------------------------------------------------------------------------------------------------------------------

def _run(repo, args, timeout=TIMEOUT):
    return subprocess.run(_GIT + ["-C", str(repo)] + args, capture_output=True, timeout=timeout)


def branch_exists(repo, branch):
    if not valid_ref(branch):
        return False
    if branch == "HEAD":
        return True
    try:
        r = _run(repo, ["rev-parse", "--verify", "--quiet", "refs/heads/" + branch + "^{commit}"])
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def worktrees(repo):
    """[{path, branch}] from `git worktree list --porcelain` (the main checkout too)."""
    try:
        r = _run(repo, ["worktree", "list", "--porcelain"])
    except (OSError, subprocess.SubprocessError):
        return []
    out, cur = [], None
    for ln in r.stdout.decode("utf-8", "replace").splitlines():
        if ln.startswith("worktree "):
            cur = {"path": ln[len("worktree "):], "branch": ""}
            out.append(cur)
        elif ln.startswith("branch ") and cur is not None:
            ref = ln[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ""
    return out


def _real(p):
    return os.path.normcase(os.path.realpath(p))


def resolve(repo, branch=None, worktree_path=None):
    """(branch, worktree path or '') for a proposal, or ContestError. A worktree must be one of the repository's own worktrees
    (exact folder, links resolved), so a path cannot point anywhere else; a branch must be a local branch of that repository."""
    wt = ""
    if worktree_path:
        if not isinstance(worktree_path, str) or "\x00" in worktree_path or len(worktree_path) > 1000:
            raise ContestError("worktree must be a folder path")
        want = _real(worktree_path)
        hit = next((w for w in worktrees(repo) if _real(w["path"]) == want), None)
        if hit is None:
            raise ContestError("That folder is not a worktree of the project's repository (see git worktree list).")
        wt = hit["path"]
        if branch and branch != hit["branch"]:
            raise ContestError("That worktree is on branch %s, not %s." % (hit["branch"] or "(none)", branch))
        branch = hit["branch"]
        if not branch:
            loc = worktree.locate(wt)
            branch = loc.get("branch", "") if not loc.get("branch", "").startswith("detached") else ""
    if not branch:
        raise ContestError("Give your branch (or your worktree folder) so the owner can see your changes.")
    if not valid_ref(branch) or branch == "HEAD":
        raise ContestError("That branch name is not allowed (letters, digits and . _ / - only).")
    if not branch_exists(repo, branch):
        raise ContestError("There is no branch %s in the project's repository. Commit your work on a branch first." % branch)
    return branch, wt


def changed(repo, base, branch, limit=MAX_FILES):
    """([{file, added, removed}], added total, removed total) for base...branch (what the branch changed since it left base)."""
    if not (valid_ref(base) and valid_ref(branch)):
        raise ContestError("bad branch name")
    try:
        r = _run(repo, ["diff", "--numstat", "--no-ext-diff", "--no-color", "--no-renames", base + "..." + branch, "--"])
    except (OSError, subprocess.SubprocessError) as e:
        raise ContestError("git could not compare the branches (%s)" % e)
    if r.returncode:
        raise ContestError("git could not compare %s with %s" % (branch, base))
    files, add, rem = [], 0, 0
    for ln in r.stdout.decode("utf-8", "replace").splitlines():
        parts = ln.split("\t")
        if len(parts) != 3:
            continue
        a, b = (int(x) if x.isdigit() else 0 for x in parts[:2])
        add, rem = add + a, rem + b
        files.append({"file": parts[2], "added": parts[0], "removed": parts[1]})
    return files[:limit], add, rem


def base_of(repo):
    """The branch the project's own checkout is on (the competition's base), or HEAD when detached / unknown."""
    b = worktree.locate(repo).get("branch", "")
    return b if valid_ref(b) and branch_exists(repo, b) else "HEAD"


# ---- helper side (MCP propose_fix) ---------------------------------------------------------------------------------------------------

def propose(feed, path, quest, agent, summary, branch=None, worktree_path=None):
    """Record a helper's proposal for an open competition. Proposals only: nobody but the owner picks a winner."""
    if not isinstance(quest, str) or not quest:
        raise ContestError("say which quest (its id from list_quests)")
    rec = load(path).get(quest)
    if rec is None:
        known = any(ev.task == quest for ev in feed.read_since(0, 1000000))
        raise ContestError(("Quest %s is not open for competing fixes; the owner decides that." if known else "There is no quest called %s.") % quest)
    if not is_open(rec):
        raise ContestError("The competition for this quest is over" + (" (the owner picked a winner)." if rec.get("winner") else "."))
    summary = (summary or "").strip() if isinstance(summary, str) else ""
    if not summary:
        raise ContestError("give a short summary of your fix")
    have = proposals(feed, quest, rec["ts"])
    if agent not in have and len(have) >= rec["tries"]:
        raise ContestError("All %d tries for this quest are taken." % rec["tries"])
    branch, wt = resolve(rec["repo"], branch if branch else None, worktree_path)
    if branch == rec.get("base"):
        raise ContestError("Propose a branch of your own, not the base branch %s." % branch)
    # V-049: the tool cannot tell which session calls it, so a proposal is tied to its branch: resending is only possible from the
    # same branch, and nobody can hand in a branch another helper already proposed (no replacing someone else's fix by naming them)
    taken = [a for a, pr in have.items() if a != agent and pr.get("branch") == branch]
    if taken:
        raise ContestError("Branch %s is already %s's proposal; propose a branch of your own." % (branch, taken[0]))
    if agent in have and have[agent].get("branch") not in (None, branch):
        raise ContestError("%s already proposed branch %s; resend from that branch (the owner can stop the competition to start over)." % (agent, have[agent]["branch"]))
    files, add, rem = changed(rec["repo"], rec.get("base") or "HEAD", branch)
    data = {"title": rec.get("title") or quest, "summary": summary[:MAX_SUMMARY], "branch": branch, "files": "\n".join(f["file"] for f in files)[:1900],
            "lines_added": add, "lines_removed": rem}
    if wt:
        data["worktree"] = wt[:1000]
    feed.append("fix_proposed", task=quest, agent=agent, data=data)
    return {"branch": branch, "worktree": wt, "files": len(files), "added": add, "removed": rem}


# ---- owner side (the window, POST /api/contest) -----------------------------------------------------------------------------------------

def owner_open(ctx, quest, tries):
    """The owner's 'Let several helpers try'. Needs an open git project (proposals are branches of it)."""
    from ..events.feed import Feed
    proj = ctx.state.project
    if not proj or not os.path.exists(os.path.join(str(proj), ".git")):
        raise ContestError("Open a project that is a git folder first: the helpers' fixes are branches of it.")
    ctx.state.refresh()
    with ctx.state.lock:
        t = ctx.state.f.tasks.get(quest)
    if t is None:
        raise ContestError("There is no quest called %s." % quest)
    if t["status"] in ("complete", "failed"):
        raise ContestError("That quest is already finished.")
    if not isinstance(tries, int) or isinstance(tries, bool):
        raise ContestError("How many helpers may try: 2, 3 or 4.")
    base = base_of(proj)
    open_contest(path_for(ctx.cfg["app"]), quest, t["title"], tries, os.path.abspath(str(proj)), base)
    Feed(ctx.feed_path).append("contest_opened", task=quest, data={"title": t["title"], "tries": str(tries), "base": base})
    ctx.state.refresh()


def owner_stop(ctx, quest):
    from ..events.feed import Feed
    rec = close(path_for(ctx.cfg["app"]), quest)
    Feed(ctx.feed_path).append("contest_closed", task=quest, data={"title": rec.get("title") or quest})
    ctx.state.refresh()


def _proposal(ctx, quest, agent):
    from ..events.feed import Feed
    rec = load(path_for(ctx.cfg["app"])).get(quest)
    if not is_open(rec):
        raise ContestError("This quest has no open competition.")
    p = proposals(Feed(ctx.feed_path), quest, rec["ts"]).get(agent)
    if p is None:
        raise ContestError("%s has not sent a fix for this quest." % agent)
    return rec, p


def owner_check(ctx, quest, agent):
    """'Check this one': the project's checker on that proposal's branch (its last commit)."""
    rec, p = _proposal(ctx, quest, agent)
    if not branch_exists(rec["repo"], p["branch"]):
        raise ContestError("The branch %s is gone." % p["branch"])
    return ctx.verify.start(ctx.state.project, proposal={"quest": quest, "agent": agent, "branch": p["branch"], "title": rec.get("title") or quest})


def owner_pick(ctx, quest, agent):
    """The owner picks the winner: record it, tell the winner to merge, thank the others, close the competition. Never merges."""
    from ..events.feed import Feed
    from . import social
    rec, p = _proposal(ctx, quest, agent)
    others = [a for a in proposals(Feed(ctx.feed_path), quest, rec["ts"]) if a != agent]
    rec = close(path_for(ctx.cfg["app"]), quest, winner=agent)
    feed, ui, title, base = Feed(ctx.feed_path), ctx.cfg["strings"]["ui"], rec.get("title") or quest, rec.get("base") or "HEAD"
    feed.append("fix_picked", task=quest, agent=agent, data={"title": title, "winner": agent, "base": base, "branch": p["branch"]})
    fill = lambda k: ui[k].replace("{title}", title).replace("{base}", base).replace("{branch}", p["branch"])
    social.whisper(feed, agent, fill("contest.whisper_winner"))
    for a in others:
        social.whisper(feed, a, fill("contest.whisper_other"))
    ctx.state.refresh()
    return others


def branch_diff(project, branch, base=None, file=None, max_bytes=200000):
    """GET /api/diff?branch=..[&base=..][&file=..]: what a proposal branch changed since it left base, in the OPEN project's repository only.
    Read-only git with argument lists; both names must pass valid_ref and be local branches there; the file is a plain path inside the repo."""
    if not project or not os.path.exists(os.path.join(str(project), ".git")):
        return {"error": "Open a project that is a git folder to see a helper's changes."}
    repo = os.path.abspath(str(project))
    base = base or base_of(repo)
    for b in (branch, base):
        if not valid_ref(b):
            return {"error": "That branch name is not allowed."}
        if not branch_exists(repo, b):
            return {"error": "There is no branch %s in this project." % b}
    rng = base + "..." + branch
    if file is None:
        try:
            files, add, rem = changed(repo, base, branch, limit=200)
        except ContestError as e:
            return {"error": str(e)}
        return {"branch": branch, "base": base, "files": [dict(f, status="M") for f in files], "added": add, "removed": rem}
    rel = str(file).replace("\\", "/")
    if not rel or "\x00" in rel or rel.startswith(("/", "-")) or re.match(r"^[A-Za-z]:", rel) or any(p in ("..", "") for p in rel.split("/")):
        return {"error": "That file is outside the project."}
    r = _run(repo, ["diff", "--no-ext-diff", "--no-color", "--no-renames", rng, "--", rel])
    return {"file": rel, "branch": branch, "base": base, "diff": r.stdout[:max_bytes].decode("utf-8", "replace"), "truncated": len(r.stdout) > max_bytes}
