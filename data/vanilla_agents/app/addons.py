"""Addons (V-051, docs/ADDONS.md): your own scripts that run inside the program window, like WoW addons.

Two shapes in <assets>/addons/:
  <id>/addon.json + main.js (+ optional main.css, images, LICENSE)   a folder addon with a manifest
  <name>.js                                                         a legacy single-file addon (manifest made up from the name)
Which ones load is the owner's choice, kept in the settings file (settings.py EXTRA "addons": {"enabled": {key: bool}}).
A folder addon starts switched off; a legacy file stays on as before. Remove never deletes: it moves the addon to
<assets>/addons/.removed/<id>-<time>/. Installing from a zip is in addon_zip.py, packaging and the Workshop in addon_workshop.py.
Addons run in the window with the window's power; nothing here can make one safe, so only owner clicks (window key) change them."""
import json
import re
import time
from pathlib import Path

from . import settings

ID_RE = re.compile(r"^[a-z0-9-]{2,48}\Z")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}\Z")
LICENSE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+ -]{0,63}\Z")
# what an addon says it does; shown to the owner before enabling. Not a sandbox: every addon can do all of it.
PERMISSIONS = ("state", "panel", "inn", "chat", "actions", "network")
MANIFEST = "addon.json"
MAIN = "main.js"
STYLE = "main.css"
GROUP = "addons"
LIMITS = {"name": 48, "author": 80, "summary": 280, "description": 4000}


def addons_dir(assets_dir):
    return Path(assets_dir) / "addons"


def _url_ok(v):
    return isinstance(v, str) and v.startswith("https://") and len(v) <= 300 and not any(c.isspace() or c in "\"'<>" for c in v)


def validate(m, folder_id=None):
    """A clean manifest dict or ValueError with a plain message. folder_id: the folder it sits in must have the same name."""
    if not isinstance(m, dict):
        raise ValueError("addon.json must be a group { ... }")
    i = m.get("id")
    if not isinstance(i, str) or not ID_RE.match(i):
        raise ValueError("addon.json: id must be 2-48 lower-case letters, digits or dashes")
    if folder_id is not None and i != folder_id:
        raise ValueError("addon.json: id '%s' does not match its folder '%s'" % (i, folder_id))
    out = {"id": i}
    for k, n in LIMITS.items():
        v = m.get(k, "" if k == "description" else None)
        if not isinstance(v, str) or (k != "description" and not v.strip()) or len(v) > n:
            raise ValueError("addon.json: %s must be text of at most %d characters%s" % (k, n, "" if k == "description" else " (not empty)"))
        out[k] = v.strip() if k != "description" else v.strip("\n")
    if not isinstance(m.get("version"), str) or not VERSION_RE.match(m["version"]):
        raise ValueError("addon.json: version must look like 1.0.2 (letters, digits, dots, + or -)")
    out["version"] = m["version"]
    if not isinstance(m.get("license"), str) or not LICENSE_RE.match(m["license"]):
        raise ValueError("addon.json: license must be a licence name such as MIT")
    out["license"] = m["license"]
    for k in ("homepage", "source_url"):
        if m.get(k) not in (None, ""):
            if not _url_ok(m[k]):
                raise ValueError("addon.json: %s must be an https:// address" % k)
            out[k] = m[k]
    p = m.get("permissions", [])
    if not isinstance(p, list) or not all(isinstance(x, str) for x in p):
        raise ValueError("addon.json: permissions must be a list of names")
    bad = [x for x in p if x not in PERMISSIONS]
    if bad:
        raise ValueError("addon.json: unknown permission %s (known: %s)" % (", ".join(bad), ", ".join(PERMISSIONS)))
    out["permissions"] = sorted(set(p), key=PERMISSIONS.index)
    return out


def legacy_manifest(name):
    """A made-up manifest for an old single-file addon (quest-count.js)."""
    stem = name[:-3]
    return {"id": re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:48] or "addon", "name": stem[:48], "author": "", "version": "",
            "summary": "", "description": "", "license": "", "permissions": list(PERMISSIONS)}


def enabled_map(settings_file):
    v = settings.load_extra(settings_file, GROUP).get("enabled")
    return {k: b for k, b in v.items() if isinstance(k, str) and isinstance(b, bool)} if isinstance(v, dict) else {}


