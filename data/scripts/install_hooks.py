"""Connect Claude Code to Vanilla Agents: add the hook entries that send session events to the program's feed.
Edits YOUR Claude Code settings (default %USERPROFILE%\\.claude\\settings.json), keeps a timestamped backup next to it,
leaves every other setting and hook untouched, and is safe to run twice. --remove takes our entries out again.

Usage: python scripts/install_hooks.py [--settings PATH] [--remove] [--dry-run]"""
import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Notification", "Stop", "SubagentStop", "PermissionRequest", "StopFailure", "SessionEnd",
          "PostToolUseFailure", "SubagentStart", "PreCompact", "PostCompact", "TaskCreated", "TaskCompleted", "CwdChanged"]
# V-026: hooks that only report run in the background ("async") so they never slow a Claude session down; SessionStart and
# UserPromptSubmit print the owner's requests and Stop may hand over whispers, so those three run in step
ASYNC = {"PreToolUse", "PostToolUse", "Notification", "SubagentStop", "PermissionRequest", "StopFailure", "SessionEnd",
         "PostToolUseFailure", "SubagentStart", "PreCompact", "PostCompact", "TaskCreated", "TaskCompleted", "CwdChanged"}
WITH_MATCHER = {"PreToolUse", "PostToolUse"}
MARK = "claude_hook.py"


def command(python=None):
    py = python or sys.executable or "python"
    return '"%s" "%s"' % (py, os.path.join(ROOT, "scripts", MARK))


def _ours(entry):
    return any(MARK in (h.get("command") or "") for h in entry.get("hooks", []) if isinstance(h, dict))


def merged(settings, cmd, remove=False):
    """A copy of the settings dict with our hook entries added (or removed). Other entries are kept in order."""
    out = json.loads(json.dumps(settings))
    hooks = out.setdefault("hooks", {})
    for ev in EVENTS:
        kept = [e for e in hooks.get(ev, []) if not (isinstance(e, dict) and _ours(e))]
        if not remove:
            h = {"type": "command", "command": cmd, "timeout": 10}
            if ev in ASYNC:
                h["async"] = True
            e = {"hooks": [h]}
            if ev in WITH_MATCHER:
                e = {"matcher": "*", **e}
            kept.append(e)
        if kept:
            hooks[ev] = kept
        else:
            hooks.pop(ev, None)
    if not hooks:
        out.pop("hooks")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--settings", default=os.path.join(os.path.expanduser("~"), ".claude", "settings.json"))
    ap.add_argument("--remove", action="store_true", help="take the Vanilla Agents entries out")
    ap.add_argument("--dry-run", action="store_true", help="print the result, change nothing")
    a = ap.parse_args(argv)
    cur = {}
    if os.path.exists(a.settings):
        with open(a.settings, encoding="utf-8") as f:
            text = f.read()
        try:
            cur = json.loads(text) if text.strip() else {}
        except ValueError as e:
            print("Your settings file is not valid JSON (%s); fix it first, nothing was changed." % e)
            return 1
    new = merged(cur, command(), a.remove)
    if a.dry_run:
        print(json.dumps(new, indent=2))
        return 0
    os.makedirs(os.path.dirname(a.settings), exist_ok=True)
    if os.path.exists(a.settings):
        shutil.copy2(a.settings, a.settings + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    tmp = a.settings + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=2)
        f.write("\n")
    os.replace(tmp, a.settings)
    print(("Removed" if a.remove else "Installed") + " the Vanilla Agents hooks in " + a.settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
