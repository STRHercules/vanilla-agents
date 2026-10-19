"""Vanilla asset readers. Pure tests use synthetic bytes; the RealData class runs only when a 1.12 Data folder is
configured (VANILLA_DATA, or data_dir in vanilla.local.json) and is skipped otherwise."""
import os
import struct
import tempfile
import unittest
import zlib

from vanilla_agents.app.config import load_all
from vanilla_agents.assets import blp, dbc, mpq, settings


def blp_raw(w, h, px):  # uncompressed BGRA BLP2
    head = b"BLP2" + struct.pack("<IBBBBII", 1, 3, 8, 8, 0, w, h) + struct.pack("<16I", 148 + 1024, *[0] * 15) + struct.pack("<16I", w * h * 4, *[0] * 15)
    return head.ljust(148 + 1024, b"\0") + px


def blp_dxt1(color_bytes):
    head = b"BLP2" + struct.pack("<IBBBBII", 1, 2, 0, 0, 0, 4, 4) + struct.pack("<16I", 148 + 1024, *[0] * 15) + struct.pack("<16I", 8, *[0] * 15)
    return head.ljust(148 + 1024, b"\0") + color_bytes


def encrypt(data, key):
    n = len(data) // 4
    seed, out = 0xEEEEEEEE, []
    for v in struct.unpack("<%dI" % n, data):
        seed = (seed + mpq._CRYPT[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
        out.append(v ^ ((key + seed) & 0xFFFFFFFF))
        key = (((~key << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
        seed = (v + seed + (seed << 5) + 3) & 0xFFFFFFFF
    return struct.pack("<%dI" % n, *out)


def make_mpq(files, sector_shift=3):
    """A minimal v0 archive: files = {name: bytes}; even files are stored zlib-compressed in sectors, odd ones plain."""
    hsize = 16
    body, blocks = bytearray(), []
    sector = 512 << sector_shift
    for i, (name, data) in enumerate(files.items()):
        off = 32 + len(body)
        if i % 2 == 0:
            ns = (len(data) + sector - 1) // sector
            parts = []
            for k in range(ns):            # like the game's packer: a sector that does not shrink is stored as is
                raw = data[k * sector:(k + 1) * sector]
                z = b"\x02" + zlib.compress(raw)
                parts.append(z if len(z) < len(raw) else raw)
            table, pos = [], 4 * (ns + 1)
            for p in parts:
                table.append(pos)
                pos += len(p)
            table.append(pos)
            blob = struct.pack("<%dI" % (ns + 1), *table) + b"".join(parts)
            flags = mpq.FLAG_EXISTS | mpq.FLAG_COMPRESS
        else:
            blob, flags = data, mpq.FLAG_EXISTS
        body += blob
        blocks.append((off, len(blob), len(data), flags))
    hash_entries = [(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)] * hsize
    for i, name in enumerate(files):
        slot = mpq.hash_string(name, 0) % hsize
        while hash_entries[slot][3] != 0xFFFFFFFF:
            slot = (slot + 1) % hsize
        hash_entries[slot] = (mpq.hash_string(name, 1), mpq.hash_string(name, 2), 0, i)
    hpos = 32 + len(body)
    htab = encrypt(b"".join(struct.pack("<4I", *e) for e in hash_entries), mpq.hash_string("(hash table)", 3))
    btab = encrypt(b"".join(struct.pack("<4I", *b) for b in blocks), mpq.hash_string("(block table)", 3))
    bpos = hpos + len(htab)
    total = bpos + len(btab)
    head = struct.pack("<4sIIHHIIII", b"MPQ\x1a", 32, total, 0, sector_shift, hpos, bpos, hsize, len(blocks))
    return head + bytes(body) + htab + btab


class Blp(unittest.TestCase):
    def test_png_roundtrip(self):
        png = blp.to_png(1, 1, bytes((1, 2, 3, 4)))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        i = png.index(b"IDAT")
        self.assertEqual(zlib.decompress(png[i + 4:i + 4 + struct.unpack(">I", png[i - 4:i])[0]]), b"\0\1\2\3\4")

    def test_bgra_and_dxt1(self):
        self.assertEqual(blp.decode_blp(blp_raw(1, 1, bytes((30, 20, 10, 255)))), (1, 1, bytes((10, 20, 30, 255))))
        w, h, px = blp.decode_blp(blp_dxt1(struct.pack("<HHI", 0xF800, 0xF800, 0)))
        self.assertEqual((w, h, px[:4]), (4, 4, bytes((255, 0, 0, 255))))

    def test_halve_box_filters(self):
        w, h, px = blp.halve(2, 2, bytes([0, 0, 0, 0, 4, 4, 4, 4, 8, 8, 8, 8, 12, 12, 12, 12]), 1)
        self.assertEqual((w, h, px), (1, 1, bytes((6, 6, 6, 6))))


class Mpq(unittest.TestCase):
    def test_table_keys_match_the_known_constants(self):
        self.assertEqual(mpq.hash_string("(hash table)", 3), 0xC3AF3770)
        self.assertEqual(mpq.hash_string("(block table)", 3), 0xEC83B3A3)

    def test_read_by_path_any_case_and_slash(self):
        big = bytes(range(256)) * 40          # several sectors
        files = {"Interface\\Icons\\Gear.blp": big, "(listfile)": b"Interface\\Icons\\Gear.blp\r\nFonts\\a.ttf\r\n", "Fonts\\a.ttf": b"TTF"}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "patch.MPQ"), "wb") as f:
                f.write(make_mpq(files))
            with mpq.DataDir(d) as dd:
                self.assertEqual(dd.read("interface/icons/GEAR.BLP"), big)
                self.assertEqual(dd.read("Fonts\\a.ttf"), b"TTF")
                self.assertIsNone(dd.read("nope.blp"))
                self.assertEqual(dd.names("fonts\\*"), ["Fonts\\a.ttf"])

    def test_patch_archives_win(self):
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("base.MPQ", b"old"), ("patch.MPQ", b"mid"), ("patch-2.MPQ", b"new")):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(make_mpq({"a.txt": text}))
            with mpq.DataDir(d) as dd:
                self.assertEqual(dd.read("a.txt"), b"new")

    def test_empty_folder_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(mpq.MPQError):
            mpq.DataDir(d)


class Dbc(unittest.TestCase):
    def test_rows_strings_and_layouts(self):
        strings = b"\0Agamand\0"
        data = b"WDBC" + struct.pack("<4I", 1, 4, 16, len(strings)) + struct.pack("<4I", 195, 1, 1, 0) + strings
        t = dbc.DBC(data)
        self.assertEqual(t.rows, [(195, 1, 1, 0)])
        self.assertEqual(t.named(dbc.COLUMNS["NameGen"]), [{"id": 195, "name": "Agamand", "race": 1, "sex": 0}])


class Settings(unittest.TestCase):
    def test_options_frame_lua_becomes_categories(self):
        gs = settings.global_strings('INVERT_MOUSE = "Invert Mouse";\nOPTION_TOOLTIP_INVERT_MOUSE = "Flip it.";\nMOUSE_SPEED = "Speed";\n')
        lua = ('-- Controls\nUIOptionsFrameCheckButtons["INVERT_MOUSE"] = { index = 1, cvar = "mouseInvertPitch" };\n'
               'UIOptionsFrameSliders = {\n { text = "MOUSE_SPEED", cvar = "mousespeed", minValue = 0.5, maxValue = 1.5, valueStep = 0.05 },\n};\n')
        c = settings.category(lua, "UIOPTIONS_MENU", "Gameplay", gs)
        self.assertEqual([i["kind"] for i in c["items"]], ["header", "checkbox", "slider"])
        self.assertEqual((c["items"][1]["label"], c["items"][1]["tip"], c["items"][1]["var"]), ("Invert Mouse", "Flip it.", "mouseInvertPitch"))
        self.assertEqual((c["items"][2]["min"], c["items"][2]["max"]), (0.5, 1.5))


def _data_dir():
    d = os.environ.get("VANILLA_DATA") or load_all()["app"].get("data_dir", "")
    return d if d and os.path.isdir(d) else None


@unittest.skipUnless(_data_dir(), "no WoW 1.12 Data folder configured")
class RealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dd = mpq.DataDir(_data_dir())

    @classmethod
    def tearDownClass(cls):
        cls.dd.close()

    def test_character_model_has_body_hair_and_animations(self):
        from vanilla_agents.assets import character, export
        t = character.Tables(self.dd)
        look = {"race": 1, "sex": 0, "skin": 1, "face": 2, "hair": 4, "hair_color": 2, "beard": 1, "gear": {}}
        geo = character.geosets(t, look)
        self.assertIn(0, geo)
        glb, rep = export.convert(self.dd.read(character.model_path(1, 0)), {"name": "t", "geosets": sorted(geo), "clips": [{"name": "sit", "id": 103}]},
                                  lambda typ, p: character.textures(t, look).get(typ))
        self.assertEqual(glb[:4], b"glTF")
        self.assertIn("sit", rep["clips"])
        self.assertGreater(rep["tris"], 600)

    def test_inn_building_and_settings(self):
        from vanilla_agents.assets import wmo
        info = wmo.root(self.dd.read(wmo.INN["root"]))
        self.assertGreater(info["groups"], 1)
        self.assertGreater(len(info["doodads"]), 100)
        cats = settings.build(self.dd)["categories"]
        self.assertTrue(cats and all(c["items"] for c in cats))


if __name__ == "__main__":
    unittest.main()
