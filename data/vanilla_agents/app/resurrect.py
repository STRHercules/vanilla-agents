"""V-039: Resurrect a helper whose Claude session went quiet or ended: open a NEW terminal window running `claude --resume <session id>`
in the folder that session worked in. The owner's own click only (POST /api/resurrect with the window key); never an agent/MCP tool.
Safety: the session id must look like a Claude session id (hex digits and dashes), the folder must exist, and every program is started
with an argument list (no shell parses our text; on Windows `cmd /c start` only ever sees the checked id). Without a `claude` command or a
terminal program the caller gets a plain error plus the exact command to type."""
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SESSION_RE = re.compile(r"^[0-9A-Fa-f]{8}(-?[0-9A-Fa-f]{2,12}){0,6}\Z")      # \Z, not $: "$" also matches before a trailing newline (V-048)
# Linux terminals, tried in order: (program, arguments before the command)
LINUX_TERMINALS = (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]),
                   ("mate-terminal", ["-x"]), ("kitty", []), ("alacritty", ["-e"]), ("xterm", ["-e"]))


class ResurrectError(Exception):
    def __init__(self, message, command=""):
        super().__init__(message)
        self.command = command


def valid_session(sid):
    return isinstance(sid, str) and len(sid) <= 64 and bool(SESSION_RE.match(sid))


def real_folder(cwd, home=None):
    """The feed hides your user name in paths ('/home/[user]/...'); the session ran on this PC as you, so put it back."""
    cwd = str(cwd or "")
    if "[user]" in cwd:
        cwd = cwd.replace("[user]", re.split(r"[\\/]", str(home or Path.home()).rstrip("\\/"))[-1])
    return cwd


def copy_command(sid, cwd, platform=None):
    """What to type by hand (shown when no terminal could be opened)."""
    if (platform or sys.platform).startswith("win"):
        return 'cd /d "%s" && claude --resume %s' % (cwd.replace('"', ""), sid)
    return "cd %s && claude --resume %s" % (shlex.quote(cwd), sid)


def _applescript_text(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def plan(sid, cwd, platform=None, which=shutil.which):
    """[argv] to try in order (each started with cwd=cwd). Raises ResurrectError when nothing can work."""
    platform = platform or sys.platform
    cmd = copy_command(sid, cwd, platform)
    claude = which("claude")      # found from this server's own folder and PATH, before the terminal starts in the helper's folder
    if not claude:
        raise ResurrectError("There is no `claude` command on this PC (or it is not on the PATH), so the session cannot be reopened from here.", cmd)
    claude = os.path.abspath(claude)
    if _inside(claude, cwd):          # V-049: never a claude.bat / claude script that sits in the helper's own folder
        raise ResurrectError("The `claude` command found is inside the helper's own folder, so it is not started from here.", cmd)
    if platform.startswith("win"):
        # start "" opens a new console window; /k keeps it open after Claude exits. Only the checked id and the full path of the
        # real claude program reach cmd's parser (by name, cmd would look in the helper's folder first and could run a planted claude.bat).
        return [["cmd", "/c", "start", "", "cmd", "/k", claude, "--resume", sid]]
    if platform == "darwin":
        script = "cd %s && claude --resume %s" % (shlex.quote(cwd), sid)
        return [["osascript", "-e", 'tell application "Terminal" to do script %s' % _applescript_text(script), "-e", 'tell application "Terminal" to activate']]
    out = [[which(t) or t] + pre + ["claude", "--resume", sid] for t, pre in LINUX_TERMINALS if which(t)]
    if not out:
        raise ResurrectError("No terminal program was found (tried %s)." % ", ".join(t for t, _ in LINUX_TERMINALS), cmd)
    return out


def _inside(path, folder):
    try:
        p, f = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    except (TypeError, ValueError):
        return False
    return bool(folder) and (p == f or p.startswith(f.rstrip("\\/") + os.sep))


def _popen(argv, cwd):
    kw = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name != "nt":
        kw["start_new_session"] = True       # the terminal outlives this server
    subprocess.Popen(argv, **kw)


def resurrect(sid, cwd, platform=None, which=shutil.which, runner=_popen, home=None):
    """Open the terminal. Returns {"ok": True, "command": <what runs>}; raises ResurrectError (with .command to copy)."""
    if not valid_session(sid):
        raise ResurrectError("This helper has no Claude session id to resume (it was not started by Claude Code with the hooks).")
    folder = real_folder(cwd, home)
    if not folder:
        raise ResurrectError("This helper's folder is not known, so the session cannot be reopened.", "claude --resume %s" % sid)
    if not os.path.isdir(folder):
        raise ResurrectError("The folder this helper worked in is gone: %s" % folder, "claude --resume %s" % sid)
    cmd = copy_command(sid, folder, platform)
    last = None
    for argv in plan(sid, folder, platform, which):
        try:
            runner(argv, folder)
            return {"ok": True, "command": cmd}
        except OSError as e:
            last = e
    raise ResurrectError("The terminal could not be opened (%s)." % last, cmd)


def find(snapshot, agent_id):
    """The helper (present or departed) with its session id and folder, from a state snapshot; None if unknown."""
    for a in list(snapshot.get("agents", [])) + list(snapshot.get("departed", [])):
        if a.get("id") == agent_id:
            return a
    return None
