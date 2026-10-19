"""the owner's decisions from a Claude session when he is away from the PC (for example talking to a session from his phone). D-388.
The program window only listens on 127.0.0.1, so a phone cannot press its buttons; a session running on the PC runs this instead.
Usage: python scripts/decide.py approve <quest-id> --project <name or folder>    the Yes button on a quest
       python scripts/decide.py pause <quest-id> --project <name or folder>      undo an automatic yes (until the quest is done)
       python scripts/decide.py done <quest-id> --project <name>         the quest was already finished by a normal session
       python scripts/decide.py yes <idea-key> --project <name or folder>        approve the Idea; <idea-key> is the key the Idea panel shows (it must still match)
Every decision is recorded as who = 'you (via phone session)' in the approvals file and the feed. Run it ONLY when the owner told you, in his own words, to do exactly this."""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vanilla_agents.app import idea_api, server, tiers   # noqa: E402
from vanilla_agents.app.config import load_all   # noqa: E402
from vanilla_agents.contracts import VanillaError   # noqa: E402
from vanilla_agents.onboard import setup   # noqa: E402

WHO = "you (via phone session)"


def main(argv=None, cfg=None, feed_path=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=("approve", "pause", "yes", "done"))
    ap.add_argument("target", help="quest id (approve, pause) or Idea key (yes)")
    ap.add_argument("--project", required=True, help="project name or folder")
    a = ap.parse_args(argv)
    cfg = cfg or load_all()
    app = cfg["app"]
    _, e = setup.find(setup.load_registry(os.environ.get("VANILLA_PROJECTS") or app["projects_path"]), a.project)
    if not e:
        print("%s is not a set-up project." % a.project)
        return 1
    ctx = server.Ctx(cfg, feed_path or server.default_feed(app), e["repo"], watch=False)
    try:
        if a.action == "approve":
            ctx.state.approve(a.target, who=WHO)
        elif a.action == "pause":
            tiers.veto(ctx, a.target, who=WHO)
        elif a.action == "done":
            ctx.state.mark_done_elsewhere(a.target, who=WHO)
        else:
            idea_api.approve(ctx, a.target, who=WHO)
    except (ValueError, VanillaError, OSError) as ex:
        print("Not done: %s" % ex)
        return 1
    finally:
        ctx.stop.set()
    print("Done: %s %s, recorded as %s." % (a.action, a.target, WHO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
