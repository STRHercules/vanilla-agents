"""V-046: zone light from the game's own lighting tables. Small synthetic WDBC tables built here (no game data): the 1.12 layouts
of Light / LightIntBand / LightFloatBand, keyframes interpolated across midnight, BGRA colours to '#rrggbb', the light chosen by
distance and falloff with the continent default as fallback, the places index carrying "lighting", and the inn's fallback."""
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from vanilla_agents.assets import lighting, places
from vanilla_agents.assets.dbc import DBC

HERE = Path(__file__).resolve().parent.parent
SKY = HERE / "app" / "web" / "workshop3d" / "innsky.js"


def u(v):
    return struct.unpack("<I", struct.pack("<f", v))[0] if isinstance(v, float) else v


def wdbc(rows, fields):
    body = b"".join(struct.pack("<%dI" % fields, *[u(v) for v in r]) for r in rows)
    return b"WDBC" + struct.pack("<4I", len(rows), fields, fields * 4, 1) + body + b"\0"


def bgra(r, g, b):
    """A colour as the file stores it (bytes B, G, R, A) read back as the little-endian uint32 the reader sees."""
    return struct.unpack("<I", bytes([b, g, r, 0]))[0]


def band(row_id, keys):
    times = [t for t, _ in keys] + [0] * (16 - len(keys))
    vals = [v for _, v in keys] + [0] * (16 - len(keys))
    return [row_id, len(keys)] + times + vals


def light(lid, m, x, y, start, end, params):
    """A Light.dbc row for world (x, y): stored as 1/36 yard from the map corner (x <- world Y, z <- world X)."""
    return [lid, m, float((17066.666 - y) * 36), 0.0, float((17066.666 - x) * 36), float(start * 36), float(end * 36), params, params + 1, 0, 0, 0]


def tables(lights, colours):
    """colours = {params: (day sky, night sky)}; every band gets the sky colour (noon) and its night colour (midnight)."""
    ints, floats = [], []
    for p, (day, night) in colours.items():
        for b in range(18):
            ints.append(band(p * 18 - 17 + b, [(0, night), (1440, day)]))
        for b in range(6):
            floats.append(band(p * 6 - 5 + b, [(0, 3600.0), (1440, 18000.0)] if b == 0 else [(1440, 0.5)]))
    return {"Light": wdbc(lights, 12), "LightIntBand": wdbc(ints, 34), "LightFloatBand": wdbc(floats, 34)}


def loaded(raw):
    return lighting.load_tables(lambda n: raw.get(n))


RED, BLUE, GREEN, BLACK = bgra(255, 0, 0), bgra(0, 0, 255), bgra(0, 255, 0), bgra(0, 0, 0)
WORLD = tables([[1, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 10, 11, 0, 0, 0],            # Eastern Kingdoms default (no position, no falloff)
                light(2, 0, -10000, -1000, 100, 300, 20),                     # a big light
                light(3, 0, -10050, -1000, 20, 60, 30),                       # a small one inside it
                light(4, 1, -7000, -3800, 100, 200, 20)],                     # Kalimdor, no default there
               {10: (BLUE, BLACK), 20: (RED, BLACK), 30: (GREEN, BLACK)})


