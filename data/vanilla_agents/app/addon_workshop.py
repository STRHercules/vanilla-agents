"""Share addons through the Open Vanilla Workshop (V-051).

package(): a folder addon -> <assets>/addons/.packages/<id>-<version>.zip (deterministic: sorted entries, 1980-01-01 timestamps,
fixed attributes, everything under <id>/, OPENVANILLA-CREDITS.txt added) and <id>.yml, a catalogue entry in the Workshop's format
(workshop/catalog/<id>.yml, keys of tools/workshop_lib.py FIELD_ORDER) with category "agents" and kind "vanilla-agents-addon".
The YAML is written by hand (no PyYAML): strings are quoted whenever plain YAML could misread them.

browse() / install_from(): only when the owner clicks Browse. Reads the Workshop index (app.json addons.workshop_index_url), lists
entries with kind == vanilla-agents-addon, downloads a package over https only (size-limited), checks its sha256 against the entry
and installs it through addon_zip (the same checks as a zip from disk). Tests pass a fake opener; nothing else goes online."""
import datetime as dt
import hashlib
import io
import json
import os
import re
import urllib.request
import zipfile
from urllib.parse import urljoin

from . import addon_zip, addons

KIND = "vanilla-agents-addon"
CATEGORY = "agents"
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
INDEX_URL = "https://openvanilla.app/community/workshop/index.json"
# tools/workshop_lib.py ALLOWED_LICENSES: the Workshop may host a copy only under these
ALLOWED_LICENSES = ("MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "MPL-2.0", "Unlicense", "CC0-1.0")
FIELD_ORDER = ("id", "name", "author", "summary", "description", "category", "license", "rehost", "source_url", "homepage", "version", "added",
               "files", "screenshots", "tags", "compat", "ours", "folders", "verified", "upstream", "scan")
SHA_RE = re.compile(r"^[0-9a-f]{64}\Z")
INDEX_MAX = 4 * 1024 * 1024


# ---------- YAML (the small subset a catalogue entry needs) ----------
_PLAIN = re.compile(r"^[A-Za-z_/][A-Za-z0-9 _./@()+-]*\Z")
_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "y", "n", "~"}
_ESC = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r", "\0": "\\0"}


def _dq(s):
    out = []
    for c in s:
        if c in _ESC:
            out.append(_ESC[c])
        elif ord(c) < 0x20 or ord(c) == 0x7f or c in "\x85  ﻿":
            out.append("\\x%02x" % ord(c) if ord(c) < 0x100 else "\\u%04x" % ord(c))
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v)
    if _PLAIN.match(s) and s.lower() not in _RESERVED and not s.endswith(" ") and "  " not in s:
        return s
    return _dq(s)


def _block(s, pad):
    """A literal block (|) for multi-line text, or None when the text has characters a block cannot hold."""
    if any((ord(c) < 0x20 and c not in "\n\t") or c in "\x7f\x85  ﻿" for c in s) or s.strip("\n") == "":
        return None
    body = s.rstrip("\n")
    trail = len(s) - len(body)
    head = "|" + ("2" if body[:1] in (" ", "\t") else "") + {0: "-", 1: ""}.get(trail, "+")
    lines = body.split("\n") + [""] * (trail - 1 if trail > 1 else 0)
    return head + "\n" + "\n".join((pad + ln) if ln else "" for ln in lines)


def dump(obj, indent=0):
    """Mapping -> YAML text (block style like PyYAML's safe_dump: list items at the key's indent)."""
    pad, out = " " * indent, []
    for k, v in obj.items():
        key = pad + scalar(k) + ":"
        if isinstance(v, dict):
            out.append(key + (" {}" if not v else "\n" + dump(v, indent + 2)))
        elif isinstance(v, list):
            if not v:
                out.append(key + " []")
                continue
            out.append(key)
            for it in v:
                if isinstance(it, dict) and it:
                    sub = dump(it, indent + 2).split("\n")
                    out.append(pad + "- " + sub[0][indent + 2:])
                    out.extend(sub[1:])
                else:
                    out.append(pad + "- " + scalar(it))
        elif isinstance(v, str) and "\n" in v and _block(v, pad + "  "):
            out.append(key + " " + _block(v, pad + "  "))
        else:
            out.append(key + " " + scalar(v))
    return "\n".join(out)


