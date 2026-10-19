"""Create a Windows shortcut (.lnk) to vanilla-agents.cmd via PowerShell WScript.Shell.
Usage: python scripts/make_shortcut.py [--lnk PATH]   (default: vanilla-agents.lnk on the Desktop)
Nothing is created unless you run this yourself; the tests only use a temp folder."""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.expanduser(os.path.join("~", ".vanilla-agents", "assets", "vanilla-agents.ico"))      # made by scripts/make_icon.py; skipped when missing
PS = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:FC_LNK);"
      "$s.TargetPath=$env:FC_TARGET;$s.WorkingDirectory=$env:FC_DIR;$s.WindowStyle=7;"
      "$s.Description='vanilla-agents';if($env:FC_ICON -and (Test-Path $env:FC_ICON)){$s.IconLocation=$env:FC_ICON};$s.Save()")


def desktop_lnk(env=os.environ):
    return os.path.join(env.get("USERPROFILE", "."), "Desktop", "vanilla-agents.lnk")


def make_shortcut(lnk, target=None, run=subprocess.run):
    """Create the .lnk. Paths travel in environment variables, never inside the PowerShell text (no quoting problems)."""
    target = target or os.path.join(os.path.dirname(ROOT), "vanilla-agents.cmd")      # the launcher sits one level up, next to data/
    env = dict(os.environ, FC_LNK=str(lnk), FC_TARGET=str(target), FC_DIR=os.path.dirname(str(target)) or ".", FC_ICON=ICON)
    r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS], env=env, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(lnk):
        raise RuntimeError("could not create the shortcut: %s" % (r.stderr or r.stdout).strip())
    return lnk


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lnk", default=None, help="where to put the shortcut (default: Desktop)")
    a = ap.parse_args(argv)
    print("created " + make_shortcut(a.lnk or desktop_lnk()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
