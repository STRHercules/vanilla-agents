"""Recorded 'plan approved' marks (D-310): which quests the owner approved, kept in one small JSON file OUTSIDE git (app.json approvals_path).
{"projects": {project: {task_id: {"ts", "title", "who", "why"[, "vetoed"]}}}} (D-370: every decision stores who decided and why; who = 'the owner', 'you (via phone session)' or 'auto').
This file, not the event feed, is what makes an approval real (D-380): a feed line alone approves nothing.
Projects are keyed by their real path (D-390: links and junctions resolve, Windows case ignored). Bounded (newest kept; Idea approvals 'idea:...' are never dropped), written atomically, a damaged file reads as empty."""
import json
import os
import contextlib
import threading
import time

_LOCK = threading.Lock()


@contextlib.contextmanager
def _filelock(path, wait=10.0):
    """Cross-process lock (the app and scripts/decide.py both write this file): a stale write must not undo a veto. Lock older than 30 s counts as dead."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lock, end = path + ".lock", time.monotonic() + wait
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 30:
                    os.remove(lock)
                    continue
            except OSError:
                pass
            if time.monotonic() > end:
                raise OSError("the approvals file is busy; try again")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(lock)


def norm(project):
    """The key for a project: its real path (links resolved), case-folded on Windows. '' = no project open."""
    return os.path.normcase(os.path.realpath(str(project))) if project else ""


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f).get("projects")
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for p, ts in d.items():
        if isinstance(ts, dict):
            out.setdefault(norm(p), {}).update({str(t): v for t, v in ts.items() if isinstance(v, dict)})       # older files used the path as typed: merge those spellings
    return out


def load(path, project):
    """{task_id: {ts, title, who, why[, vetoed]}} for one project ('' = no project open)."""
    return _read(path).get(norm(project), {}) if path else {}


def _prune(mine, max_tasks):
    quests = [k for k in mine if not k.startswith("idea:")]
    for k in sorted(quests, key=lambda k: mine[k].get("ts", 0), reverse=True)[max_tasks:]:
        del mine[k]


def _store(path, d, key, max_tasks, max_projects):
    _prune(d[key], max_tasks)
    d[key] = d.pop(key)      # newest project last
    for p in list(d)[:-max_projects]:
        d[p] = {k: v for k, v in d[p].items() if k.startswith("idea:")}       # the cap never drops an Idea approval
        if not d[p]:
            del d[p]
    _write(path, d)


def add(path, project, task, title, ts, max_tasks, max_projects, who="you", why="approved by you"):
    """Record the approval. Returns False (and writes nothing) if that quest was already approved (a vetoed one may be approved again)."""
    with _LOCK, _filelock(path):
        d, key = _read(path), norm(project)
        mine = d.setdefault(key, {})
        if task in mine and not mine[task].get("vetoed"):
            return False
        mine[task] = {"ts": ts, "title": str(title)[:200], "who": str(who)[:40], "why": str(why)[:300]}
        _store(path, d, key, max_tasks, max_projects)
        return True


def _write(path, d):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"projects": d}, f, separators=(",", ":"))
    os.replace(tmp, path)


def veto(path, project, task, ts):
    """Mark an approval as undone by the owner (D-376). False if there is no live approval to undo."""
    with _LOCK, _filelock(path):
        d = _read(path)
        r = d.get(norm(project), {}).get(task)
        if not r or r.get("vetoed"):
            return False
        r["vetoed"] = ts
        _write(path, d)
        return True
