"""Give Claude the Vanilla Agents tools (MCP server scripts/vanilla_mcp.py):
- Claude desktop app: adds "vanilla-agents" to claude_desktop_config.json (with a timestamped backup). Restart the app afterwards.
  Cloud sessions linked to this PC through the desktop app can then use it too.
- Claude Code: runs `claude mcp add --scope user vanilla-agents ...` when the `claude` command exists (otherwise prints it).
Safe to run twice; --remove takes it out again.

Usage: python scripts/install_mcp.py [--desktop-config PATH] [--no-desktop] [--no-code] [--remove] [--dry-run]"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "vanilla-agents"
SERVER = os.path.join(ROOT, "scripts", "vanilla_mcp.py")


def desktop_config_path(env=os.environ, platform=sys.platform):
    if platform.startswith("win"):
        return os.path.join(env.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming"), "Claude", "claude_desktop_config.json")
    if platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
    return os.path.expanduser("~/.config/Claude/claude_desktop_config.json")


def merged(cfg, python, remove=False):
    out = json.loads(json.dumps(cfg))
    servers = out.setdefault("mcpServers", {})
    if remove:
        servers.pop(NAME, None)
    else:
        servers[NAME] = {"command": python, "args": [SERVER]}
    if not servers:
        out.pop("mcpServers")
    return out


def write_desktop(path, python, remove, dry):
    cur = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        try:
            cur = json.loads(text) if text.strip() else {}
        except ValueError as e:
            print("The desktop config %s is not valid JSON (%s); nothing changed." % (path, e))
            return 1
    new = merged(cur, python, remove)
    if dry:
        print(json.dumps(new, indent=2))
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(new, f, indent=2)
        f.write("\n")
    os.replace(path + ".tmp", path)
    print(("Removed from" if remove else "Added to") + " the Claude desktop app: " + path + " (restart the app to load it)")
    return 0


def code_command(python, remove):
    return ["claude", "mcp", "remove", "--scope", "user", NAME] if remove else ["claude", "mcp", "add", "--scope", "user", NAME, "--", python, SERVER]


def main(argv=None, run=subprocess.run, which=shutil.which):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desktop-config", default=desktop_config_path())
    ap.add_argument("--no-desktop", action="store_true")
    ap.add_argument("--no-code", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    py = sys.executable or "python"
    rc = 0
    if not a.no_desktop:
        rc |= write_desktop(a.desktop_config, py, a.remove, a.dry_run)
    if not a.no_code:
        cmd = code_command(py, a.remove)
        if a.dry_run or not which("claude"):
            print("Claude Code: run  " + " ".join('"%s"' % c if " " in c else c for c in cmd))
        else:
            r = run(cmd, capture_output=True, text=True)
            out = (r.stdout or r.stderr or "").strip()
            print("Claude Code: " + (out or ("done" if r.returncode == 0 else "failed")))
            if r.returncode != 0 and "already exists" not in out:
                rc |= 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
