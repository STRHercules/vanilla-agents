"""Claude Code status line entry point (V-030): sends the session's real numbers (context use, cost, plan limits) to the
Vanilla Agents feed, then prints your old status line (or a short default). Always exits 0. Install: scripts/install_statusline.py."""
import os
import sys

try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from vanilla_agents.events.statusline import main
    main()
except BaseException:
    pass
sys.exit(0)
