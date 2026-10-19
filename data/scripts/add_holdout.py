"""Add hidden (holdout) checks to one project. Usage: python scripts/add_holdout.py <project path or name> <defs.json>
defs.json = one check definition or a list (same format as checks.json). The content goes ONLY into that project's trusted folder (holdout.json, pinned in its manifest);
the project's fingerprint in the registry is regenerated. This script prints only the count and the new fingerprint, never the content (D-290..D-293)."""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vanilla_agents.app.config import load_all   # noqa: E402
from vanilla_agents.contracts import VanillaError   # noqa: E402
from vanilla_agents.onboard import setup   # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("defs")
    ap.add_argument("--registry")
    a = ap.parse_args(argv)
    reg = a.registry or os.environ.get("VANILLA_PROJECTS") or load_all()["app"]["projects_path"]
    try:
        with open(a.defs, encoding="utf-8") as f:
            r = setup.add_holdout(reg, a.project, json.load(f))
    except (OSError, ValueError, VanillaError) as e:
        print("Could not add: %s" % e)
        return 1
    print("%d hidden check(s) now kept for %s. New fingerprint: %s" % (r["holdouts"], a.project, r["hash"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
