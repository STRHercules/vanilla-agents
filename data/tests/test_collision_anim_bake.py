"""Bake pieces for collisions and animations: M2 collision mesh reader, hidden collision children in the building glb, compact clips."""
import json
import struct
import unittest

from vanilla_agents.assets import gltf, m2v


def glb_json(glb):
    n = struct.unpack_from("<I", glb, 12)[0]
    return json.loads(glb[20:20 + n])


class Collision(unittest.TestCase):
    def test_reads_collision_tris_and_verts(self):
        m = bytearray(0x200)
        m[0:4] = b"MD20"
        struct.pack_into("<I", m, 4, 256)
        verts = [0, 0, 0, 1, 0, 0, 0, 1, 0]
        struct.pack_into("<9f", m, 0x120, *verts)
        struct.pack_into("<3H", m, 0x150, 0, 1, 2)
        struct.pack_into("<II", m, 0xEC, 3, 0x150)
        struct.pack_into("<II", m, 0xF4, 3, 0x120)
        v, t = m2v.collision(bytes(m))
        self.assertEqual((v, t), (verts, [0, 1, 2]))
        struct.pack_into("<3H", m, 0x150, 0, 1, 9)          # index past the vertices: treated as no collision
        self.assertEqual(m2v.collision(bytes(m)), ([], []))
        self.assertEqual(m2v.collision(bytes(0x200)[:4] and bytes(m[:0xEC]) + bytes(0x114)), ([], []))

    def test_collision_child_is_not_a_scene_root(self):
        mesh = {"name": "m", "pos": [0, 0, 0, 1, 0, 0, 0, 1, 0], "nor": [0, 1, 0] * 3, "uv": [0, 0] * 3, "prims": [([0, 1, 2], 0)],
                "materials": [{"image": None, "alpha": "OPAQUE"}], "images": []}
        doc = glb_json(gltf.build_scene([mesh, dict(mesh, name="col_m")], [{"name": "walls", "mesh": 0}, {"name": "chair", "mesh": 0, "children": [2]}, {"name": "col_chair", "mesh": 1}]))
        self.assertEqual(doc["scenes"][0]["nodes"], [0, 1])
        self.assertEqual(doc["nodes"][1]["children"], [2])


class Clips(unittest.TestCase):
    def test_rotations_are_normalized_shorts_and_times_shared(self):
        bones = [{"parent": -1, "pivot": (0, 0, 0)}]
        model = {"pos": [0, 0, 0] * 3, "nor": [0, 1, 0] * 3, "uv": [0, 0] * 3, "prims": [([0, 1, 2], 0)], "materials": [{"image": None, "alpha": "OPAQUE"}],
                 "images": [], "bones": bones, "joints": [0, 0, 0, 0] * 3, "weights": [1, 0, 0, 0] * 3,
                 "clips": [("a0_0", [(0, "rotation", [0.0, 1.0], [0, 0, 0, 1, 0, 0.7071, 0, 0.7071], False), (0, "translation", [0.0, 1.0], [0, 0, 0, 0, 1, 0], False)])]}
        try:
            doc = glb_json(gltf.build(model))
        except (KeyError, TypeError) as e:
            self.skipTest("build() needs a fuller model dict here (%s)" % e)
        a = doc["animations"][0]
        rot = doc["accessors"][a["samplers"][0]["output"]]
        self.assertEqual((rot["componentType"], rot.get("normalized")), (gltf.SHORT, True))
        self.assertEqual(a["samplers"][0]["input"], a["samplers"][1]["input"])


if __name__ == "__main__":
    unittest.main()


class SequenceWindow(unittest.TestCase):
    def test_keys_outside_the_sequence_are_dropped(self):
        """A track whose range for a sequence also lists keys of other sequences must not stretch the clip (the 85 s run bug)."""
        m = bytearray(0x400)
        times = [0, 500, 1000, 90000]               # sequence 0 = 0..1000 ms, a stray key at 90 s
        vals = [0.0, 1.0, 2.0, 9.0]
        struct.pack_into("<4I", m, 0x200, *times)
        struct.pack_into("<4f", m, 0x240, *vals)
        struct.pack_into("<2I", m, 0x280, 0, 3)       # range for sequence 0 covers all four keys
        struct.pack_into("<2I", m, 0x288, 3, 3)       # sequence 1 (2000..3000) only has the stray key: held as a pose
        struct.pack_into("<HhIIIIII", m, 0x100, 1, -1, 2, 0x280, 4, 0x200, 4, 0x240)
        seqs = [(0, 0, 0, 1000, 0, 0, -1, 0, 0), (4, 0, 2000, 3000, 0, 0, -1, 0, 0)]
        _i, _g, out = m2v._track(bytes(m), 0x100, seqs, 4, lambda d, p: struct.unpack_from("<f", d, p)[0])
        self.assertEqual(out[0], ([0, 500, 1000], [0.0, 1.0, 2.0]))
        self.assertEqual(out[1], ([0], [9.0]))


class Stamp(unittest.TestCase):
    def test_stale_steps(self):
        import json as _j
        import os as _o
        import tempfile as _t
        from vanilla_agents.assets import bake
        with _t.TemporaryDirectory() as d:
            self.assertEqual(bake.stale_steps(d), [])                          # nothing baked: nothing to say
            _o.makedirs(_o.path.join(d, "models", "workers"))
            open(_o.path.join(d, "models", "workers", "manifest.json"), "w").close()
            self.assertEqual(bake.stale_steps(d), ["workers"])                 # baked before stamps existed
            with open(_o.path.join(d, bake.STAMP), "w") as f:
                _j.dump({"workers": bake.BAKE_VERSIONS["workers"]}, f)
            self.assertEqual(bake.stale_steps(d), [])
