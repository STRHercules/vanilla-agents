"""Opt-in (V-030): let Claude Code's status line send the real numbers (conversation memory used, session cost, lines changed,
5-hour / weekly plan limits) to Vanilla Agents. Sets "statusLine" in YOUR Claude Code settings (default ~/.claude/settings.json) to
scripts/statusline.py, keeps a timestamped backup next to it, and saves the status line you had before so it keeps showing
(our script runs it and prints its output). Safe to run twice. --remove puts your old status line back.

Usage: python scripts/install_statusline.py [--settings PATH] [--store PATH] [--remove] [--dry-run]"""
import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from vanilla_agents.events.hooks import default_feed_path  # noqa: E402
from vanilla_agents.events.statusline import MARK, STORE_NAME  # noqa: E402


def command(python=None):
    py = python or sys.executable or "python"
    return '"%s" "%s"' % (py, os.path.join(ROOT, "scripts", MARK))


def default_store():
    return os.path.join(os.path.dirname(os.path.abspath(default_feed_path())), STORE_NAME)


def ours(sl):
    return isinstance(sl, dict) and MARK in str(sl.get("command") or "")


def merged(settings, store, cmd, remove=False):
    """(new settings, new store) for install or remove. store = {"previous": <old statusLine or None>}."""
    out = json.loads(json.dumps(settings))
    store = dict(store or {})
    cur = out.get("statusLine")
    if remove:
        if ours(cur):
            prev = store.get("previous")
            if isinstance(prev, dict) and prev.get("command"):
                out["statusLine"] = prev
            else:
                out.pop("statusLine", None)
        store.pop("previous", None)
        return out, store
    if not ours(cur):        # first install (or you changed it since): remember what you had
        store["previous"] = cur if isinstance(cur, dict) else None
    new = {"type": "command", "command": cmd}
    prev = store.get("previous")
    for k in ("padding", "refreshInterval"):      # keep your own spacing / timer
        src = cur if ours(cur) else prev
        if isinstance(src, dict) and k in src:
            new[k] = src[k]
    out["statusLine"] = new
    return out, store


def _read(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return json.loads(text) if text.strip() else {}


def _write(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--settings", default=os.path.join(os.path.expanduser("~"), ".claude", "settings.json"))
    ap.add_argument("--store", default=None, help="where your old status line is kept (default: statusline.json next to the feed)")
    ap.add_argument("--remove", action="store_true", help="put your old status line back")
    ap.add_argument("--dry-run", action="store_true", help="print the result, change nothing")
    a = ap.parse_args(argv)
    store_path = a.store or default_store()
    try:
        cur = _read(a.settings)
    except ValueError as e:
        print("Your settings file is not valid JSON (%s); fix it first, nothing was changed." % e)
        return 1
    try:
        store = _read(store_path)
    except ValueError:
        store = {}
    new, new_store = merged(cur, store, command(), a.remove)
    if a.dry_run:
        print(json.dumps(new.get("statusLine"), indent=2))
        return 0
    if os.path.exists(a.settings):
        shutil.copy2(a.settings, a.settings + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    _write(store_path, new_store)
    _write(a.settings, new)
    print(("Removed the Vanilla Agents status line (yours is back) in " if a.remove else "Installed the Vanilla Agents status line in ") + a.settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
