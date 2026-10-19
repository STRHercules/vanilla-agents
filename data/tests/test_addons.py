"""V-051 addons: manifests, old single-file addons, enable/disable kept in the settings file (and disabled ones never loaded or
served), safe zip installs against crafted zips, the Replace rule, Remove never deletes, deterministic Workshop packages and their
hand-written YAML (parsed back with a tiny parser here: PyYAML is not used), Workshop browse/install with a fake opener only, and
every addon call is the owner's (window key; not a phone call; not an MCP tool: tests/test_mcp.py)."""
import base64
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from vanilla_agents.app import addon_workshop as W
from vanilla_agents.app import addon_zip, addons, addons_api, server, settings
from vanilla_agents.app.config import ROOT, load_all

EXAMPLE = ROOT / "examples" / "addons" / "hello-inn"
GOOD = {"id": "my-addon", "name": "My addon", "author": "Me", "version": "1.2.0", "summary": "Does a small thing.",
        "description": "Longer.", "license": "MIT", "permissions": ["panel", "state"]}


def mkzip(entries, mode=None):
    """entries: [(name, bytes) | (ZipInfo, bytes)]."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def addon_entries(m=None, prefix=""):
    return [(prefix + "addon.json", json.dumps(m or GOOD)), (prefix + "main.js", "export default () => {};")]


class Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.assets = self.root / "assets"
        (self.assets / "addons").mkdir(parents=True)
        self.sfile = str(self.root / "settings.json")
        self.addCleanup(self._t.cleanup)

    def copy_example(self):
        shutil.copytree(EXAMPLE, self.assets / "addons" / "hello-inn")


class Manifest(unittest.TestCase):
    def test_good_manifest(self):
        m = addons.validate({**GOOD, "homepage": "https://example.com/x", "extra": 1})
        self.assertEqual((m["id"], m["permissions"], m["homepage"]), ("my-addon", ["state", "panel"], "https://example.com/x"))
        self.assertNotIn("extra", m)

    def test_bad_manifests(self):
        for bad in ({"id": "A"}, {"id": "a"}, {"id": "x" * 49}, {"id": "a_b"}, {"id": "ab\n"}, {"id": "../ab"}, {"name": ""}, {"name": "n" * 49},
                    {"version": "1 0"}, {"version": ""}, {"summary": None}, {"license": ""}, {"permissions": ["root"]}, {"permissions": "panel"},
                    {"homepage": "http://example.com"}, {"source_url": "javascript:alert(1)"}, {"description": 5}):
            with self.assertRaises(ValueError, msg=bad):
                addons.validate({**GOOD, **bad})
        with self.assertRaises(ValueError):
            addons.validate(GOOD, folder_id="other")
        with self.assertRaises(ValueError):
            addons.validate(["not", "a", "dict"])

    def test_example_addon_is_valid(self):
        m = addons.validate(json.loads((EXAMPLE / "addon.json").read_text(encoding="utf-8")), "hello-inn")
        self.assertEqual(m["license"], "MIT")
        self.assertTrue((EXAMPLE / "main.js").is_file() and (EXAMPLE / "LICENSE").is_file())


class Listing(Tmp):
    def test_legacy_and_folder_addons(self):
        (self.assets / "addons" / "Quest Count.js").write_text("export default () => {};")
        self.copy_example()
        (self.assets / "addons" / "broken").mkdir()
        (self.assets / "addons" / "broken" / "addon.json").write_text("{nope")
        (self.assets / "addons" / ".removed").mkdir()
        (self.assets / "addons" / "notes.txt").write_text("x")
        p = addons.payload(self.assets, self.sfile)
        by = {a["key"]: a for a in p["addons"]}
        self.assertEqual(set(by), {"Quest Count.js", "hello-inn", "broken"})
        leg = by["Quest Count.js"]
        self.assertTrue(leg["legacy"] and leg["enabled"])
        self.assertEqual((leg["id"], leg["name"]), ("quest-count", "Quest Count"))
        self.assertFalse(by["hello-inn"]["enabled"])            # a folder addon starts switched off
        self.assertTrue(by["broken"]["problem"])
        self.assertEqual([a["key"] for a in p["load"]], ["Quest Count.js"])
        with self.assertRaises(ValueError):
            addons.set_enabled(self.assets, self.sfile, "broken", True)
        with self.assertRaises(ValueError):
            addons.set_enabled(self.assets, self.sfile, "nope", True)

    def test_enable_disable_persist_and_load(self):
        self.copy_example()
        (self.assets / "addons" / "old.js").write_text("x")
        addons.set_enabled(self.assets, self.sfile, "hello-inn", True)
        addons.set_enabled(self.assets, self.sfile, "old.js", False)
        saved = json.loads(Path(self.sfile).read_text(encoding="utf-8"))
        self.assertEqual(saved["addons"]["enabled"], {"hello-inn": True, "old.js": False})
        load = addons.payload(self.assets, self.sfile)["load"]
        self.assertEqual([(a["key"], a["style"]) for a in load], [("hello-inn", "/addons/hello-inn/main.css")])
        self.assertTrue(load[0]["script"].startswith("/addons/hello-inn/main.js?v="))
        # the sound menu (settings.update) keeps the group, and /api/settings cannot change it
        sd = load_all()["sounds"]
        settings.update(self.sfile, {"master": {"volume": 0.5}, "addons": {"enabled": {"old.js": True}}}, sd)
        self.assertEqual(settings.load_extra(self.sfile, "addons")["enabled"], {"hello-inn": True, "old.js": False})
        self.assertIn("addons", settings.EXTRA)
        with self.assertRaises(ValueError):
            addons.set_enabled(self.assets, self.sfile, "hello-inn", "yes")

    def test_served_only_when_enabled(self):
        self.copy_example()
        f = lambda rel: addons.served_file(self.assets, self.sfile, rel)      # noqa: E731
        self.assertFalse(f("hello-inn/main.js"))
        addons.set_enabled(self.assets, self.sfile, "hello-inn", True)
        self.assertTrue(f("hello-inn/main.js"))
        for bad in (".removed/hello-inn/main.js", ".packages/x.zip", "", "/", "Bad_Id/main.js"):
            self.assertFalse(f(bad), bad)
        self.assertTrue(f("legacy.js"))       # an old single file loads unless switched off

    def test_remove_moves_never_deletes(self):
        self.copy_example()
        (self.assets / "addons" / "old.js").write_text("x")
        addons.set_enabled(self.assets, self.sfile, "hello-inn", True)
        dest = Path(addons.remove(self.assets, self.sfile, "hello-inn", clock=lambda: 0))
        self.assertTrue((dest / "addon.json").is_file())
        self.assertEqual(dest.parent, self.assets / "addons" / ".removed")
        self.assertTrue(dest.name.startswith("hello-inn-"))
        self.assertFalse((self.assets / "addons" / "hello-inn").exists())
        self.assertNotIn("hello-inn", addons.enabled_map(self.sfile))
        dest2 = Path(addons.remove(self.assets, self.sfile, "old.js", clock=lambda: 0))
        self.assertTrue((dest2 / "old.js").is_file())
        self.assertNotEqual(dest, dest2)
        self.assertEqual(addons.scan(self.assets, self.sfile), [])


class SafeZip(Tmp):
    def install(self, data, replace=False, lim=addon_zip.LIMITS):
        return addon_zip.install(self.assets, self.sfile, data, replace, lim)

    def assert_refused(self, data, word, lim=addon_zip.LIMITS):
        before = sorted(str(p) for p in self.root.rglob("*"))
        with self.assertRaises(ValueError) as cm:
            self.install(data, lim=lim)
        self.assertIn(word, str(cm.exception))
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)     # nothing written anywhere

    def test_good_zip_installs_switched_off(self):
        r = self.install(mkzip(addon_entries() + [("img/a.png", b"\x89PNG"), (".gitignore", "x"), ("__MACOSX/._main.js", "x")]))
        self.assertEqual(r["installed"], "my-addon")
        d = self.assets / "addons" / "my-addon"
        self.assertEqual(sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()), ["addon.json", "img/a.png", "main.js"])
        self.assertEqual(addons.enabled_map(self.sfile), {"my-addon": False})
        self.assertEqual(addons.payload(self.assets, self.sfile)["load"], [])
        self.assertFalse([p for p in (self.assets / "addons").iterdir() if p.name.startswith(".incoming")])

    def test_one_top_folder_is_accepted(self):
        self.assertEqual(self.install(mkzip(addon_entries(prefix="my-addon/") + [("my-addon/LICENSE", "MIT")]))["installed"], "my-addon")
        self.assertTrue((self.assets / "addons" / "my-addon" / "LICENSE").is_file())

    def test_crafted_names_are_refused(self):
        for name in ("../evil.js", "a/../../evil.js", "/etc/evil.js", "a\\..\\evil.js", "C:evil.js", "a//b.js", "./main2.js", "con.js", "a/nul", "trail.js ", "<x>.js"):
            self.assert_refused(mkzip(addon_entries() + [(name, "x")]), "unsafe")

    def test_symlink_and_device_are_refused(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o644, stat.S_IFIFO | 0o644):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for n, d in addon_entries():
                    z.writestr(n, d)
                i = zipfile.ZipInfo("link.js")
                i.external_attr = mode << 16
                z.writestr(i, "/etc/passwd")
            self.assert_refused(buf.getvalue(), "link or device")

    def test_zip_bomb_and_limits(self):
        self.assert_refused(mkzip(addon_entries() + [("big.txt", b"\0" * 3_000_000)]), "zip bomb")
        self.assert_refused(mkzip(addon_entries() + [("f%03d.js" % i, "x") for i in range(201)]), "more than 200")
        self.assert_refused(mkzip(addon_entries() + [("a.txt", os.urandom(2500)), ("b.txt", os.urandom(2500))]), "unpacks to more", lim={**addon_zip.LIMITS, "max_unpacked_bytes": 4000})
        self.assert_refused(b"x" * 10, "not a zip")
        self.assert_refused(mkzip(addon_entries()) + b"\0" * 10, "bigger than", lim={**addon_zip.LIMITS, "max_zip_bytes": 100})
        self.assert_refused(mkzip(addon_entries() + [("/".join("d" * 8) + "/x.js", "x")]), "too deep")

    def test_manifest_and_contents_rules(self):
        self.assert_refused(mkzip([("main.js", "x")]), "no addon.json")
        self.assert_refused(mkzip([("a/addon.json", json.dumps(GOOD)), ("a/main.js", "x"), ("b/x.js", "x")]), "no addon.json")
        self.assert_refused(mkzip(addon_entries({**GOOD, "id": "Bad_Id"})), "id must be")
        self.assert_refused(mkzip([("addon.json", "{nope"), ("main.js", "x")]), "not valid JSON")
        self.assert_refused(mkzip([("addon.json", json.dumps(GOOD))]), "no main.js")
        self.assert_refused(mkzip(addon_entries() + [("tool.exe", "MZ")]), "may not contain")
        self.assert_refused(mkzip(addon_entries() + [("A.js", "x"), ("a.js", "y")]), "same file twice")

    def test_damaged_or_lying_entries_refused(self):
        body = b"export default () => { /* " + b"padding " * 200 + b"*/ };"
        data = bytearray(mkzip([("addon.json", json.dumps(GOOD)), ("main.js", body)]))
        at = bytes(data).index(b"main.js") + len("main.js") + 10       # inside main.js's compressed data
        data[at:at + 8] = b"\xff" * 8
        self.assert_refused(bytes(data), "damaged")
        small = bytearray(mkzip([("addon.json", json.dumps(GOOD)), ("main.js", body)]))
        cd = bytes(small).rindex(b"PK\x01\x02")            # main.js's central record: claim it unpacks to 10 bytes
        small[cd + 24:cd + 28] = (10).to_bytes(4, "little")
        self.assert_refused(bytes(small), "damaged")

    def test_encrypted_entry_refused(self):
        data = bytearray(mkzip(addon_entries() + [("x.js", "x")]))
        # set the 'encrypted' flag bit in every local and central header
        for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            for m in re.finditer(re.escape(sig), bytes(data)):
                data[m.start() + off] |= 1
        self.assert_refused(bytes(data), "encrypted")

    def test_replace_rule(self):
        self.install(mkzip(addon_entries()))
        addons.set_enabled(self.assets, self.sfile, "my-addon", True)
        v2 = mkzip(addon_entries({**GOOD, "version": "2.0.0"}))
        with self.assertRaises(addon_zip.Exists):
            self.install(v2)
        self.assertEqual(addons.find(self.assets, self.sfile, "my-addon")["version"], "1.2.0")
        r = self.install(v2, replace=True)
        self.assertTrue(r["replaced"] and Path(r["replaced"]).joinpath("addon.json").is_file())
        a = addons.find(self.assets, self.sfile, "my-addon")
        self.assertEqual((a["version"], a["enabled"]), ("2.0.0", False))      # replaced = off again until you enable it


# ---------- a tiny YAML reader for exactly the subset addon_workshop.dump writes ----------
def _scalar(s):
    s = s.strip()
    if s.startswith('"'):
        assert s.endswith('"') and len(s) >= 2, s
        out, i, body = [], 0, s[1:-1]
        esc = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r", "0": "\0"}
        while i < len(body):
            c = body[i]
            if c == "\\":
                n = body[i + 1]
                if n in esc:
                    out.append(esc[n]); i += 2
                elif n == "x":
                    out.append(chr(int(body[i + 2:i + 4], 16))); i += 4
                elif n == "u":
                    out.append(chr(int(body[i + 2:i + 6], 16))); i += 6
                else:
                    raise ValueError("bad escape " + n)
            else:
                assert c != '"', s
                out.append(c); i += 1
        return "".join(out)
    if s == "[]":
        return []
    if s == "{}":
        return {}
    if s in ("null", "true", "false"):
        return {"null": None, "true": True, "false": False}[s]
    if re.fullmatch(r"-?[0-9]+", s):
        return int(s)
    assert re.fullmatch(r"[A-Za-z_/][A-Za-z0-9 _./@()+-]*", s), "not a safe plain scalar: %r" % s
    return s


def _ind(line):
    return len(line) - len(line.lstrip(" "))


def yload(text):
    lines = text.split("\n")
    val, i = _block(lines, 0, 0)
    assert all(not ln.strip() for ln in lines[i:]), lines[i:]
    return val


def _block(lines, i, ind):
    while i < len(lines) and not lines[i].strip():
        i += 1
    if lines[i][ind:].startswith("- "):
        return _list(lines, i, ind)
    return _map(lines, i, ind)


def _map(lines, i, ind):
    out = {}
    while i < len(lines) and lines[i].strip() and _ind(lines[i]) == ind and not lines[i][ind:].startswith("- "):
        ln = lines[i][ind:]
        if ln.startswith('"'):
            end = ln.index('":') + 1
            key, rest = _scalar(ln[:end]), ln[end + 1:]
        else:
            key, _, rest = ln.partition(":")
        rest = rest[1:] if rest.startswith(" ") else rest
        i += 1
        if rest.startswith("|"):
            m = re.fullmatch(r"\|([1-9])?([+-])?", rest)
            assert m, rest
            cind = ind + int(m.group(1) or 2)
            body = []
            while i < len(lines) and (not lines[i].strip() or _ind(lines[i]) >= cind):
                if i == len(lines) - 1 and not lines[i]:
                    break
                body.append(lines[i][cind:]); i += 1
            chomp = m.group(2)
            if chomp == "+":
                out[key] = "\n".join(body) + "\n"
            else:
                while body and not body[-1]:
                    body.pop()
                out[key] = "\n".join(body) + ("" if chomp == "-" else "\n")
        elif rest == "":
            if i < len(lines) and lines[i][ind:].startswith("- ") and _ind(lines[i]) == ind:
                out[key], i = _list(lines, i, ind)
            else:
                out[key], i = _block(lines, i, _ind(lines[i]))
        else:
            out[key] = _scalar(rest)
    return out, i


def _list(lines, i, ind):
    out = []
    while i < len(lines) and _ind(lines[i]) == ind and lines[i][ind:].startswith("- "):
        item = lines[i][ind + 2:]
        if re.match(r'^("[^"]*"|[^"\s][^:]*):( |$)', item):
            lines = lines[:i] + [" " * (ind + 2) + item] + lines[i + 1:]
            v, i = _map(lines, i, ind + 2)
            out.append(v)
        else:
            out.append(_scalar(item)); i += 1
    return out, i


TRICKY = {
    "colon": "Bags: better, faster",
    "hash": "C# #1 fan",
    "quotes": 'He said "hi" and it\'s fine',
    "dash": "- not a list",
    "star": "*ref",
    "yes": "yes",
    "Null": "Null",
    "num": "1.0",
    "int_like": "007",
    "date": "2026-10-19",
    "unicode": "Café — ☕ Kühl 日本",
    "backslash": "C:\\path\\to",
    "tab": "a\tb",
    "control": "bell\x07here",
    "trail_space": "end ",
    "lead_space": " start",
    "empty": "",
    "brackets": "[x] {y}",
    "at": "@me",
    "percent": "%TEMP%",
    "multi": "line one\nline two: with colon\n\n  indented \"quoted\"\nlast",
    "multi_trail": "a\nb\n",
    "multi_two_trail": "a\nb\n\n",
    "multi_lead": "  first indented\nsecond",
    "multi_ctrl": "a\x01\nb",
    "only_newlines": "\n\n",
    "nested": {"list": ["x: y", "plain", 3, True, None], "empty": [], "map": {}},
    "maps": [{"name": "a.zip", "sha256": "ab" * 32, "folders": ["hello-inn"]}, {"k": "v"}],
}


class Yaml(unittest.TestCase):
    def test_round_trip_tricky_values(self):
        text = W.dump(TRICKY) + "\n"
        self.assertEqual(yload(text), TRICKY)
        for line in text.split("\n"):
            self.assertNotIn("\t", line.split('"')[0])

    def test_scalars_that_need_quotes(self):
        for v in ("yes", "No", "true", "null", "~", "1.0", "0x1F", "-x", "a: b", "a #b", "", " x", "x ", "Café"):
            self.assertTrue(W.scalar(v).startswith('"'), v)
        for v in ("MIT", "hello-inn", "Vanilla Agents", "vanilla-agents-addon", "workshop/catalog"):
            self.assertEqual(W.scalar(v), v)


class Package(Tmp):
    EXPECTED = """id: hello-inn