# ---------- packaging ----------
def credits_text(m):
    """Same layout as tools/workshop_build.py credits_text."""
    lines = ["%s — packaged by the Open Vanilla Workshop" % m["name"], "", "Author:   %s" % m.get("author", ""),
             "Source:   %s" % (m.get("source_url") or m.get("homepage") or ""), "Licence:  %s" % m.get("license", ""),
             "Kind:     Vanilla Agents addon (JavaScript for the Vanilla Agents window, not a game addon)", "",
             "This package is the author's code as published, unmodified except for this file. All credit belongs to the author.",
             "Report problems with the addon to the author; problems with the package to https://openvanilla.app/workshop.html."]
    return "\n".join(lines) + "\n"


def det_zip(folder, name, extra):
    """Bytes of a deterministic zip of folder under name/ (sorted, fixed time and attributes; hidden files, symlinks skipped)."""
    entries = {}
    for dp, dn, files in os.walk(folder):
        dn[:] = sorted(d for d in dn if not d.startswith(".") and d != "__MACOSX" and not os.path.islink(os.path.join(dp, d)))
        for f in sorted(files):
            full = os.path.join(dp, f)
            if f.startswith(".") or os.path.islink(full) or not os.path.isfile(full):
                continue
            entries["%s/%s" % (name, os.path.relpath(full, folder).replace(os.sep, "/"))] = full
    for zp, text in extra.items():
        entries[zp] = text.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for zp in sorted(entries):
            src = entries[zp]
            info = zipfile.ZipInfo(zp, ZIP_DATE)
            info.compress_type, info.create_system, info.external_attr = zipfile.ZIP_DEFLATED, 3, (0o100644 << 16)
            if isinstance(src, str):
                with open(src, "rb") as fh:
                    src = fh.read()
            z.writestr(info, src, compresslevel=6)
    return buf.getvalue()


def github_repo(url):
    m = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?\Z", url or "")
    return "%s/%s" % m.groups() if m else None


def entry(m, zname, digest, size, today):
    """The catalogue entry (dict, Workshop key order) for a manifest and its zip."""
    rehost = m["license"] in ALLOWED_LICENSES
    e = {"id": m["id"], "name": m["name"], "author": m["author"], "summary": m["summary"], "description": m["description"] or m["summary"],
         "category": CATEGORY, "license": m["license"], "rehost": rehost}
    if m.get("source_url"):
        e["source_url"] = m["source_url"]
    if m.get("homepage") or m.get("source_url"):
        e["homepage"] = m.get("homepage") or m["source_url"]
    e.update({"version": m["version"], "added": today, "files": [{"name": zname, "sha256": digest, "size": size, "folders": [m["id"]]}],
              "screenshots": [], "tags": ["vanilla-agents"] + m["permissions"], "compat": "unknown", "ours": False, "folders": [m["id"]], "verified": False})
    repo = github_repo(m.get("source_url"))
    if repo:
        e["upstream"] = {"how": "git", "repo": repo, "branch": "main"}
    e["kind"] = KIND
    e["permissions"] = m["permissions"]
    return e


def package(assets_dir, settings_file, key, today=None):
    """Write <id>-<version>.zip and <id>.yml into .packages/; returns where they are and what to do next."""
    a = addons.find(assets_dir, settings_file, key)
    if a["legacy"]:
        raise ValueError("only folder addons (with addon.json) can be packaged; a single .js file has no name, licence or author")
    if a["problem"]:
        raise ValueError("%s cannot be packaged: %s" % (a["name"], a["problem"]))
    folder = addons.addons_dir(assets_dir) / a["id"]
    data = det_zip(str(folder), a["id"], {"%s/OPENVANILLA-CREDITS.txt" % a["id"]: credits_text(a)})
    addon_zip.inspect(data, {**addon_zip.LIMITS, "max_zip_bytes": max(addon_zip.LIMITS["max_zip_bytes"], len(data))})      # what we hand out installs here too
    out = addons.addons_dir(assets_dir) / ".packages"
    out.mkdir(parents=True, exist_ok=True)
    zname = "%s-%s.zip" % (a["id"], a["version"])
    digest = hashlib.sha256(data).hexdigest()
    e = entry(a, zname, digest, len(data), today or dt.date.today().isoformat())
    text = dump(e) + "\n"
    (out / zname).write_bytes(data)
    (out / (a["id"] + ".yml")).write_text(text, encoding="utf-8", newline="\n")
    return {"zip": str(out / zname), "yml": str(out / (a["id"] + ".yml")), "folder": str(out), "sha256": digest, "size": len(data),
            "rehost": e["rehost"], "license": a["license"], "has_license": a.get("has_license", False), "yaml": text, "id": a["id"], "zip_name": zname}


