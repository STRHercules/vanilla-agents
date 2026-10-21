"""One-time setup on a new PC: point Vanilla Agents at your WoW 1.12 Data folder, bake the game art, and (if you say so)
connect Claude Code and put a shortcut on the Desktop.

Usage: python scripts/setup.py --data-dir "C:\\path\\to\\Data" [--hooks] [--statusline] [--shortcut]"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(ROOT, "vanilla.local.json")


def write_local(data_dir, path=LOCAL):
    cur = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cur = json.load(f)
    cur["data_dir"] = data_dir
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
        f.write("\n")
    return cur


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True, help="the client's Data folder (the one with patch.MPQ)")
    ap.add_argument("--hooks", action="store_true", help="also connect Claude Code (edits your Claude settings, with a backup)")
    ap.add_argument("--statusline", action="store_true", help="also let Claude Code's status line send real usage (cost, memory, plan limits); keeps your old status line")
    ap.add_argument("--shortcut", action="store_true", help="also put a Vanilla Agents shortcut on the Desktop (Windows)")
    ap.add_argument("--mcp", action="store_true", help="also give Claude the Vanilla Agents tools (desktop app config + Claude Code), with a backup")
    a = ap.parse_args(argv)
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is needed (this is %d.%d)." % sys.version_info[:2])
        return 1
    if not os.path.isdir(a.data_dir) or not any(f.lower().endswith(".mpq") for f in os.listdir(a.data_dir)):
        print("No .MPQ files in %s. Point --data-dir at the folder that holds patch.MPQ." % a.data_dir)
        return 1
    write_local(os.path.abspath(a.data_dir))
    print("Saved your Data folder in vanilla.local.json. Baking the game art (about a minute)...")
    py = sys.executable
    steps = [[py, "-m", "vanilla_agents.assets", "bake", "all"], [py, "-m", "vanilla_agents.app.check"], [py, "scripts/make_icon.py"]]
    if a.hooks:
        steps.append([py, "scripts/install_hooks.py"])
    if a.statusline:
        steps.append([py, "scripts/install_statusline.py"])
    if a.mcp:
        steps.append([py, "scripts/install_mcp.py"])
    if a.shortcut:
        steps.append([py, "scripts/make_shortcut.py"])
    for cmd in steps:
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            print("Stopped: '%s' did not finish. Nothing else was changed." % " ".join(cmd[1:]))
            return 1
    print("Done. Start it with vanilla-agents.cmd (or the Desktop shortcut).")
    print("Using Codex CLI? Its sessions show up automatically; run python scripts/install_codex_mcp.py (connect-codex-mcp.cmd on Windows) once to give it the same quest tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
