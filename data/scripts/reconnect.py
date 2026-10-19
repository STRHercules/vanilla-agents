"""Keep Claude pointed at this copy of Vanilla Agents. If the hooks or the Vanilla Agents tools you installed earlier point at
another folder (the program was moved, e.g. into data/), they are re-pointed here. Nothing is added that you did not install,
and nothing happens when everything already points here. vanilla-agents.cmd runs this before every start (it is quick).

Usage: python scripts/reconnect.py [--settings PATH] [--desktop-config PATH] [--dry-run]"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import install_hooks  # noqa: E402
import install_mcp  # noqa: E402
import install_statusline  # noqa: E402


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return json.loads(text) if text.strip() else {}
    except (OSError, ValueError):
        return None


def stale_hooks(settings):
    """True when our hook entries exist but run claude_hook.py from another folder."""
    here = os.path.normcase(os.path.join(install_hooks.ROOT, "scripts", install_hooks.MARK))
    cmds = [h.get("command") or "" for evs in (settings or {}).get("hooks", {}).values() if isinstance(evs, list)
            for e in evs if isinstance(e, dict) for h in e.get("hooks", []) if isinstance(h, dict)]
    ours = [c for c in cmds if install_hooks.MARK in c]
    if not ours:
        return False
    if not all(here in os.path.normcase(c) for c in ours):
        return True
    want = install_hooks.merged({}, install_hooks.command())["hooks"]          # an older install: missing events or flags -> refresh
    have = {ev: [h for e in (settings.get("hooks", {}).get(ev) or []) if isinstance(e, dict) for h in e.get("hooks", []) if isinstance(h, dict) and install_hooks.MARK in (h.get("command") or "")] for ev in want}
    return any(not have[ev] or bool(have[ev][0].get("async")) != bool(want[ev][0]["hooks"][0].get("async")) for ev in want)


def stale_statusline(settings):
    """True when the status line runs our statusline.py from another folder (V-030; only if you installed it)."""
    sl = (settings or {}).get("statusLine")
    if not install_statusline.ours(sl):
        return False
    return os.path.normcase(os.path.join(install_hooks.ROOT, "scripts", "statusline.py")) not in os.path.normcase(str(sl.get("command")))


def stale_mcp(desktop):
    """True when the desktop app's vanilla-agents server runs vanilla_mcp.py from another folder."""
    s = ((desktop or {}).get("mcpServers") or {}).get(install_mcp.NAME)
    return bool(s) and os.path.normcase(install_mcp.SERVER) not in [os.path.normcase(a) for a in s.get("args", [])]


def main(argv=None, run=subprocess.run, which=shutil.which):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--settings", default=os.path.join(os.path.expanduser("~"), ".claude", "settings.json"))
    ap.add_argument("--desktop-config", default=install_mcp.desktop_config_path())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    fixed = []
    if stale_hooks(_load(a.settings)):
        fixed.append("hooks")
        if not a.dry_run:
            install_hooks.main(["--settings", a.settings])
    if stale_statusline(_load(a.settings)):
        fixed.append("status line")
        if not a.dry_run:
            install_statusline.main(["--settings", a.settings])
    if stale_mcp(_load(a.desktop_config)):
        fixed.append("tools")
        if not a.dry_run:
            install_mcp.write_desktop(a.desktop_config, sys.executable or "python", False, False)
            if which("claude"):      # Claude Code keeps its own copy of the path: replace it
                run(install_mcp.code_command(sys.executable or "python", True), capture_output=True, text=True)
                run(install_mcp.code_command(sys.executable or "python", False), capture_output=True, text=True)
    if fixed:
        print("Re-pointed Claude's %s at %s" % (" and ".join(fixed), install_hooks.ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
