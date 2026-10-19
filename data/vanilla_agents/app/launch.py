"""Opens the program window: a chromeless Chrome (or Edge if Chrome is missing) with its OWN profile, never the owner's."""
import os
import subprocess
from pathlib import Path


def candidates(env=os.environ):
    pf, pf86, local = env.get("ProgramFiles", ""), env.get("ProgramFiles(x86)", ""), env.get("LOCALAPPDATA", "")
    chrome = [Path(b) / "Google/Chrome/Application/chrome.exe" for b in (pf, pf86, local) if b]
    edge = [Path(b) / "Microsoft/Edge/Application/msedge.exe" for b in (pf86, pf) if b]
    return [("chrome", p) for p in chrome] + [("edge", p) for p in edge]


def find_browser(env=os.environ, exists=os.path.isfile):
    """First installed (kind, path): Chrome first, Edge only as a fallback. None if neither exists."""
    for kind, p in candidates(env):
        if exists(str(p)):
            return kind, str(p)
    return None


def build_cmd(exe, url, profile_dir, width, height):
    return [exe, "--app=" + url, "--user-data-dir=" + str(profile_dir), "--window-size=%d,%d" % (width, height),
            "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check"]


def open_window(url, cfg_app, find=find_browser, popen=subprocess.Popen):
    """Start the window; returns the Popen. Raises RuntimeError with a plain message when no browser is installed."""
    found = find()
    if not found:
        raise RuntimeError("Neither Google Chrome nor Microsoft Edge was found, so the program window cannot open. Install Chrome, or open %s in a browser." % url)
    w = cfg_app["window"]
    return popen(build_cmd(found[1], url, cfg_app["browser_profile_dir"], w["width"], w["height"]))
