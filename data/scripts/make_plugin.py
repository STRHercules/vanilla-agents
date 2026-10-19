"""Write the Claude Code plugin in data/plugin/ (V-031) from the same lists the hook installer uses, so the two never drift:
.claude-plugin/plugin.json + marketplace.json (a one-plugin local marketplace), hooks/hooks.json (install_hooks.EVENTS / ASYNC) and
.mcp.json (the Vanilla Agents tools, scripts/vanilla_mcp.py). The plugin runs the scripts in data/scripts/ through
${CLAUDE_PLUGIN_ROOT}/.., so install it from this folder (it loads in place); see docs/PLUGIN.md.

Usage: python scripts/make_plugin.py [--python NAME] [--check]
  --python  the Python command Claude starts the tools with (default "python"; use python3 on macOS/Linux)
  --check   only say whether the files are up to date (exit 1 if not)"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import install_hooks  # noqa: E402

PLUGIN_DIR = os.path.join(ROOT, "plugin")
NAME = "vanilla-agents"
MARKET = "vanilla-agents-local"
SCRIPTS = "${CLAUDE_PLUGIN_ROOT}/../scripts/"


def _version():
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "0"
    except OSError:
        return "0"


def hook_command():
    """python3 first, python when there is no python3 (the hook script always exits 0, so the second only runs when the first is missing)."""
    s = '"%sclaude_hook.py"' % SCRIPTS
    return "python3 %s || python %s" % (s, s)


def files(python="python"):
    """{relative path under plugin/: JSON-able data}."""
    hooks = {}
    for ev in install_hooks.EVENTS:
        h = {"type": "command", "command": hook_command(), "timeout": 10}
        if ev in install_hooks.ASYNC:
            h["async"] = True
        e = {"hooks": [h]}
        if ev in install_hooks.WITH_MATCHER:
            e = {"matcher": "*", **e}
        hooks[ev] = [e]
    desc = "Shows your Claude Code sessions as heroes in the Vanilla Agents inn: session hooks for the event feed and the Vanilla Agents tools (quests, progress, requests)."
    return {
        ".claude-plugin/plugin.json": {"name": NAME, "displayName": "Vanilla Agents", "version": _version(), "description": desc,
                                       "author": {"name": "Vanilla Agents"}, "keywords": ["dashboard", "hooks", "mcp"]},
        ".claude-plugin/marketplace.json": {"name": MARKET, "owner": {"name": "Vanilla Agents"}, "description": "Local marketplace for the Vanilla Agents plugin in this folder.",
                                            "plugins": [{"name": NAME, "source": "./", "description": desc}]},
        "hooks/hooks.json": {"hooks": hooks},
        ".mcp.json": {"mcpServers": {NAME: {"command": python, "args": [SCRIPTS + "vanilla_mcp.py"]}}},
    }


def render(data):
    return json.dumps(data, indent=2) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--python", default="python")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    stale = []
    for rel, data in files(a.python).items():
        p = os.path.join(PLUGIN_DIR, *rel.split("/"))
        try:
            with open(p, encoding="utf-8") as f:
                same = f.read() == render(data)
        except OSError:
            same = False
        if same:
            continue
        stale.append(rel)
        if not a.check:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(render(data))
    if a.check:
        print("plugin files are up to date" if not stale else "out of date: " + ", ".join(stale))
        return 1 if stale else 0
    print("wrote " + ", ".join(stale) if stale else "plugin files were already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
