"""Help > Save diagnostics: one plain-text file you can read before sending it to anyone. What is in it: version, Python and
Windows/macOS/Linux version, settings problems, which baked art is present, whether Claude's hooks and tools point here, party
status (joined / hosting, never keys), counts from the event feed, and the window's last errors. What is NOT in it: keys, invite
links, your code, file contents, quest text or chat."""
import json
import os
import platform
import sys
import time
from pathlib import Path

from .config import ROOT

VERSION_FILE = ROOT / "VERSION"
MAX_CLIENT_LOG = 200_000


def version():
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


def log_client_error(work_dir, entry):
    """Append one browser error (message, where, when) to client-errors.log; keeps the file small."""
    path = Path(work_dir) / "client-errors.log"
    line = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **{k: str(entry.get(k, ""))[:500] for k in ("message", "source", "line", "stack")}})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_CLIENT_LOG:
            keep = path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
            path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _assets(assets_dir):
    a = Path(assets_dir)
    have = {
        "ui icons": (a / "interface" / "icons").is_dir(),
        "fonts": (a / "fonts").is_dir(),
        "heroes": (a / "models" / "workers" / "manifest.json").exists(),
        "inn": (a / "models" / "inn.glb").exists(),
        "buildings": (a / "models" / "buildings" / "index.json").exists(),
        "music": (a / "sound" / "music" / "manifest.json").exists(),
        "voices": (a / "sound" / "voices" / "manifest.json").exists(),
        "skin": (a / "skin").is_dir(),
    }
    return have


def _claude_links():
    out = {}
    try:
        with open(os.path.join(os.path.expanduser("~"), ".claude", "settings.json"), encoding="utf-8") as f:
            s = json.load(f)
        cmds = [h.get("command", "") for evs in s.get("hooks", {}).values() if isinstance(evs, list) for e in evs if isinstance(e, dict) for h in e.get("hooks", []) if isinstance(h, dict)]
        ours = [c for c in cmds if "claude_hook.py" in c]
        out["hooks"] = "installed, pointing here" if ours and all(str(ROOT) in c for c in ours) else "installed, pointing elsewhere (start vanilla-agents.cmd to fix)" if ours else "not installed"
    except (OSError, ValueError):
        out["hooks"] = "no Claude Code settings found"
    return out


def report(ctx):
    a = ctx.cfg["app"]
    lines = ["Vanilla Agents diagnostics", "=" * 26, "made: " + time.strftime("%Y-%m-%d %H:%M:%S"),
             "version: " + version(), "python: " + sys.version.split()[0], "system: " + platform.platform(), ""]
    from .check_app import check_app
    probs = check_app(ctx.cfg)
    lines += ["settings problems: " + (str(len(probs)) if probs else "none")] + ["  - " + p for p in probs[:20]]
    lines += ["", "baked art (assets folder %s):" % ("set" if a.get("assets_dir") else "default")]
    lines += ["  %-10s %s" % (k, "yes" if v else "MISSING (run setup.cmd, or: python -m vanilla_agents.assets bake)") for k, v in _assets(ctx.assets_dir).items()]
    lines += ["", "Claude:"] + ["  %s: %s" % kv for kv in _claude_links().items()]
    try:
        snap = ctx.state.snapshot()
        lines += ["", "now: %d helpers, %d quests, project open: %s" % (len(snap["agents"]), len(snap["tasks"]), "yes" if ctx.state.project else "no")]
    except Exception as e:          # a broken state is exactly what this file is for
        lines += ["", "state: could not read (%s)" % e]
    pv = ctx.party.view() if getattr(ctx, "party", None) else {}
    lines += ["party: " + ("hosting" if pv.get("hosting") else "joined" if pv.get("joined") else "not in a party") + ((" - last error: " + pv["error"]) if pv.get("error") else "")]
    try:          # V-044: on/off, how, item counts and the last error only; never tokens, keys, e-mail, site or item text
        hub = ctx.integ
        s, st, n = hub.settings(), hub.status, hub.board().counts()
        how = lambda k: ("on (%s)" % (s["github"]["mode"] if k == "github" else "api")) if s[k]["enabled"] else "off"
        lines += ["integrations: " + ", ".join("%s %s, %d imported%s" % (k, how(k), n[k], " - last error: " + st[k]["error"][:120] if st[k].get("error") else "") for k in ("github", "linear", "jira"))]
    except Exception as e:
        lines += ["integrations: could not read (%s)" % e.__class__.__name__]
    try:          # V-050: on/off and the last error only; never tokens, passphrase, channel ids, allow lists or message text
        lines += ["chat replies: " + ctx.chat.diagnostics()]
    except Exception as e:
        lines += ["chat replies: could not read (%s)" % e.__class__.__name__]
    log = Path(os.path.dirname(os.path.abspath(ctx.settings_file))) / "client-errors.log"
    lines += ["", "window errors (newest last):"]
    try:
        lines += ["  " + l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]] or ["  none"]
    except OSError:
        lines += ["  none"]
    return "\n".join(lines) + "\n"


def save(ctx, folder=None):
    """Write the report to the Desktop (or Downloads, or the work folder); returns the file path."""
    home = Path(os.path.expanduser("~"))
    for d in ([Path(folder)] if folder else []) + [home / "Desktop", home / "Downloads", Path(os.path.dirname(os.path.abspath(ctx.settings_file)))]:
        if d.is_dir():
            p = d / ("vanilla-agents-diagnostics-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
            p.write_text(report(ctx), encoding="utf-8")
            return str(p)
    raise OSError("no folder to save into")
