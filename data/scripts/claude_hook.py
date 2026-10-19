"""Claude Code hook entry point: reads the hook JSON on stdin, appends events to the feed, always exits 0. See docs/HOOKS_SETUP.md."""
import os
import sys

try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from vanilla_agents.events.hooks import main
    code = main()
except BaseException:
    code = 0
sys.exit(0)