name: "Hello, Inn"
author: Vanilla Agents
summary: A tiny sign by the door that counts the helpers in the inn and the quests done today.
description: |-
  The example addon that ships with Vanilla Agents.
  It adds a small framed sign to the window that says how many helpers are in the inn and how many quests are done, and greets you once with the gold banner.
  Copy this folder to start your own addon (see docs/ADDONS.md).
category: agents
license: MIT
rehost: true
homepage: "https://openvanilla.app/workshop.html"
version: "1.0.0"
added: "2026-10-19"
files:
- name: hello-inn-1.0.0.zip
  sha256: {sha}
  size: {size}
  folders:
  - hello-inn
screenshots: []
tags:
- vanilla-agents
- state
- panel
compat: unknown
ours: false
folders:
- hello-inn
verified: false
kind: vanilla-agents-addon
permissions:
- state
- panel
"""

    def test_example_package_is_deterministic(self):
        self.copy_example()
        r1 = W.package(self.assets, self.sfile, "hello-inn", today="2026-10-19")
        z1 = Path(r1["zip"]).read_bytes()
        for p in (self.assets / "addons" / "hello-inn").iterdir():
            os.utime(p, (time.time() + 1000, time.time() + 1000))       # file dates must not matter
        (self.assets / "addons" / "hello-inn" / ".DS_Store").write_text("x")
        r2 = W.package(self.assets, self.sfile, "hello-inn", today="2026-10-19")
        self.assertEqual(Path(r2["zip"]).read_bytes(), z1)
        self.assertEqual(r1["sha256"], hashlib.sha256(z1).hexdigest())
        self.assertEqual(Path(r1["zip"]).name, "hello-inn-1.0.0.zip")
        self.assertEqual(Path(r1["yml"]), self.assets / "addons" / ".packages" / "hello-inn.yml")
        with zipfile.ZipFile(io.BytesIO(z1)) as z:
            names = z.namelist()
            self.assertEqual(names, sorted(names))
            self.assertEqual(names, ["hello-inn/LICENSE", "hello-inn/OPENVANILLA-CREDITS.txt", "hello-inn/addon.json", "hello-inn/main.css", "hello-inn/main.js"])
            self.assertTrue(all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist()))
            self.assertIn("Licence:  MIT", z.read("hello-inn/OPENVANILLA-CREDITS.txt").decode("utf-8"))
        text = Path(r1["yml"]).read_text(encoding="utf-8")
        self.assertEqual(text, self.EXPECTED.replace("{sha}", W.scalar(r1["sha256"])).replace("{size}", str(r1["size"])))
        e = yload(text)
        self.assertEqual((e["category"], e["kind"], e["rehost"], e["files"][0]["sha256"]), ("agents", "vanilla-agents-addon", True, r1["sha256"]))
        # what we hand out installs again through the same safe path
        shutil.rmtree(self.assets / "addons" / "hello-inn")
        self.assertEqual(addon_zip.install(self.assets, self.sfile, z1)["installed"], "hello-inn")

    def test_tricky_manifest_entry_round_trips(self):
        m = {**GOOD, "name": 'Bags: "Pro" #1', "author": "Zoë O'Neil", "summary": "yes", "description": "Line: one\n\n  two\n", "license": "Proprietary",
             "source_url": "https://github.com/me/my-addon", "version": "1.0.0+b2"}
        d = self.assets / "addons" / "my-addon"
        d.mkdir()
        (d / "addon.json").write_text(json.dumps(m), encoding="utf-8")
        (d / "main.js").write_text("export default () => {};")
        r = W.package(self.assets, self.sfile, "my-addon", today="2026-01-02")
        e = yload(r["yaml"])
        self.assertEqual((e["name"], e["author"], e["summary"], e["description"]), (m["name"], m["author"], "yes", "Line: one\n\n  two"))
        self.assertEqual((e["rehost"], e["upstream"]["repo"], e["homepage"], e["version"], e["added"]), (False, "me/my-addon", m["source_url"], "1.0.0+b2", "2026-01-02"))
        self.assertFalse(r["rehost"] or r["has_license"])
        self.assertTrue(Path(r["zip"]).name == "my-addon-1.0.0+b2.zip")

    def test_legacy_and_broken_cannot_be_packaged(self):
        (self.assets / "addons" / "old.js").write_text("x")
        with self.assertRaises(ValueError):
            W.package(self.assets, self.sfile, "old.js")


class FakeResp:
    def __init__(self, body, url):
        self.body, self.url = body, url

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, pages, final=None):
        self.pages, self.final, self.asked = pages, final or {}, []

    def open(self, req, timeout=None):
        u = req.full_url
        self.asked.append(u)
        if u not in self.pages:
            raise OSError("404 for " + u)
        return FakeResp(self.pages[u], self.final.get(u, u))


IDX = "https://openvanilla.app/community/workshop/index.json"


class Workshop(Tmp):
    def zipdata(self):
        return mkzip(addon_entries(prefix="my-addon/"))

    def index(self, files=True, sha=None, url="community/workshop/files/my-addon-abc.zip"):
        data = self.zipdata()
        card = {"id": "my-addon", "name": "My addon", "author": "Me", "summary": "Small.", "kind": W.KIND, "install": True, "license": "MIT", "version": "1.2.0"}
        if files:
            card["files"] = [{"name": "my-addon-abc.zip", "url": url, "sha256": sha or hashlib.sha256(data).hexdigest(), "size": len(data)}]
        idx = {"base": "https://openvanilla.app/", "addons": [{"id": "pfui", "name": "pfUI", "category": "interface"}, card]}
        return {IDX: json.dumps(idx).encode(), "https://openvanilla.app/community/workshop/files/my-addon-abc.zip": data}

    def test_empty_state(self):
        op = FakeOpener({IDX: json.dumps({"addons": [{"id": "pfui", "name": "pfUI"}]}).encode()})
        r = W.browse(IDX, op, set())
        self.assertEqual((r["entries"], r["count"]), ([], 1))
        self.assertEqual(op.asked, [IDX])

    def test_browse_and_install(self):
        op = FakeOpener(self.index())
        r = W.browse(IDX, op, set())
        self.assertEqual([(e["id"], e["installable"], e["installed"]) for e in r["entries"]], [("my-addon", True, False)])
        res = W.install_from(IDX, op, self.assets, self.sfile, "my-addon")
        self.assertEqual(res["installed"], "my-addon")
        self.assertFalse(addons.find(self.assets, self.sfile, "my-addon")["enabled"])
        self.assertTrue(W.browse(IDX, op, {"my-addon"})["entries"][0]["installed"])
        with self.assertRaises(addon_zip.Exists):
            W.install_from(IDX, op, self.assets, self.sfile, "my-addon")

    def test_files_from_the_entry_page(self):
        pages = self.index(files=False)
        data = pages["https://openvanilla.app/community/workshop/files/my-addon-abc.zip"]
        pages["https://openvanilla.app/community/workshop/my-addon.json"] = json.dumps(
            {"files": [{"url": "community/workshop/files/my-addon-abc.zip", "sha256": hashlib.sha256(data).hexdigest()}]}).encode()
        self.assertEqual(W.install_from(IDX, FakeOpener(pages), self.assets, self.sfile, "my-addon")["installed"], "my-addon")

    def test_refusals(self):
        cases = [(self.index(sha="0" * 64), "fingerprint"), (self.index(url="http://evil.example/x.zip"), "https"),
                 (self.index(files=False), "404"), ({IDX: b"<html>"}, "not JSON")]
        for pages, word in cases:
            with self.assertRaises(ValueError) as cm:
                W.install_from(IDX, FakeOpener(pages), self.assets, self.sfile, "my-addon")
            self.assertIn(word, str(cm.exception))
        with self.assertRaises(ValueError):
            W.install_from(IDX, FakeOpener(self.index()), self.assets, self.sfile, "pfui")         # not a Vanilla Agents addon
        with self.assertRaises(ValueError):
            W.install_from(IDX, FakeOpener(self.index()), self.assets, self.sfile, "../x")
        with self.assertRaises(ValueError) as cm:      # redirected to plain http
            W.install_from(IDX, FakeOpener(self.index(), final={IDX: "http://openvanilla.app/x"}), self.assets, self.sfile, "my-addon")
        self.assertIn("non-https", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            W.install_from(IDX, FakeOpener(self.index()), self.assets, self.sfile, "my-addon", lim={**addon_zip.LIMITS, "max_zip_bytes": 50})
        self.assertIn("more than", str(cm.exception))
        with self.assertRaises(ValueError):
            W.browse("http://openvanilla.app/index.json", FakeOpener({}), set())
        self.assertEqual(addons.scan(self.assets, self.sfile), [])        # nothing was installed

    def test_package_id_must_match_entry(self):
        pages = self.index()
        other = mkzip(addon_entries({**GOOD, "id": "other-one"}))
        pages["https://openvanilla.app/community/workshop/files/my-addon-abc.zip"] = other
        idx = json.loads(pages[IDX])
        idx["addons"][1]["files"][0]["sha256"] = hashlib.sha256(other).hexdigest()
        pages[IDX] = json.dumps(idx).encode()
        with self.assertRaises(ValueError) as cm:
            W.install_from(IDX, FakeOpener(pages), self.assets, self.sfile, "my-addon")
        self.assertIn("other-one", str(cm.exception))


class OwnerOnly(Tmp):
    def setUp(self):
        super().setUp()
        self.ctx = server.Ctx(load_all(), str(self.root / "feed.jsonl"), None, assets_dir=self.assets, watch=False, settings_file=self.sfile)
        self.ctx.addons_opener = FakeOpener({IDX: json.dumps({"addons": []}).encode()})
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request(method, path, body, headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def post(self, path, body, key=True):
        h = {"Content-Type": "application/json", **({"X-Vanilla-Key": self.ctx.key} if key else {})}
        st, data = self.req("POST", path, json.dumps(body), h)
        return st, json.loads(data)

    def test_every_post_needs_the_window_key(self):
        for call in addons_api.POST_CALLS:
            self.assertEqual(self.post(call, {"key": "x"}, key=False)[0], 403, call)
            st, _ = self.req("POST", call, "{}", {"Content-Type": "text/plain", "X-Vanilla-Key": self.ctx.key})
            self.assertEqual(st, 404, call)
        self.assertFalse(set(addons_api.POST_CALLS) & set(server.PHONE_CALLS))

    def test_flow_through_the_server(self):
        z = base64.b64encode(mkzip(addon_entries())).decode()
        st, r = self.post("/api/addons/install", {"zip": "data:application/zip;base64," + z})
        self.assertEqual((st, r["result"]["installed"], r["load"]), (200, "my-addon", []))
        self.assertEqual([x["key"] for x in r["addons"]], ["my-addon"])          # the reply lists it already (after the install)
        self.assertEqual(self.req("GET", "/addons/my-addon/main.js")[0], 404)            # off: not served
        st, r = self.post("/api/addons/install", {"zip": z})
        self.assertEqual((st, r.get("exists")), (409, True))
        self.assertEqual(self.post("/api/addons/install", {"zip": z, "replace": True})[0], 200)
        st, r = self.post("/api/addons/enable", {"key": "my-addon", "enabled": True})
        self.assertEqual([a["key"] for a in r["load"]], ["my-addon"])
        self.assertEqual(self.req("GET", "/addons/my-addon/main.js"), (200, b"export default () => {};"))
        self.assertEqual(self.req("GET", "/addons/my-addon/addon.json")[0], 200)
        self.assertEqual(self.req("GET", "/addons/.removed/")[0], 404)
        st, r = self.post("/api/addons/package", {"key": "my-addon"})
        self.assertTrue(st == 200 and r["package"]["zip_name"] == "my-addon-1.2.0.zip")
        self.assertEqual(self.req("GET", "/addons/.packages/my-addon-1.2.0.zip")[0], 404)
        self.assertEqual(self.post("/api/addons/browse", {}), (200, {"url": IDX, "count": 0, "entries": []}))
        st, r = self.post("/api/settings", {"addons": {"enabled": {"my-addon": False}}})
        self.assertEqual(addons.enabled_map(self.sfile)["my-addon"], True)                # /api/settings cannot switch addons
        st, r = self.post("/api/addons/remove", {"key": "my-addon"})
        self.assertEqual((st, r["addons"]), (200, []))
        self.assertIn(".removed", r["moved_to"])
        self.assertEqual(self.post("/api/addons/enable", {"key": "my-addon", "enabled": True})[0], 400)
        self.assertEqual(self.post("/api/addons/install", {"zip": "!!notbase64"})[0], 400)
        self.assertEqual(self.post("/api/addons/install", {"zip": z, "replace": "yes"})[0], 400)

    def test_body_limits(self):
        big = {"key": "x" * 5000}
        self.assertEqual(self.post("/api/addons/enable", big)[0], 400)          # the normal 4 KB limit
        self.assertGreater(addons_api.body_limit(self.ctx.cfg), 2 * 1024 * 1024)
        z = base64.b64encode(mkzip(addon_entries() + [("pad.png", os.urandom(6000))])).decode()
        self.assertEqual(self.post("/api/addons/install", {"zip": z})[0], 200)   # bigger than 4 KB is fine on this route


if __name__ == "__main__":
    unittest.main()
