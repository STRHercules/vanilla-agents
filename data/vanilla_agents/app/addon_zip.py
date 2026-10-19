"""Install a folder addon from a .zip (V-051): the owner's click (window key), or a Workshop download (addon_workshop.py).

Checked before anything is written: size, entry count, total unpacked size and the packed/unpacked ratio (zip bombs), every name
(no absolute paths, '..', backslashes, drive letters, Windows device names, odd characters), only plain files and folders (no
symlinks, devices, encrypted entries), only file types an addon needs, addon.json valid with main.js next to it. The addon may sit at
the top of the zip or in one folder (the Workshop packs it as <id>/...). An installed id is replaced only when the owner chose
Replace, and the old one is moved to .removed/ first. A new install starts switched off."""
import io
import json
import os
import re
import secrets
import shutil
import stat
import time
import zipfile
import zlib

from . import addons

LIMITS = {"max_zip_bytes": 2 * 1024 * 1024, "max_files": 200, "max_unpacked_bytes": 8 * 1024 * 1024, "max_ratio": 100, "max_depth": 6}
EXTS = (".js", ".css", ".json", ".png", ".jpg", ".txt", ".md", ".ogg", ".wav", ".mp3", ".woff2", ".ttf")
BARE = ("LICENSE", "LICENCE", "COPYING", "README", "NOTICE", "AUTHORS")
DEVICE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?\Z", re.I)
PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 ._+()-]{0,99}\Z")
SKIP = ("__MACOSX/",)


class Exists(ValueError):
    """The id is installed already and Replace was not chosen."""


def limits(cfg_app):
    return {**LIMITS, **{k: v for k, v in (cfg_app.get("addons") or {}).items() if k in LIMITS}}


def _check_name(name, depth):
    if "\x00" in name or "\\" in name or name.startswith("/") or ":" in name:
        raise ValueError("the zip has an unsafe name: %r" % name[:80])
    parts = name.rstrip("/").split("/")
    if len(parts) > depth or len(name) > 240:
        raise ValueError("the zip has a path that is too deep or long: %r" % name[:80])
    for p in parts:
        if p in ("", ".", ".."):
            raise ValueError("the zip has an unsafe name: %r" % name[:80])
    if any(p.startswith(".") for p in parts):
        return None          # hidden files (.gitignore, .github/) are left out
    for p in parts:
        if not PART.match(p) or DEVICE.match(p) or p.endswith((".", " ")):
            raise ValueError("the zip has an unsafe name: %r" % name[:80])
    return parts


def _kind(info):
    mode = info.external_attr >> 16
    if info.flag_bits & 0x1:
        raise ValueError("the zip is encrypted: %s" % info.filename[:80])
    if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ValueError("the zip holds a link or device, not a plain file: %s" % info.filename[:80])
    return "dir" if info.is_dir() or (mode and stat.S_ISDIR(mode)) else "file"


def inspect(data, lim=LIMITS):
    """Check a zip (bytes) without writing. Returns (manifest, [(ZipInfo, path inside the addon)], zipfile)."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("no zip file")
    if len(data) > lim["max_zip_bytes"]:
        raise ValueError("the zip is bigger than %d KB" % (lim["max_zip_bytes"] // 1024))
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, EOFError):
        raise ValueError("this is not a zip file (or it is damaged)")
    infos = [i for i in infos if not i.filename.startswith(SKIP) and not i.filename.endswith("/.DS_Store") and i.filename != ".DS_Store"]
    if len(infos) > lim["max_files"]:
        raise ValueError("the zip has more than %d entries" % lim["max_files"])
    total, files, seen = 0, [], set()
    for i in infos:
        parts = _check_name(i.filename, lim["max_depth"] + 1)
        if _kind(i) == "dir" or parts is None:
            continue
        low = "/".join(parts).lower()
        if low in seen:
            raise ValueError("the zip has the same file twice: %s" % i.filename[:80])
        seen.add(low)
        if not (parts[-1].lower().endswith(EXTS) or parts[-1] in BARE):
            raise ValueError("an addon may not contain %s (allowed: %s and LICENSE/README)" % (parts[-1][:60], " ".join(EXTS)))
        if i.file_size > lim["max_unpacked_bytes"] or (i.file_size > 4096 and i.file_size > lim["max_ratio"] * max(1, i.compress_size)):
            raise ValueError("the zip looks like a zip bomb (%s unpacks too big)" % i.filename[:80])
        total += i.file_size
        files.append((i, parts))
    if total > lim["max_unpacked_bytes"]:
        raise ValueError("the zip unpacks to more than %d KB" % (lim["max_unpacked_bytes"] // 1024))
    if total > 65536 and total > lim["max_ratio"] * len(data):
        raise ValueError("the zip looks like a zip bomb (it unpacks %d times bigger)" % (total // len(data)))
    tops = {p[0] for _i, p in files}
    if any(len(p) == 1 and p[0] == addons.MANIFEST for _i, p in files):
        root = 0
    elif len(tops) == 1 and any(len(p) == 2 and p[1] == addons.MANIFEST for _i, p in files):
        root = 1
    else:
        raise ValueError("the zip has no addon.json (at the top, or in one folder)")
    files = [(i, "/".join(p[root:])) for i, p in files if len(p) > root]
    by = {rel: i for i, rel in files}
    raw = _read(z, by[addons.MANIFEST], 65536)
    try:
        m = addons.validate(json.loads(raw.decode("utf-8")))
    except ValueError as e:
        raise ValueError(str(e) if str(e).startswith("addon.json") else "addon.json is not valid JSON")
    if addons.MAIN not in by:
        raise ValueError("the zip has no main.js next to addon.json")
    return m, files, z


def _read(z, info, limit):
    """Read one entry, never more than its declared size (headers can lie) nor limit."""
    out = bytearray()
    try:
        with z.open(info) as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                out += chunk
                if len(out) > min(limit, info.file_size):
                    raise ValueError("the zip is damaged or a zip bomb (%s is bigger than it says)" % info.filename[:80])
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as e:
        raise ValueError("the zip is damaged (%s: %s)" % (info.filename[:80], e))
    return bytes(out)


def install(assets_dir, settings_file, data, replace=False, lim=LIMITS, clock=time.time):
    """Install from zip bytes. Returns {"installed": id, "name", "replaced": old place or None}. Exists when the id is taken."""
    m, files, z = inspect(data, lim)
    d = addons.addons_dir(assets_dir)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / m["id"]
    if (dest.exists() or dest.is_symlink()) and not replace:
        raise Exists("an addon with the id '%s' is installed already; choose Replace to put this one in its place" % m["id"])
    tmp = d / (".incoming-" + secrets.token_hex(6))
    tmp.mkdir()
    try:
        budget = lim["max_unpacked_bytes"]
        for info, rel in files:
            body = _read(z, info, budget)
            budget -= len(body)
            target = tmp.joinpath(*rel.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write(body)
        old = None
        if dest.exists() or dest.is_symlink():
            old = str(addons.move_aside(assets_dir, dest, m["id"], clock))
        tmp.replace(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    addons._store(settings_file, m["id"], False)
    return {"installed": m["id"], "name": m["name"], "version": m["version"], "replaced": old}
