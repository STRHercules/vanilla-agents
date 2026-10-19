"""GET /api/zones (D-260): derive the zones (cached per git HEAD), mix in touches + the run log, add plain-language names. Read-only."""
import os
import subprocess
import time

from ..events import worktree
from . import runs, zones
from .resurrect import real_folder


class ZoneCache:
    def __init__(self):
        self.key, self.val = None, None

    def get(self, repo, m):
        head = zones.head_of(repo, m)
        if self.key != (str(repo), head):
            self.val, self.key = zones.cap(zones.derive(repo, m), m), (str(repo), head)
        return head, self.val


def eli5_map(cfg):
    return {z["id"]: z for z in cfg["strings"]["zones"]["zones"]}


def payload(cfg, cache, state, runs_path, now=None):
    """state = the AppState (project, tracker, folded agents). Never raises: a problem is a plain code the panel words."""
    m, ui = cfg["map"], cfg["strings"]["ui"]
    base = {"grid": m["grid"], "zones": [], "problem": "", "head": "", "project": ""}
    st = state
    if not st.project or st.tracker is None:
        return {**base, "problem": "no_project"}
    base["project"] = str(st.project)
    try:
        head, zl = cache.get(st.project, m)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {**base, "problem": "not_git"}
    with st.lock:
        touches = {k: dict(v) for k, v in st.tracker.act.get(st.project).items()}
        here = {a: z for a, (z, ts) in st.tracker.last.items() if st.f.agents.get(a, {}).get("state") == "working"}
        last = {a: v for a, v in st.tracker.last.items() if a in st.f.agents}
        files = [f for f in st.f.files if f.get("agent") in st.f.agents]       # V-045: helpers still here, as dots on the map
        roots = {a: v["root"] for a, v in st.f.agents.items() if v.get("root") and same_repo(v, st.project)}
    eli = eli5_map(cfg)
    out = zones.build(zl, touches, runs.read(runs_path, 200), here, m, eli, now or time.time())
    for z in out:
        if z["id"] == zones.OTHER and not z["name"] or z["id"] == zones.OTHER and z["name"] == zones.OTHER:
            z["name"] = ui["map.other_name"].replace("{count}", str(z["merged"]))
        if z["id"] == "." and z["name"] == ".":
            z["name"] = ui["map.root_name"]
    heroes = zones.hero_spots(files, last, st.tracker.root, m["split_dirs"], out, roots)
    seen = {h["zone"]: h["ts"] for h in heroes}
    for z in out:            # V-047: a helper's dot never stands in fog (the tracker uses the same path rule; this covers files from other tools)
        if z["state"] == "UNDISCOVERED" and z["id"] in seen:
            z["state"], z["last_touch"] = "EXPLORED", z["last_touch"] or seen[z["id"]] or None
    return {**base, "head": head[:12], "zones": out, "heroes": heroes}


def same_repo(agent, project):
    """V-045: the helper's checkout is the project itself or one of its git worktrees (so its file paths map onto the project's zones).
    The feed hides the user name in paths ('[user]'): put it back before looking at the folder."""
    loc = worktree.locate(real_folder(agent.get("root")))
    return bool(loc) and os.path.normcase(os.path.realpath(loc.get("repo", ""))) == os.path.normcase(os.path.realpath(str(project)))
