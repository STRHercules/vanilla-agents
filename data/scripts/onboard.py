"""One-command onboarding: make any git repo ready for Verify now.
Usage: python scripts/onboard.py <repo> [--name short-name]   detect the project type, create <repo>/state/db, build ~/.vanilla-agents/trusted/<name>, write the registry entry
       python scripts/onboard.py --regen [project]            after changing the checker code: rebuild every project's (or one project's) trusted folder, record new fingerprints
Registry and trusted root come from app.json (projects_path, trusted_root). Prints only plain facts; never hidden-check content."""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vanilla_agents.app.config import load_all   # noqa: E402
from vanilla_agents.contracts import VanillaError   # noqa: E402
from vanilla_agents.onboard import setup   # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--name")
    ap.add_argument("--regen", action="store_true")
    ap.add_argument("--registry")
    ap.add_argument("--trusted-root")
    a = ap.parse_args(argv)
    app = load_all()["app"]
    reg = a.registry or os.environ.get("VANILLA_PROJECTS") or app["projects_path"]
    try:
        if a.regen:
            r = setup.load_registry(reg)
            hit = setup.find(r, a.repo)[0] if a.repo else None
            if a.repo and not hit:
                sys.exit("%s is not registered." % a.repo)
            for k in ([hit] if hit else list(r)):
                print("%s: new fingerprint %s" % (k, setup.rebuild(reg, k)))
            return 0
        if not a.repo:
            ap.error("give the repo folder, or --regen")
        s = setup.onboard(a.repo, reg, a.trusted_root or app["trusted_root"], a.name, app.get("ideas_root"))
    except VanillaError as e:
        print("Could not set up: %s" % e)
        return 1
    print("%s %s (%s). Checks: %s." % ("Already set up:" if s["already"] else "Set up:", s["name"], s["type"] or "registered earlier", ", ".join(s["checks"]) or "none found; agents work, nothing is auto-checked"))
    if s["drafted"]:
        print("Drafted the Idea (goal, not building, done means). Open the Idea panel (I) to read and approve it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
