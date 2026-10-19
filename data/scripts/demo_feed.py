"""Write a realistic FAKE event stream (3 agents, tasks being verified, one failure, one blocked) to a temp feed, so the program
window can be looked at without real agents.  Usage:  python scripts/demo_feed.py [--out FILE] [--live [--speed N]]
Default writes everything at once with past timestamps and prints the feed path. --live plays it out in real time instead."""
import argparse
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from vanilla_agents.events.feed import Feed  # noqa: E402

B, R, S = "builder", "reviewer", "scribe"


def script(agents=3, barmaid=False):
    """[(seconds_since_previous, type, task, agent, data)] - data uses only fields the event schema allows."""
    t1, t2, t3, t4, t5 = "t-move", "t-login", "t-save", "t-combat", "t-docs"
    return [
        (0, "usage_update", None, None, {"used_pct": 12, "resets_at": "Resets in 4 hours."}),
        (1, "agent_started", None, B, {"role": "builder"}),
        (1, "agent_started", None, R, {"role": "reviewer"}),
        (1, "agent_started", None, S, {"role": "scribe"}),
        (2, "task_created", t1, None, {"title": "Fix the movement code"}),
        (1, "task_created", t2, None, {"title": "Add the login screen"}),
        (1, "task_created", t3, None, {"title": "Save the game state"}),
        (1, "task_created", t4, None, {"title": "Write combat tests"}),
        (1, "task_created", t5, None, {"title": "Tidy the docs"}),
        (1, "plan_approved", t1, None, {"title": "Fix the movement code"}),
        (2, "lease_acquired", t1, B, {"resource": "file:movement.rs", "expires_at": time.time() + 900}),
        (2, "agent_working", t1, B, {"activity": "reading the movement code"}),
        (3, "tool_use", t1, B, {"tool": "Read", "file": "src/movement.rs"}),
        (4, "tool_use", t1, B, {"tool": "Edit", "file": "src/movement.rs"}),
        (4, "tool_use", t1, B, {"tool": "Bash", "command": "cargo test movement", "output_excerpt": "test result: ok. 5 passed"}),
        (3, "task_ready", t1, B, {"title": "Fix the movement code"}),
        (1, "agent_idle", None, B, {"note": "Handed in the movement fix."}),
        (1, "usage_update", None, None, {"used_pct": 27, "resets_at": "Resets in 3 hours."}),
        (2, "agent_working", t1, R, {"activity": "checking the movement fix"}),
        (0, "route_chosen", t1, None, {"route": "fast", "why": "Only a small change since the last good check (2 files, 14 lines), so just the quick checks run."}),
        (1, "check_started", t1, None, {"check": "Tests passing 5/5"}),
        (1, "check_started", t1, None, {"check": "Builds with no warnings"}),
        (4, "check_finished", t1, None, {"check": "Tests passing 5/5", "verdict": "VERIFIED"}),
        (2, "check_finished", t1, None, {"check": "Builds with no warnings", "verdict": "VERIFIED"}),
        (1, "task_verified", t1, R, {"title": "Fix the movement code"}),
        (1, "lease_expired", t1, None, {"resource": "file:movement.rs"}),
        (2, "agent_working", t2, S, {"activity": "drawing the login screen"}),
        (3, "tool_use", t2, S, {"tool": "Write", "file": "web/login.html"}),
        (3, "tool_use", t2, S, {"tool": "Write", "file": "web/login.css"}),
        (3, "task_ready", t2, S, {"title": "Add the login screen"}),
        (1, "agent_idle", None, S, {"note": ""}),
        (1, "agent_working", t2, R, {"activity": "checking the login screen"}),
        (0, "route_chosen", t2, None, {"route": "full", "why": "A build or dependency file changed, so every check runs."}),
        (1, "check_started", t2, None, {"check": "Login page opens"}),
        (1, "check_started", t2, None, {"check": "Wrong password is refused"}),
        (4, "check_finished", t2, None, {"check": "Login page opens", "verdict": "VERIFIED"}),
        (3, "check_finished", t2, None, {"check": "Wrong password is refused", "verdict": "FAILED"}),
        (0, "sent_back", t2, R, {"check": "Login page opens", "evidence": "exit code 101: a wrong password still let the player in"}),
        (1, "task_failed", t2, R, {"title": "Add the login screen", "reason": "A wrong password still let the player in."}),
        (2, "agent_working", t3, B, {"activity": "planning the save system"}),
        (3, "tool_use", t3, B, {"tool": "Grep", "command": "grep -rn save src/"}),
        (3, "task_blocked", t3, B, {"title": "Save the game state", "reason": "Waiting for you to approve changing the save format."}),
        (1, "usage_update", None, None, {"used_pct": 41, "resets_at": "Resets in 2 hours."}),
        (2, "agent_working", t4, B, {"activity": "writing combat tests"}),
        (3, "tool_use", t4, B, {"tool": "Edit", "file": "tests/combat.rs"}),
        (2, "task_ready", t4, B, {"title": "Write combat tests"}),
        (1, "check_started", t4, None, {"check": "Tests passing 8/8"}),
        (1, "agent_working", t4, R, {"activity": "running the combat tests"}),
        (1, "agent_working", t5, S, {"activity": "tidying the docs"}),
        (2, "tool_use", t5, S, {"tool": "Edit", "file": "docs/README.md"}),
        (2, "agent_idle", None, S, {"note": "Docs are tidy for now."}),
    ] + [x for k in range(4, agents + 1) for x in (
        (1, "agent_started", None, "helper%d" % k, {"role": "builder"}),
        (1, "agent_working", t5, "helper%d" % k, {"activity": "helping with the docs"}))] + (
        [(1, "agent_started", None, "barmaid", {"role": "barmaid"})] if barmaid else [])


def write(path, clock_end=None, live=False, speed=1.0, sleep=time.sleep, agents=3, barmaid=False):
    """Append the whole script to the feed at `path`. Backfilled events get past timestamps ending at clock_end (default now)."""
    s = script(agents, barmaid)
    total = sum(x[0] for x in s)
    now = [(clock_end or time.time()) - total]
    feed = Feed(path, clock=lambda: now[0])
    for dt, et, task, agent, data in s:
        if live:
            sleep(dt / speed)
            now[0] = time.time()
        else:
            now[0] += dt
        feed.append(et, task, agent, data)
    return len(s)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="feed file to write (default: a new temp folder)")
    ap.add_argument("--live", action="store_true", help="play events in real time")
    ap.add_argument("--agents", type=int, default=3, help="number of agents (3 = the usual story, more adds extra workers)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--barmaid", action="store_true", help="add a barmaid who walks between the tables (the inn, with the server's --demo)")
    a = ap.parse_args(argv)
    out = a.out or os.path.join(tempfile.mkdtemp(prefix="vanilla-demo-"), "feed.jsonl")
    n = write(out, live=a.live, speed=a.speed, agents=a.agents, barmaid=a.barmaid)
    print("wrote %d demo events to %s" % (n, out))
    print("view:  python -m vanilla_agents.app.server --feed %s --open" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
