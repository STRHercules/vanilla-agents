"""V-042 zones: the zone list and building recipes agree, map tiles join and overlays blend, the places endpoint works before and
after baking, inn pictures are checked before they are saved, and the places step counts as stale once buildings exist."""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from vanilla_agents.app import places_api
from vanilla_agents.assets import bake, places, wmo

HERE = Path(__file__).resolve().parent.parent
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 100


def wj(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


class Recipes(unittest.TestCase):
    def test_every_building_is_in_a_known_zone_and_keys_are_unique(self):
        zones = {z["id"] for z in places.load()["zones"]}
        recipes = json.loads(Path(wmo.BUILDINGS).read_text(encoding="utf-8"))
        keys = [r["key"] for r in recipes]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreaterEqual(len(recipes), 24)
        for r in recipes:
            self.assertIn(r["zone"], zones, r["key"])
            for k in ("label", "town", "inn", "root", "sets", "music"):
                self.assertIn(k, r, r["key"])
            self.assertTrue(r["label"].startswith(r["inn"]), r["key"])     # the Zones window shows the rest of the label as the variant
        self.assertIn("Lion's Pride Inn", {r["inn"] for r in recipes})

    def test_zones_have_what_the_window_needs(self):
        p = places.load()
        for z in p["zones"]:
            self.assertIn(z["continent"], p["loading"], z["id"])
            self.assertIn(z["faction"], ("alliance", "horde", "contested"), z["id"])
            self.assertTrue(z["map"] and z["light"] and z["name"], z["id"])
        self.assertGreaterEqual(len(p["zones"]), 16)

    def test_every_zone_light_exists_in_the_inn(self):
        inn = (HERE / "app" / "web" / "workshop3d" / "inn.js").read_text(encoding="utf-8")
        for z in places.load()["zones"]:
            self.assertTrue(z["light"] == "forest" or ("  " + z["light"] + ": {") in inn, z["light"])

    def test_index_keeps_zone_fields(self):
        self.assertTrue({"zone", "town", "inn"} <= set(wmo.INDEX_KEYS))


class Pictures(unittest.TestCase):
    def test_join_tiles_cuts_the_painted_part(self):
        tiles = [(256, 256, bytes([i, 0, 0, 0]) * 256 * 256) for i in range(12)]
        w, h, px = places.join_tiles(tiles)
        self.assertEqual((w, h, len(px)), (1002, 668, 1002 * 668 * 4))
        self.assertEqual(px[0:4], bytes([0, 0, 0, 255]))                   # alpha forced opaque
        o = (300 * 1002 + 600) * 4                                           # row 1, column 2 -> tile 6
        self.assertEqual(px[o], 6)
        with self.assertRaises(ValueError):
            places.join_tiles([(128, 128, b"\0" * 128 * 128 * 4)] * 12)

    def test_blend_uses_alpha_and_clips(self):
        dst = bytearray(bytes([100, 100, 100, 255]) * 4)                   # 2x2
        src = bytes([200, 0, 0, 255, 200, 0, 0, 0, 0, 200, 0, 128, 9, 9, 9, 255])
        places.blend(dst, 2, 2, src, 2, 2, 0, 0)
        self.assertEqual(dst[0:3], bytes([200, 0, 0]))                     # opaque: replaced
        self.assertEqual(dst[4:7], bytes([100, 100, 100]))                 # transparent: kept
        self.assertEqual(dst[8:11], bytes([49, 150, 49]))                  # half: mixed
        dst2 = bytearray(bytes([1, 1, 1, 255]) * 4)
        places.blend(dst2, 2, 2, src, 2, 2, 1, 1, 1, 1)                    # offset and cut to 1x1: only one pixel changes
        self.assertEqual(dst2, bytearray(bytes([1, 1, 1, 255]) * 3 + bytes([200, 0, 0, 255])))


class Endpoint(unittest.TestCase):
    def test_before_baking_lists_zones_without_pictures(self):
        with tempfile.TemporaryDirectory() as d:
            r = places_api.places(d)
            self.assertEqual(len(r["zones"]), len(places.load()["zones"]))
            self.assertTrue(all("picture" not in z for z in r["zones"]))
            self.assertEqual(r["buildings"], [])

    def test_after_baking_with_photos_and_own_buildings(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "places"))
            os.makedirs(os.path.join(d, "models", "buildings"))
            os.makedirs(os.path.join(d, "office"))
            wj(os.path.join(d, "places", "index.json"), {"zones": [{"id": "elwynn", "name": "Elwynn Forest", "picture": "places/elwynn.png"}], "loading": {"ek": "places/load_ek.png"}})
            wj(os.path.join(d, "models", "buildings", "index.json"), [{"key": "goldshire_c", "label": "Lion's Pride Inn, Goldshire", "zone": "elwynn"}])
            Path(d, "office", "mine.glb").write_bytes(b"")
            places_api.save_photo(d, "goldshire_c", base64.b64encode(JPEG).decode())
            places_api.save_photo(d, "custom:mine", "data:image/jpeg;base64," + base64.b64encode(JPEG).decode())
            r = places_api.places(d)
            self.assertEqual(r["loading"], {"ek": "places/load_ek.png"})
            by = {b["key"]: b for b in r["buildings"]}
            self.assertTrue(by["goldshire_c"]["photo"].startswith("places/photos/goldshire_c.jpg?v="))
            self.assertEqual(by["custom:mine"]["zone"], "own")
            self.assertTrue(by["custom:mine"]["photo"].startswith("places/photos/custom_mine.jpg"))

    def test_photo_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            good = base64.b64encode(JPEG).decode()
            for key, data in (("../x", good), ("a/b", good), ("", good), (5, good), ("ok", "not base64!"), ("ok", base64.b64encode(b"\x89PNG....").decode()),
                              ("ok", "data:image/png;base64," + good), ("ok", base64.b64encode(JPEG + b"\0" * places_api.PHOTO_MAX).decode()), ("ok", None)):
                with self.assertRaises(ValueError, msg=repr((key, str(data)[:20]))):
                    places_api.save_photo(d, key, data)
            self.assertFalse(os.path.exists(os.path.join(d, "places", "photos", "ok.jpg")))
            self.assertEqual(places_api.save_photo(d, "kharanos", good), "places/photos/kharanos.jpg")
            self.assertEqual(Path(d, "places", "photos", "kharanos.jpg").read_bytes(), JPEG)


class Stale(unittest.TestCase):
    def test_places_is_stale_once_buildings_exist(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("places", bake.stale_steps(d))
            os.makedirs(os.path.join(d, "models", "buildings"))
            Path(d, "models", "buildings", "index.json").write_text("[]")
            self.assertIn("places", bake.stale_steps(d))
            wj(os.path.join(d, bake.STAMP), {"buildings": bake.BAKE_VERSIONS["buildings"], "places": 1})
            self.assertEqual(bake.stale_steps(d), ["places"])                 # V-046: places 2 adds the game's light per zone
            wj(os.path.join(d, bake.STAMP), {"buildings": bake.BAKE_VERSIONS["buildings"], "places": bake.BAKE_VERSIONS["places"]})
            self.assertEqual(bake.stale_steps(d), [])


class Http(unittest.TestCase):
    def test_server_routes(self):
        src = (HERE / "vanilla_agents" / "app" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"/api/places"', src)
        self.assertIn('"/api/inn_photo"', src)
        self.assertIn('".jpg"', src)


if __name__ == "__main__":
    unittest.main()
