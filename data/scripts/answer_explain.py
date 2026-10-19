"""Answer the owner's 'Explain it more' (or record a finished 'Change it') for a quest. D-386.
Usage: python scripts/answer_explain.py <quest-id> "<plain answer>"            -> explain_answered, shown under the quest in the Quest Log
       python scripts/answer_explain.py <quest-id> --changed "<what changed>"  -> change_answered, clears the open change request
Writes one line to the event feed (VANILLA_FEED, else the .vanilla-agents folder in your user profile). No model call, nothing else is touched."""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vanilla_agents.events.feed import Feed   # noqa: E402
from vanilla_agents.events.hooks import default_feed_path   # noqa: E402


def title_of(feed, quest):
    """The quest's title as the feed knows it (the newest event of that quest that carries one), else the id."""
    title = quest
    for ev in feed.read_since(0, 100000):
        if ev.task == quest and ev.data.get("title") and ev.type not in ("explain_answered", "change_answered"):
            title = ev.data["title"]
    return title


def main(argv=None, feed_path=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("quest")
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--changed", action="store_true", help="record that the requested change was made instead of an explanation")
    a = ap.parse_intermixed_args(argv)   # the text may come after --changed
    if not a.changed and not a.text.strip():
        print("Give the answer in words: answer_explain.py <quest-id> \"<answer>\"")
        return 1
    feed = Feed(feed_path or default_feed_path())
    data = {"title": title_of(feed, a.quest), "text": a.text.strip()[:1500]}
    if a.changed and not data["text"]:
        del data["text"]
    feed.append("change_answered" if a.changed else "explain_answered", task=a.quest, data=data)
    print("Recorded for quest %s." % a.quest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