def scan(assets_dir, settings_file):
    """Every installed addon, sorted by name: {key, id, name, ..., legacy, enabled, problem, script, style}.
    key = the folder id, or the file name for a legacy addon (a dot never appears in an id, so they cannot clash)."""
    d, on, out = addons_dir(assets_dir), enabled_map(settings_file), []
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if p.name.startswith(".") or p.is_symlink():
            continue
        if p.is_file() and p.suffix == ".js":
            m = legacy_manifest(p.name)
            out.append({**m, "key": p.name, "legacy": True, "problem": "", "enabled": on.get(p.name, True), "script": "/addons/" + p.name, "style": None,
                        "v": int(p.stat().st_mtime)})
        elif p.is_dir() and ID_RE.match(p.name):
            out.append(_folder(p, on))
    return sorted(out, key=lambda a: (a["name"].lower(), a["key"]))


def _folder(p, on):
    a = {**legacy_manifest(p.name + ".js"), "permissions": [], "key": p.name, "legacy": False, "problem": "", "style": None}
    try:
        raw = (p / MANIFEST).read_bytes()
        if len(raw) > 65536:
            raise ValueError("addon.json is too big")
        a.update(validate(json.loads(raw.decode("utf-8")), p.name))
        if not (p / MAIN).is_file():
            raise ValueError("main.js is missing")
    except OSError:
        a["problem"] = "addon.json is missing"
    except ValueError as e:      # bad JSON / UTF-8 are ValueErrors too
        a["problem"] = str(e)
    a["enabled"] = on.get(p.name, False) and not a["problem"]
    a["has_license"] = any((p / n).is_file() for n in ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING"))
    if not a["problem"]:
        a["script"] = "/addons/%s/%s" % (p.name, MAIN)
        a["style"] = "/addons/%s/%s" % (p.name, STYLE) if (p / STYLE).is_file() else None
        a["v"] = int(max((p / MAIN).stat().st_mtime, (p / MANIFEST).stat().st_mtime))
    return a


def payload(assets_dir, settings_file):
    """GET /api/addons: the list for the Addons window, and `load` = only what the window should start (enabled, no problem)."""
    lst = scan(assets_dir, settings_file)
    return {"addons": lst, "load": [{"key": a["key"], "script": a["script"] + "?v=%d" % a["v"], "style": a["style"], "id": a["id"], "name": a["name"],
                                     "version": a["version"], "legacy": a["legacy"]} for a in lst if a["enabled"] and not a["problem"]],
            "permissions": list(PERMISSIONS), "folder": str(addons_dir(assets_dir))}


def find(assets_dir, settings_file, key):
    if not isinstance(key, str):
        raise ValueError("key must be text")
    for a in scan(assets_dir, settings_file):
        if a["key"] == key:
            return a
    raise ValueError("there is no addon called %s" % key)


def set_enabled(assets_dir, settings_file, key, on):
    if not isinstance(on, bool):
        raise ValueError("enabled must be true or false")
    with settings.WRITE_LOCK:
        a = find(assets_dir, settings_file, key)
        if on and a["problem"]:
            raise ValueError("%s cannot be switched on: %s" % (a["name"], a["problem"]))
        _store(settings_file, key, on)


def _store(settings_file, key, on):
    with settings.WRITE_LOCK:
        g = settings.load_extra(settings_file, GROUP)
        m = enabled_map(settings_file)
        if on is None:
            m.pop(key, None)
        else:
            m[key] = on
        settings.save_extra(settings_file, GROUP, {**g, "enabled": m})


def served_file(assets_dir, settings_file, rel):
    """For GET /addons/<rel>: True when rel belongs to an enabled addon (a disabled or removed one is never served)."""
    first = rel.lstrip("/").split("/", 1)[0]
    if not first or first.startswith("."):
        return False
    on = enabled_map(settings_file)
    if "/" not in rel.lstrip("/"):
        return first.endswith(".js") and on.get(first, True)
    return bool(ID_RE.match(first)) and on.get(first, False)


def stamp(clock=time.time):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))


def move_aside(assets_dir, path, name, clock=time.time):
    """Move an addon folder / file into .removed/<name>-<time>/ (never deleted). Returns the new place."""
    rm = addons_dir(assets_dir) / ".removed"
    rm.mkdir(parents=True, exist_ok=True)
    base = "%s-%s" % (name, stamp(clock))
    dest, n = rm / base, 1
    while dest.exists():
        n += 1
        dest = rm / ("%s-%d" % (base, n))
    if path.is_dir():
        path.replace(dest)
    else:
        dest.mkdir()
        path.replace(dest / path.name)
    return dest


def remove(assets_dir, settings_file, key, clock=time.time):
    with settings.WRITE_LOCK:
        a = find(assets_dir, settings_file, key)
        src = addons_dir(assets_dir) / a["key"]
        dest = move_aside(assets_dir, src, a["id"] if not a["legacy"] else a["key"][:-3], clock)
        _store(settings_file, a["key"], None)
    return str(dest)
