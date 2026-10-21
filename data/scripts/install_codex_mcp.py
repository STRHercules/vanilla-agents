"""Optionally register the existing Vanilla Agents tools with Codex CLI (V-053), next to Claude's registration.
Uses `codex mcp add`, never hand-edited TOML, and never overwrites a different server called vanilla-agents.
--remove only removes a registration that points at this checkout's scripts/vanilla_mcp.py.
Safe to run twice. Restart Codex afterwards.

Usage: python scripts/install_codex_mcp.py [--remove] [--dry-run]"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "vanilla-agents"
SCRIPT = Path(__file__).resolve().parent / "vanilla_mcp.py"


def command(python=None):
    return ["codex", "mcp", "add", NAME, "--", python or sys.executable, str(SCRIPT)]


def _run(cmd):
    """Run the Codex CLI. On Windows it may be a .cmd shim, which CreateProcess cannot start directly."""
    exe = shutil.which(cmd[0]) or cmd[0]
    argv = [exe] + list(cmd[1:])
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", exe] + list(cmd[1:])
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def _ours(info):
    """Whether `codex mcp get --json` describes this checkout's server (command may be stored differently by versions)."""
    if not isinstance(info, dict):
        return False
    transport = info.get("transport") if isinstance(info.get("transport"), dict) else info
    if transport.get("type", "stdio") != "stdio":
        return False
    args = transport.get("args")
    return (isinstance(args, list) and len(args) >= 1 and
            os.path.normcase(os.path.abspath(str(args[0]))) == os.path.normcase(os.path.abspath(str(SCRIPT))))


def main(argv=None, run=_run, which=shutil.which):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--remove", action="store_true", help="disconnect only this checkout's MCP registration")
    p.add_argument("--dry-run", action="store_true", help="print commands without modifying Codex settings")
    args = p.parse_args(argv)
    if not SCRIPT.is_file():
        print("Missing upstream MCP server:", SCRIPT, file=sys.stderr)
        return 1
    if not which("codex"):
        if args.remove:
            print("Codex CLI is not on PATH; nothing to disconnect.")
            return 0
        print("Codex CLI is not on PATH. Install Codex and run this script again.", file=sys.stderr)
        return 1
    try:
        found = run(["codex", "mcp", "get", NAME, "--json"])
        existing = None
        if found.returncode == 0:
            try:
                existing = json.loads(found.stdout)
            except ValueError:
                print("Existing MCP configuration cannot be read; leaving it untouched.", file=sys.stderr)
                return 1
        if args.remove:
            if not _ours(existing):
                print("Nothing to remove from this checkout.")
                return 0
            cmd = ["codex", "mcp", "remove", NAME]
        elif existing is not None:
            if _ours(existing):
                print("Codex already has Vanilla Agents MCP tools registered for this checkout.")
                return 0
            print("Codex has an MCP server named 'vanilla-agents' pointing elsewhere. Leaving it untouched.", file=sys.stderr)
            return 1
        else:
            cmd = command()
        if args.dry_run:
            print("Would run:", subprocess.list2cmdline(cmd))
            return 0
        outcome = run(cmd)
        msg = (outcome.stdout or outcome.stderr or "").strip()
        if outcome.returncode:
            print(msg or "Codex MCP registration failed.", file=sys.stderr)
            return 1
        print(msg or ("Disconnected Codex MCP tools." if args.remove else "Connected Codex to Vanilla Agents MCP tools."))
        return 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("Codex MCP CLI failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
