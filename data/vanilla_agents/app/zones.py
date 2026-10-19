"""World map data (DESIGN 2, D-260..D-269): the project's source areas as zones, and how much of each the agents have seen.
Zones come read-only from `git ls-tree` / `git grep -c` on HEAD (bounded, never writes, never puts file contents anywhere).
Touches come from tool_use events (path only). Recent touch times are kept in a small bounded JSON file OUTSIDE git."""
import json
import os
import posixpath
import subprocess
import time

from ..events.redact_util import clean

OTHER = "other"


def zone_of(rel, split_dirs, is_dir=False):
    """Zone id for a repo-relative path (forward slashes): top folder; for a split dir its first sub-folder or module file.
    Files in the repo root share the zone '.'. A bare split dir (e.g. 'src' as a Grep path) names no single zone: None."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return None
    if len(parts) == 1:
        return None if parts[0] in split_dirs else parts[0] if is_dir else "."
    return parts[0] + "/" + parts[1] if parts[0] in split_dirs else parts[0]


def _slashes(p):
    """A path with forward slashes and '.', '..', '//' folded (string only: Windows paths work on any OS)."""
    return posixpath.normpath(str(p).replace("\\", "/"))


def _is_abs(s):
    return s.startswith("/") or (len(s) > 2 and s[0].isalpha() and s[1] == ":" and s[2] == "/")


def _fold(s):
    """Windows paths (a drive letter, or running on Windows) compare without case."""
    return s.casefold() if os.name == "nt" or (len(s) > 1 and s[1] == ":") else s


def rel_in_repo(path, repo):
    """Path of an absolute file inside the repo, relative with forward slashes; None when outside it.
    V-047: compared as strings with slashes normalised (C:\\x\\y and C:/x/y are the same place, also when this runs on Linux)."""
    if not isinstance(path, (str, os.PathLike)) or not isinstance(repo, (str, os.PathLike)) or not str(path) or not str(repo):
        return None
    try:
        p, r = _slashes(path), _slashes(repo)
        p = p if _is_abs(p) else _slashes(os.path.abspath(path))
        r = r if _is_abs(r) else _slashes(os.path.abspath(repo))
    except (TypeError, ValueError):
        return None
    base = r.rstrip("/")
    if _fold(p) == _fold(r) or not _fold(p).startswith(_fold(base) + "/"):
        return None
    return p[len(base) + 1:]


def repo_rel(path, root, own_root=None):
    """V-047: the one path rule for the map (fog / explored and the hero dots): repo-relative path of a file a helper touched, or None.
    An absolute path must be inside the project (root, as the feed spells it) or inside the helper's own checkout (own_root: its git
    worktree of the same project, V-038); a relative one is taken as repo-relative ('./' dropped); one that climbs out ('../') is outside."""
    if not isinstance(path, str) or not path.strip():
        return None
    f = _slashes(path.strip())
    if _is_abs(f):
        return (rel_in_repo(f, root) if root else None) or (rel_in_repo(f, own_root) if own_root else None)
    if f in (".", "..") or f.startswith("../"):
        return None
    return f


def _git(repo, args, m):
    r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo)] + args, capture_output=True, timeout=m["git_timeout_seconds"])
    if r.returncode != 0:
        raise OSError("git failed")
    return r.stdout[: m["git_max_bytes"]].decode("utf-8", "replace")


def head_of(repo, m):
    return _git(repo, ["rev-parse", "HEAD"], m).strip()


def derive(repo, m):
    """[{'id','files','bytes','lines'}] for every zone in HEAD (uncapped). lines is None if git could not count them."""
    split = set(m["split_dirs"])
    zones = {}
    for ln in _git(repo, ["ls-tree", "-r", "-l", "-z", "HEAD"], m).split("\0"):
        if "\t" not in ln:
            continue
        meta, path = ln.split("\t", 1)
        try:
            size = int(meta.split()[-1])
        except ValueError:
            size = 0
        zid = zone_of(path, split)
        z = zones.setdefault(zid, {"id": zid, "files": 0, "bytes": 0, "lines": 0})
        z["files"] += 1
        z["bytes"] += size
    try:
        out = _git(repo, ["grep", "-I", "-c", "", "HEAD", "--"], m)
        for ln in out.splitlines():
            head, _, n = ln.rpartition(":")
            path = head.split(":", 1)[1] if ":" in head else head
            z = zones.get(zone_of(path, split))
            if z is not None and n.isdigit():
                z["lines"] += int(n)
    except (OSError, subprocess.SubprocessError):
        for z in zones.values():
            z["lines"] = None
    return list(zones.values())


def cap(zones, m):
    """Keep the biggest zone_max-1 (by lines, else bytes) when there are too many; the rest become one 'other' zone. Sorted by id for a stable map."""
    mx = m["zone_max"]
    w = lambda z: (z["lines"] or 0, z["bytes"])
    if len(zones) <= mx:
        return sorted(zones, key=lambda z: z["id"])
    big = sorted(zones, key=lambda z: (-w(z)[0], -w(z)[1], z["id"]))
    keep, rest = big[: mx - 1], big[mx - 1:]
    other = {"id": OTHER, "files": sum(z["files"] for z in rest), "bytes": sum(z["bytes"] for z in rest),
             "lines": sum(z["lines"] or 0 for z in rest) if all(z["lines"] is not None for z in rest) else None, "merged": sorted(z["id"] for z in rest)}
    return sorted(keep, key=lambda z: z["id"]) + [other]


class Activity:
    """{project: {zone_id: {'touch': ts, 'edit': ts}}} on disk. Bounded: entries older than keep_days dropped, zone_max_entries per project, max_projects."""

    def __init__(self, path, m, clock=time.time):
        self.path, self.m, self.clock, self.dirty = path, m, clock, False
        self.d = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            raw = d.get("projects") if isinstance(d, dict) else None
            for proj, zs in (raw or {}).items():
                if isinstance(zs, dict):
                    self.d[str(proj)] = {k: {"touch": float(v.get("touch", 0)), "edit": float(v.get("edit", 0))} for k, v in zs.items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError, AttributeError):
            self.d = {}
        self._prune()

    def touch(self, project, zone, ts, edit):
        e = self.d.setdefault(str(project), {}).setdefault(zone, {"touch": 0.0, "edit": 0.0})
        if ts > e["touch"]:
            e["touch"], self.dirty = ts, True
        if edit and ts > e["edit"]:
            e["edit"], self.dirty = ts, True

    def get(self, project):
        return self.d.get(str(project), {})

    def _prune(self):
        floor = self.clock() - self.m["activity_keep_days"] * 86400
        for proj in list(self.d):
            zs = {k: v for k, v in self.d[proj].items() if v["touch"] >= floor}
            keep = sorted(zs, key=lambda k: -zs[k]["touch"])[: self.m["activity_max_zones"]]
            self.d[proj] = {k: zs[k] for k in keep}
            if not self.d[proj]:
                del self.d[proj]
        newest = sorted(self.d, key=lambda p: -max(v["touch"] for v in self.d[p].values()))
        for p in newest[self.m["activity_max_projects"]:]:
            del self.d[p]

    def flush(self):
        if not self.dirty or not self.path:
            return
        self._prune()
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": 1, "projects": self.d}, f, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass


class Tracker:
    """Turns tool_use events into zone touches for one project, and remembers where each agent last worked.
    V-047: paths go through repo_rel, the same rule as the hero dots (relative paths, the helper's own worktree, Windows spelling);
    same_repo(root) says whether a helper's reported checkout is this project or one of its worktrees (asked once per root)."""

    def __init__(self, project, m, activity, same_repo=None):
        self.project, self.m, self.act = project, m, activity
        self.root = clean(str(project)) if project else None      # events carry redacted paths (C:\Users\[user]\...): redact the repo path the same way
        self.last = {}      # agent -> (zone id, ts)
        self.same_repo, self._mine = same_repo, {}

    def own(self, root):
        """The helper's checkout root when it is a checkout of this project, else None (memoised)."""
        if not root or not self.same_repo:
            return None
        if root not in self._mine:
            try:
                self._mine[root] = bool(self.same_repo(root))
            except (OSError, ValueError, TypeError):
                self._mine[root] = False
        return root if self._mine[root] else None

    def on_tool(self, ev, own_root=None):
        d = ev.data
        tool, f = d.get("tool"), d.get("file")
        edit = tool in self.m["edit_tools"]
        if not (self.root and isinstance(f, str) and (edit or tool in self.m["read_tools"])):
            return
        rel = repo_rel(f, self.root, self.own(own_root))
        z = zone_of(rel, self.m["split_dirs"], "/" not in rel and os.path.isdir(os.path.join(self.project, rel))) if rel else None
        if z is None:
            return
        self.act.touch(self.project, z, ev.ts, edit)
        if ev.agent:
            self.last[ev.agent] = (z, ev.ts)


def fold(touches, zone):
    """Touch record for a built zone (an 'other' zone folds the touches of the zones it merged)."""
    ids = zone.get("merged") or [zone["id"]]
    ts = [touches[i] for i in ids if i in touches]
    return {"touch": max((t["touch"] for t in ts), default=0), "edit": max((t["edit"] for t in ts), default=0)} if ts else None


def build(zones, touches, run_list, here, m, eli5, now):
    """The /api/zones list. run_list = newest-first run log. here = {agent: zone id} for working agents.
    CLEARED = touched AND a VERIFIED run with a recorded commit finished after the zone's last edit (whole-project checks, not coverage)."""
    ok = [r for r in run_list if r.get("verdict") == "VERIFIED" and r.get("commit") and isinstance(r.get("ts"), (int, float))]
    best = max(ok, key=lambda r: r["ts"], default=None)
    cols = m["grid"]["cols"]
    out = []
    for i, z in enumerate(zones):
        t = fold(touches, z)
        state, edited, commit = "UNDISCOVERED", False, ""
        if t and t["touch"]:
            state = "EXPLORED"
            if best and best["ts"] > t["edit"]:
                state, commit = "CLEARED", str(best["commit"])[:12]
            elif t["edit"]:
                edited = True
        ids = z.get("merged") or [z["id"]]
        agents = sorted(a for a, zid in here.items() if zid in ids)
        out.append({"id": z["id"], "name": eli5.get(z["id"], {}).get("name") or z["id"], "eli5": eli5.get(z["id"], {}).get("eli5", ""), "files": z["files"],
                    "lines": z["lines"], "bytes": z["bytes"], "merged": len(z.get("merged", [])), "state": state, "edited_since_check": edited, "commit": commit,
                    "last_touch": t["touch"] if t and t["touch"] else None, "last_edit": t["edit"] if t and t["edit"] else None,
                    "col": i % cols, "row": i // cols, "here": agents})
    return out


def zone_for_file(path, root, split_dirs, own_root=None):
    """V-045: zone id for a file a helper touched (the same rules as the map: zone_of on repo_rel's path). None = no zone."""
    rel = repo_rel(path, root, own_root)
    return zone_of(rel, split_dirs) if rel else None


def hero_spots(files, last, root, split_dirs, zone_list, roots=None):
    """V-045: [{agent, zone, file, ts}] = each helper in the built zone of the file it touched most recently.
    files = the snapshot's changed files [{file, agent, ts}] (newest first); last = {agent: (zone id, ts)} from the Tracker (reads too);
    roots = {agent: its own checkout root} so a helper working in a worktree of the project still lands in the right zone.
    A zone merged into 'other' maps to 'other'; a file outside every built zone gives no spot."""
    built = {}
    for z in zone_list:
        for i in z.get("merged") or [z["id"]]:
            built[i] = z["id"]
    best = {}
    for f in files or []:
        a = f.get("agent")
        if not a or (a in best and best[a]["ts"] >= _num(f.get("ts"))):
            continue
        zid = zone_for_file(f.get("file"), root, split_dirs, (roots or {}).get(a))
        if zid in built:
            best[a] = {"agent": a, "zone": built[zid], "file": str(f.get("file")), "ts": _num(f.get("ts"))}
    for a, (zid, ts) in (last or {}).items():
        if zid in built and (a not in best or _num(ts) > best[a]["ts"]):
            best[a] = {"agent": a, "zone": built[zid], "file": "", "ts": _num(ts)}
    return sorted(best.values(), key=lambda h: (h["zone"], h["agent"]))


def _num(ts):
    """Event times are epoch numbers in the feed; anything else sorts as oldest."""
    return float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else 0.0