class Layout(unittest.TestCase):
    def test_colours_are_bgra_in_the_file(self):
        self.assertEqual(lighting.hexc(bgra(0xff, 0x80, 0x10)), "#ff8010")
        self.assertEqual(lighting.hexc(0), "#000000")
        t = DBC(WORLD["LightIntBand"])
        self.assertEqual((t.fields, len(t.rows)), (34, 3 * 18))
        self.assertEqual(lighting.hexc(t.by_id()[20 * 18 - 17][19]), "#ff0000")    # params 20, band 0, second keyframe (noon)

    def test_light_positions_back_to_world(self):
        L = {r["id"]: r for r in lighting.light_rows(DBC(WORLD["Light"]))}
        self.assertTrue(L[1]["default"])
        self.assertAlmostEqual(L[2]["x"], -10000, places=1)
        self.assertAlmostEqual(L[2]["y"], -1000, places=1)
        self.assertAlmostEqual(L[2]["end"], 300, places=2)
        self.assertEqual(L[2]["params"][0], 20)

    def test_keyframes_interpolate_and_wrap_past_midnight(self):
        lin = lambda a, b, f: a + (b - a) * f
        times, vals = [720, 2160], [10.0, 30.0]
        self.assertEqual(lighting.sample(times, vals, 1440, lin), 20.0)
        self.assertEqual(lighting.sample(times, vals, 0, lin), 20.0)              # 2160 -> 720 + 2880: halfway at midnight
        self.assertEqual(lighting.sample(times, vals, 2520, lin), 25.0)
        self.assertEqual(lighting.sample(times, vals, 360, lin), 15.0)
        self.assertEqual(lighting.sample([1440], [7.0], 5, lin), 7.0)
        self.assertIsNone(lighting.sample([], [], 5, lin))
        self.assertEqual(lighting.sample([0, 1440], [BLACK, RED], 720, lighting.lerp_int), bgra(128, 0, 0))
        self.assertEqual(lighting.sample([0, 1440], [BLACK, RED], 2160, lighting.lerp_int), bgra(128, 0, 0))   # noon -> next midnight

    def test_nearest_light_by_falloff_and_default(self):
        lights = loaded(WORLD)["_lights"]
        L, w, d = lighting.pick(lights, 0, -10050, -1000)
        self.assertEqual((L["id"], w, d["id"]), (3, 1.0, 1))                      # inside both: the smaller, more specific one
        L, w, _ = lighting.pick(lights, 0, -10090, -1000)                          # 40 from 3 (start 20, end 60): 0.5; 90 from 2: 1
        self.assertEqual((L["id"], w), (2, 1.0))
        L, w, _ = lighting.pick(lights, 0, -10000, -800)                           # 200 from 2: halfway through its falloff
        self.assertEqual(L["id"], 2)
        self.assertAlmostEqual(w, 0.5, places=4)
        L, w, d = lighting.pick(lights, 0, -5000, 0)
        self.assertIsNone(L)
        self.assertEqual(d["id"], 1)
        self.assertEqual(lighting.pick(lights, 1, 0, 0), (None, 0.0, None))


class Zones(unittest.TestCase):
    def test_zone_lighting_samples_three_times(self):
        t = loaded(WORLD)
        rec = lighting.zone_lighting(t, 0, -10050, -1000)
        self.assertEqual((rec["light"], rec["params"], rec["weight"]), (3, 30, 1.0))
        self.assertEqual(rec["day"]["sky"], "#00ff00")
        self.assertEqual(rec["night"]["ambient"], "#000000")
        self.assertEqual(rec["dusk"]["diffuse"], "#006000")                         # 19:30: 900 of the 1440 half-minutes from noon to midnight
        self.assertEqual(rec["day"]["fog_end"], 500.0)                             # 18000 / 36 yards
        self.assertEqual(rec["night"]["fog_end"], 100.0)
        self.assertEqual(set(rec["day"]), {"sky", "sky_top", "ambient", "diffuse", "sun", "fog", "fog_end"})

    def test_partial_light_mixes_with_the_default_and_nothing_falls_back(self):
        t = loaded(WORLD)
        half = lighting.zone_lighting(t, 0, -10000, -800)
        self.assertEqual(half["light"], 2)
        self.assertIn(half["day"]["sky"], ("#7f0080", "#800080", "#80007f"))                           # half red, half the default's blue
        out = lighting.zone_lighting(t, 0, -5000, 0)
        self.assertEqual((out["light"], out["day"]["sky"]), (1, "#0000ff"))       # no light covers it: the continent default
        self.assertIsNone(lighting.zone_lighting(t, 1, 0, 0))                      # no light and no default: nothing

    def test_missing_tables_and_bands(self):
        self.assertIsNone(loaded({k: v for k, v in WORLD.items() if k != "LightFloatBand"}))
        self.assertIsNone(loaded(dict(WORLD, Light=b"nope")))
        broken = dict(WORLD, LightIntBand=wdbc([], 34))
        self.assertIsNone(lighting.zone_lighting(loaded(broken), 0, -10050, -1000))

    def test_folder_bake_skips_zones_without_a_place(self):
        with tempfile.TemporaryDirectory() as d:
            for n, raw in WORLD.items():
                Path(d, n + ".dbc").write_bytes(raw)
            zones = [{"id": "a", "map_id": 0, "pos": [-10050, -1000]}, {"id": "b"}, {"id": "c", "map_id": 1, "pos": [0, 0]}]
            out = lighting.bake_lighting(lighting.from_folder(d), zones, log=lambda m: None)
            self.assertEqual(list(out), ["a"])
            self.assertEqual(lighting.bake_lighting(lighting.from_folder(os.path.join(d, "none")), zones, log=lambda m: None), {})
            dest = os.path.join(d, "out.json")
            with open(os.devnull, "w") as quiet:
                import contextlib
                with contextlib.redirect_stderr(quiet):
                    self.assertEqual(lighting.main([d, dest]), 0)
            self.assertIsInstance(json.loads(Path(dest).read_text()), dict)

    def test_every_zone_has_a_place_in_the_world(self):
        for z in places.load()["zones"]:
            self.assertIn(z.get("map_id"), (0, 1), z["id"])
            self.assertEqual(z["map_id"], {"ek": 0, "kalimdor": 1}[z["continent"]], z["id"])
            x, y = z["pos"]
            self.assertTrue(-17066 < x < 17066 and -17066 < y < 17066, z["id"])

    def test_places_index_carries_lighting_only_when_found(self):
        zones = [{"id": "a", "name": "A", "continent": "ek", "light": "forest", "faction": "alliance", "map_id": 0, "pos": [1, 2]},
                 {"id": "b", "name": "B", "continent": "ek", "light": "gloom", "faction": "alliance"}]
        rec = places.zone_records(zones, {"a": {"light": 2, "day": {}}})
        self.assertEqual(rec[0]["lighting"], {"light": 2, "day": {}})
        self.assertNotIn("lighting", rec[1])
        self.assertNotIn("pos", rec[0])                                            # the index keeps only what the app needs


