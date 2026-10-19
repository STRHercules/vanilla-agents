"""One-command onboarding (D-330..D-339): detect the project type, create state/db, build a PER-PROJECT trusted folder, write the registry entry
(its manifest hash lives in the entry, not in a repo doc). Used by scripts/onboard.py and by the window's /api/onboard. Stdlib only, no model calls."""
import json
import os
import re
import tempfile
from pathlib import Path

from ..contracts import VanillaError, ValidationError
from ..storage import StorageService, layout
from ..verify.rules import validate_check_def
from . import idea
from .detect import detect
from .trusted import make_trusted


class OnboardError(VanillaError):
    code = "onboard_failed"


def norm(p):
    return os.path.normcase(os.path.realpath(str(p)))       # D-390: links and junctions resolve to the real folder


def slugify(name):
    return re.sub(r"[^a-z0-9._-]+", "-", str(name).lower()).strip("-.") or "project"


def load_registry(path):
    try:
        reg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return reg if isinstance(reg, dict) else {}


def write_registry(path, key, entry):
    reg = load_registry(path)
    reg[key] = entry
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=1)
    os.replace(tmp, path)


def find(reg, project):
    """(key, entry) for a project given as a path or as its short name, or (None, None). Strict: an exact normalized path or exact name, nothing else."""
    for k, v in reg.items():
        if isinstance(v, dict) and (norm(k) == norm(project) or os.path.normcase(v.get("name") or Path(k).name) == os.path.normcase(str(project))):       # names ignore case on Windows
            return k, v
    return None, None


def _outside(repo, trusted):
    return os.path.commonpath([norm(repo), norm(trusted)]) not in (norm(repo), norm(trusted))


def _free_dir(root, slug, reg):
    used = {norm(v.get("trusted_dir", "")) for v in reg.values() if isinstance(v, dict)}
    n, cand = 1, slug
    while norm(os.path.join(root, cand)) in used or os.path.exists(os.path.join(root, cand)):
        n += 1
        cand = "%s-%d" % (slug, n)
    return cand, os.path.join(root, cand)


def _make_state(repo):
    """Empty vanilla-agents database in <repo>/state (kept out of git through .git/info/exclude, a local file that is never committed)."""
    if layout(repo)["db"].is_file():
        return
    svc = StorageService(repo)
    svc.open()
    svc.close()
    ex = Path(repo) / ".git" / "info" / "exclude"
    try:
        have = ex.read_text(encoding="utf-8") if ex.is_file() else ""
        if "/state/" not in have.splitlines():
            ex.parent.mkdir(parents=True, exist_ok=True)
            ex.write_text(have + ("" if not have or have.endswith("\n") else "\n") + "/state/\n", encoding="utf-8")
    except OSError:
        pass


def summary(key, entry, already=False, detected=None, drafted=False):
    return {"drafted": drafted, "name": entry.get("name") or Path(key).name, "path": key, "already": already, "type": (detected or {}).get("type", ""),
            "checks": list(entry.get("criteria", []))}


def onboard(repo, registry_path, trusted_root, name=None, ideas_root=None):
    """Set one repo up for checking. Idempotent: an already-registered repo is left exactly as it is (use regen/add_holdout to change it).
    With ideas_root, the project's IDEA.md is drafted from its docs unless it already has one (D-341)."""
    repo = os.path.realpath(str(repo))
    if not os.path.isdir(repo):
        raise OnboardError("there is no folder at %s" % repo)
    if not os.path.exists(os.path.join(repo, ".git")):
        raise OnboardError("%s is not a git folder (the checker needs git saves to look at)" % repo)
    reg = load_registry(registry_path)
    k, e = find(reg, repo)
    if e:
        return summary(k, e, already=True, drafted=bool(ideas_root) and idea.write_draft(ideas_root, e.get("name") or slugify(Path(k).name), k))
    slug, tdir = _free_dir(trusted_root, slugify(name or Path(repo).name), reg)
    if not _outside(repo, tdir):
        raise OnboardError("the trusted folder must be outside the project (it is %s)" % tdir)
    d = detect(repo)
    h = make_trusted(tdir, checks=d["checks"], protected=d["protected"])
    _make_state(repo)
    entry = {"repo": repo, "name": slug, "trusted_dir": tdir, "expected_manifest_hash": h, "criteria": [c["check_id"] for c in d["checks"]],
             "default_base_ref": "HEAD", "route": {"full_paths": d["full_paths"]}}
    write_registry(registry_path, repo, entry)
    return summary(repo, entry, detected=d, drafted=bool(ideas_root) and idea.write_draft(ideas_root, slug, repo))


def _read_list(tdir, name):
    try:
        v = json.loads((Path(tdir) / name).read_text(encoding="utf-8"))
    except OSError:
        return []
    except ValueError:
        raise OnboardError("%s in %s is damaged; nothing was changed" % (name, tdir))
    if not isinstance(v, list):
        raise OnboardError("%s in %s is not a list; nothing was changed" % (name, tdir))
    return v


def rebuild(registry_path, key, holdouts=None):
    """Rebuild one project's trusted folder from its own checks.json / protected.json / holdout.json (kept as they are, or `holdouts` replaces them)
    and record the new fingerprint in its registry entry. Run after the checker code changed, or after adding a holdout. Returns the hash."""
    reg = load_registry(registry_path)
    e = reg.get(key)
    if not isinstance(e, dict):
        raise OnboardError("%s is not registered" % key)
    t = e["trusted_dir"]
    h = make_trusted(t, checks=_read_list(t, "checks.json"), protected=_read_list(t, "protected.json"),
                     holdouts=_read_list(t, "holdout.json") if holdouts is None else holdouts)
    write_registry(registry_path, key, dict(e, expected_manifest_hash=h))
    return h


def add_holdout(registry_path, project, defs):
    """Add hidden check definitions for a project (D-290..D-293 / D-336). Content goes only into that project's trusted holdout.json; only the count and hash come back."""
    k, e = find(load_registry(registry_path), project)
    if not e:
        raise OnboardError("%s is not set up; run onboard.py on it first" % project)
    defs = [defs] if isinstance(defs, dict) else list(defs)
    if not defs:
        raise OnboardError("no check definitions given")
    ids = {c.get("check_id") for c in _read_list(e["trusted_dir"], "checks.json")}
    for d in defs:
        if not isinstance(d, dict):
            raise ValidationError("a holdout must be a check definition object")
        validate_check_def(d)
        if d["check_id"] in ids or str(d["check_id"]).startswith("hidden-"):
            raise ValidationError("holdout id %r clashes with a normal check or the reserved hidden- names" % d["check_id"])
    merged = {c["check_id"]: c for c in _read_list(e["trusted_dir"], "holdout.json")}
    merged.update({d["check_id"]: d for d in defs})
    return {"holdouts": len(merged), "hash": rebuild(registry_path, k, holdouts=list(merged.values()))}
