"""V-039: Resurrect a helper whose session went quiet or ended: open a NEW terminal window running `claude --resume <session id>` or
`codex resume <session id>` (V-053) in the folder that session worked in. The owner's own click only (POST /api/resurrect with the
window key); never an agent/MCP tool. Safety: the session id must be hex digits and dashes, the folder must exist, and every program is
started with an argument list (no shell parses our text; on Windows `cmd /c start` only ever sees the checked id). Without the provider's
command or a terminal program the caller gets a plain error plus the exact command to type."""
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


def provider_of(agent):
    """Which CLI resumes this helper: Codex watcher ids start with codex-; everything else is Claude Code."""
    return "codex" if str((agent or {}).get("id", "")).startswith("codex-") else "claude"


def copy_command(sid, cwd, platform=None, provider="claude"):
    """What to type by hand (shown when no terminal could be opened)."""
    cmd = "codex resume" if provider == "codex" else "claude --resume"
    if (platform or sys.platform).startswith("win"):
        return 'cd /d "%s" && %s %s' % (cwd.replace('"', ""), cmd, sid)
    return "cd %s && %s %s" % (shlex.quote(cwd), cmd, sid)


def _applescript_text(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def plan(sid, cwd, platform=None, which=shutil.which, provider="claude"):
    """[argv] to try in order (each started with cwd=cwd). Raises ResurrectError when nothing can work."""
    if provider not in ("claude", "codex"):
        raise ResurrectError("This helper's AI tool is not known, so the session cannot be reopened from here.")
    platform = platform or sys.platform
    cmd = copy_command(sid, cwd, platform, provider)
    program = provider
    args = ["resume", sid] if provider == "codex" else ["--resume", sid]
    exe = which(program)      # found from this server's own folder and PATH, before the terminal starts in the helper's folder
    if not exe:
        raise ResurrectError("There is no `%s` command on this PC (or it is not on the PATH), so the session cannot be reopened from here." % program, cmd)
    exe = os.path.abspath(exe)
    if _inside(exe, cwd):          # V-049: never a claude.bat / codex.cmd script that sits in the helper's own folder
        raise ResurrectError("The `%s` command found is inside the helper's own folder, so it is not started from here." % program, cmd)
    if platform.startswith("win"):
        # start "" opens a new console window; /k keeps it open after the tool exits. Only the checked id and the full path of the
        # real program reach cmd's parser (by name, cmd would look in the helper's folder first and could run a planted script).
        return [["cmd", "/c", "start", "", "cmd", "/k", exe] + args]
    if provider == "codex":
        if platform == "darwin":
            script = "cd %s && %s %s" % (shlex.quote(cwd), shlex.quote(exe), " ".join(args))
            return [["osascript", "-e", 'tell application "Terminal" to do script %s' % _applescript_text(script), "-e", 'tell application "Terminal" to activate']]
        out = [[which(t) or t] + pre + [exe] + args for t, pre in LINUX_TERMINALS if which(t)]
    else:
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


def resurrect(sid, cwd, platform=None, which=shutil.which, runner=_popen, home=None, provider="claude"):
    """Open the terminal. Returns {"ok": True, "command": <what runs>}; raises ResurrectError (with .command to copy)."""
    if provider not in ("claude", "codex"):
        raise ResurrectError("This helper's AI tool is not known, so the session cannot be reopened from here.")
    if not valid_session(sid):
        raise ResurrectError("This helper has no valid session id to resume.")
    folder = real_folder(cwd, home)
    if not folder:
        raise ResurrectError("This helper's folder is not known, so the session cannot be reopened.", copy_command(sid, "", platform, provider))
    if not os.path.isdir(folder):
        raise ResurrectError("The folder this helper worked in is gone: %s" % folder, copy_command(sid, folder, platform, provider))
    cmd = copy_command(sid, folder, platform, provider)
    last = None
    for argv in plan(sid, folder, platform, which, provider=provider):
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
