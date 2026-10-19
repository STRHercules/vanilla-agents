"""Small/big work classifier (D-300): plain rules, no model. Decides the 'fast' route (quick checks only) or the 'full' route (every check and the holdouts).
Pure: takes the changed files since the last good check and the rules from app.json#route. Anything unclear is 'full'.
(D-346) out_of_scope = the Idea's out-of-scope path patterns: any changed file matching one forces the full route with an 'intent' reason."""
import fnmatch
import os
import subprocess


def _hit(path, pats):
    base = os.path.basename(path)
    return any(fnmatch.fnmatch(path, p) or ("/" not in p and fnmatch.fnmatch(base, p)) for p in pats)


def merge_rules(base, override):
    """Per-project route overrides (D-338): only the known keys with the right type replace the global rule; anything else is ignored (the global rule stands)."""
    out = dict(base)
    for k, v in (override or {}).items():
        if k in ("max_fast_files", "max_fast_lines"):
            ok = isinstance(v, int) and not isinstance(v, bool) and v >= 1
        elif k in ("full_paths", "trusted_paths"):
            ok = isinstance(v, list) and all(isinstance(x, str) for x in v) and (k != "trusted_paths" or bool(v))
        else:
            ok = False
        if ok:
            out[k] = v
    return out


def classify(changes, rules, trusted=(), out_of_scope=()):
    """changes: [{'path', 'lines'}] since the last verified commit, or None when that could not be worked out.
    Returns {'route': 'fast'|'full', 'holdouts': bool, 'reasons': [(key, vars)]}; the keys are strings.route.* in strings/ui.json."""
    if changes is None:
        return {"route": "full", "holdouts": True, "reasons": [("unknown", {})]}
    paths = [c["path"] for c in changes]
    lines = sum(c["lines"] for c in changes)
    touched_trusted = any(_hit(p, list(rules["trusted_paths"]) + list(trusted)) for p in paths)
    reasons = []
    if touched_trusted:
        reasons.append(("trusted", {}))
    if any(_hit(p, rules["full_paths"]) for p in paths):
        reasons.append(("paths", {}))
    hits = [p for p in paths if out_of_scope and _hit(p, out_of_scope)]
    if hits:
        reasons.append(("intent", {"files": len(hits)}))
    if len(paths) > rules["max_fast_files"]:
        reasons.append(("files", {"files": len(paths)}))
    if lines > rules["max_fast_lines"]:
        reasons.append(("lines", {"lines": lines}))
    route = "full" if reasons else "fast"
    if not reasons:
        reasons = [("small", {"files": len(paths), "lines": lines})]
    return {"route": route, "holdouts": route == "full" or touched_trusted, "reasons": reasons}


def git_changes(repo, since, head, timeout=30):
    """Files changed from commit `since` to `head` with lines added+removed, or None if git cannot tell (then the route is 'full')."""
    if not since:
        return None
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "diff.external=", "-C", str(repo), "diff", "--numstat", "--no-ext-diff", since, head], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode:
        return None
    out = []
    for ln in r.stdout.splitlines():
        a, _, rest = ln.partition("\t")
        d, _, path = rest.partition("\t")
        n = (int(a) + int(d)) if a.isdigit() and d.isdigit() else 10 ** 6    # a binary file counts as big
        out.append({"path": path.replace("\\", "/"), "lines": n})
    return out