# ---------- browsing the Workshop (only on the owner's click) ----------
class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ValueError("the Workshop redirected to a non-https address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_opener():
    return urllib.request.build_opener(_HttpsOnly())


def fetch(url, limit, opener, timeout=15):
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("only https:// addresses are used (got %s)" % str(url)[:80])
    req = urllib.request.Request(url, headers={"User-Agent": "vanilla-agents", "Accept": "*/*"})
    try:
        with opener.open(req, timeout=timeout) as r:
            final = r.geturl() if hasattr(r, "geturl") else url
            if not str(final).startswith("https://"):
                raise ValueError("the Workshop answered from a non-https address")
            body = r.read(limit + 1)
    except OSError as e:
        raise ValueError("the Workshop could not be reached (%s)" % e)
    if len(body) > limit:
        raise ValueError("the Workshop sent more than %d KB" % (limit // 1024))
    return body


def _json(url, opener, timeout):
    try:
        return json.loads(fetch(url, INDEX_MAX, opener, timeout).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("the Workshop sent something that is not JSON")


def _https(v):
    return v if isinstance(v, str) and v.startswith("https://") and len(v) < 400 else None


def card(c, installed):
    t = lambda k, n=300: str(c.get(k) or "")[:n]      # noqa: E731
    return {"id": c["id"], "name": t("name", 48) or c["id"], "author": t("author", 80), "summary": t("summary"), "version": t("version", 32),
            "license": t("license", 64), "homepage": _https(c.get("homepage")) or _https(c.get("source_url")), "installed": c["id"] in installed,
            "installable": bool(c.get("install") or c.get("files"))}


def browse(index_url, opener, installed, timeout=15):
    idx = _json(index_url, opener, timeout)
    items = idx.get("addons") if isinstance(idx, dict) else None
    if not isinstance(items, list):
        raise ValueError("the Workshop index has no addon list")
    mine = [c for c in items if isinstance(c, dict) and c.get("kind") == KIND and isinstance(c.get("id"), str) and addons.ID_RE.match(c["id"])]
    return {"url": index_url, "count": len(items), "entries": [card(c, installed) for c in mine]}


def install_from(index_url, opener, assets_dir, settings_file, addon_id, replace=False, lim=addon_zip.LIMITS, timeout=15):
    """Download the entry's package, check its sha256, install it (switched off). The zip's addon.json must have the entry's id."""
    if not isinstance(addon_id, str) or not addons.ID_RE.match(addon_id):
        raise ValueError("id must be an addon id")
    idx = _json(index_url, opener, timeout)
    c = next((x for x in (idx.get("addons") or []) if isinstance(x, dict) and x.get("id") == addon_id and x.get("kind") == KIND), None) if isinstance(idx, dict) else None
    if c is None:
        raise ValueError("%s is not a Vanilla Agents addon in the Workshop" % addon_id)
    base = idx.get("base") if _https(idx.get("base")) else index_url
    files = c.get("files")
    if not files:       # the index card has no file list: the entry's own page (<id>.json next to index.json) has it
        full = _json(urljoin(index_url, addon_id + ".json"), opener, timeout)
        files = full.get("files") if isinstance(full, dict) else None
    f = next((x for x in files or [] if isinstance(x, dict) and isinstance(x.get("url"), str) and SHA_RE.match(str(x.get("sha256", "")))), None)
    if f is None:
        raise ValueError("the Workshop lists no package with a fingerprint (sha256) for %s" % addon_id)
    url = urljoin(base, f["url"])
    data = fetch(url, lim["max_zip_bytes"], opener, timeout)
    if hashlib.sha256(data).hexdigest() != f["sha256"]:
        raise ValueError("the download does not match the Workshop's fingerprint (sha256); nothing was installed")
    m = addon_zip.inspect(data, lim)[0]
    if m["id"] != addon_id:
        raise ValueError("the package holds '%s', not '%s'; nothing was installed" % (m["id"], addon_id))
    return addon_zip.install(assets_dir, settings_file, data, replace, lim)