class Inn(unittest.TestCase):
    def js(self, expr):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        code = "import(%s).then(m=>console.log(JSON.stringify(%s)))" % (json.dumps(SKY.as_uri()), expr)
        return json.loads(subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)

    def test_game_light_becomes_readable_times(self):
        g = {"sky": "#000000", "ambient": "#000000", "diffuse": "#000000", "fog": "#ffffff", "sun": "#ffffff", "fog_end": 2000}
        L = {"day": dict(g, sky="#ffffff", ambient="#68829a", diffuse="#ff8800"), "dusk": g, "night": g}
        T = self.js("m.gameTimes(%s)" % json.dumps(L))
        self.assertEqual(set(T), {"day", "dusk", "night", "far"})
        self.assertEqual(T["far"], 180)                                             # far away only, however far the game's fog is
        for t in ("day", "dusk", "night"):
            bg, top, bottom, k, sc, sk, fog = T[t]
            self.assertGreaterEqual(k, 0.85)                                         # never black indoors
            self.assertGreater(sk, 0)
            self.assertNotEqual(bg, "#000000")
            self.assertNotEqual(bg, "#ffffff")                                       # nor glaring
        self.assertGreater(T["day"][3], T["night"][3])

    def test_missing_or_broken_lighting_falls_back(self):
        self.assertIsNone(self.js("m.gameTimes(null)"))
        self.assertIsNone(self.js("m.gameTimes({day: {sky: '#fff'}})"))
        inn = (HERE / "app" / "web" / "workshop3d" / "inn.js").read_text(encoding="utf-8")
        self.assertIn("gameTimes(lighting), L = G || PLACE_LIGHT[id]", inn)
        self.assertIn("zoneLight(zone.light, zone.lighting)", inn)

    def test_clock_blends_between_times(self):
        T = {"day": ["#ffffff", "#ffffff", "#ffffff", 2, "#ffffff", 1, "#ffffff"], "dusk": ["#808080"] * 3 + [1.5, "#808080", 0.5, "#808080"],
             "night": ["#000000", "#000000", "#000000", 1, "#000000", 0, "#000000"]}
        self.assertEqual(self.js("m.blendAt(%s, 12)" % json.dumps(T))["entry"], T["day"])
        self.assertEqual(self.js("m.blendAt(%s, 2)" % json.dumps(T))["name"], "night")
        dawn = self.js("m.blendAt(%s, 6.5)" % json.dumps(T))
        self.assertEqual((dawn["entry"][0], dawn["entry"][3]), ("#808080", 1.5))
        self.assertEqual(self.js("m.blendAt(%s, 19.5)" % json.dumps(T))["entry"], T["dusk"])


if __name__ == "__main__":
    unittest.main()
