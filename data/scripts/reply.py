"""Answer the owner's whisper from a Claude session (the hook shows the exact command). Appears as the helper's reply in the
talk window and the chat. Usage: python scripts/reply.py --agent <helper id> "<answer>" """
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vanilla_agents.app.social import reply  # noqa: E402
from vanilla_agents.events.feed import Feed  # noqa: E402
from vanilla_agents.events.hooks import default_feed_path  # noqa: E402


def main(argv=None, feed_path=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agent", default="claude")
    ap.add_argument("text")
    a = ap.parse_args(argv)
    reply(Feed(feed_path or default_feed_path()), a.agent, a.text)
    print("Replied as %s." % a.agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
