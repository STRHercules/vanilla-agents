"""Read-only list of the project's checks for the spellbook (D-241). Ids, plain-language labels, repeatability and expected duration only:
never the command, folders, environment or fingerprint from the trusted folder."""
import json
from pathlib import Path

from . import runs


def _defs(trusted_dir):
    try:
        d = json.loads((Path(trusted_dir) / "checks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return {c["check_id"]: c for c in d if isinstance(c, dict) and isinstance(c.get("check_id"), str)} if isinstance(d, list) else {}


def label(ui, cid):
    if cid.startswith("hidden-"):   # D-292: a holdout shows as one unnamed line
        return str(ui.get("check.hidden", "A hidden check"))
    t = ui.get("check." + cid)
    return t if isinstance(t, str) else str(ui.get("check.default", "{check}")).replace("{check}", cid)


def check_list(entry, cfg, run_list):
    """entry = the project's registry entry (or None). Registered checks (entry criteria) come first, then other defined checks."""
    a, ui = cfg["app"], cfg["strings"]["ui"]
    if not entry:
        return {"checks": [], "problem": "no_project"}
    defs = _defs(entry.get("trusted_dir", ""))
    crit = [c for c in entry.get("criteria", []) if isinstance(c, str)]
    last = runs.last_by_check(run_list)
    out = []
    for cid in crit + [c for c in defs if c not in crit]:
        rep = defs.get(cid, {}).get("repeatability", "repeatable")
        out.append({"id": cid, "label": label(ui, cid), "repeatability": rep if isinstance(rep, str) else "repeatable",
                    "expected_seconds": a["verify_check_seconds"].get(cid, a["verify_default_seconds"]), "registered": cid in crit, "last": last.get(cid)})
    for cid in sorted(c for c in last if isinstance(c, str) and c.startswith("hidden-")):   # holdouts are known only from past runs (the app never reads holdout.json)
        out.append({"id": cid, "label": label(ui, cid), "repeatability": "repeatable", "expected_seconds": a["verify_default_seconds"], "registered": False, "last": last[cid]})
    return {"checks": out, "problem": ""}
