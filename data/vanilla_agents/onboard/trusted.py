"""Build one trusted folder (checks.json, protected.json, optional holdout.json, pinned MANIFEST.json) and return its manifest hash.
Moved here from scripts/make_trusted.py (D-331) so the program window can onboard a project too; the script is now a thin wrapper."""
import json
import os

from ..verify.manifest import manifest_hash, write_manifest

PROTECTED = ["tests/**", "scripts/**"]   # default when a project declares none (onboard/detect.py proposes per-type globs)


def make_trusted(dest, extra_checks=(), holdouts=(), checks=None, protected=None):
    """A project passes its own `checks` (default: none) and `protected` (default: PROTECTED). extra_checks is for tests/dry runs. Holdouts are hidden checks (D-290): pinned, never read by the app."""
    checks = list(checks or []) + list(extra_checks)
    os.makedirs(dest, exist_ok=True)
    files = [("checks.json", checks), ("protected.json", list(PROTECTED if protected is None else protected))] + ([("holdout.json", list(holdouts))] if holdouts else [])
    for name, data in files:
        with open(os.path.join(dest, name), "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, sort_keys=True, indent=1)
    return manifest_hash(write_manifest(dest, [n for n, _ in files]))
