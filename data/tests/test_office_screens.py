"""V-047: your own office building with live PC screens. office_glb() builds a small synthetic office (a box room, desk_* and seat_*
empties, desks with a PC whose front face uses a material named "Screen") with the project's own glTF writer; the tests check the
file says what the browser looks for (material names, empties, UVs in glTF orientation) and that the building picker lists it.
`python -m tests.test_office_screens <assets>/office` writes office_test.glb (with screens) and office_plain.glb (no desks, no screens)."""
import json
import os
import struct
import sys
import tempfile
import unittest

from vanilla_agents.app import social
from vanilla_agents.assets import gltf

# the six faces of a box: (normal, the four corners as (x, y, z) picks of min/max, u axis, v axis)
FACES = {"+z": (0, 0, 1), "-z": (0, 0, -1), "+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0)}


def _face(lo, hi, side):
    """Corners (counter-clockwise seen from outside) and UVs (glTF: v down, u to the viewer's right) of one face."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    c = {"+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], "-z": [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)],
         "+x": [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)], "-x": [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
         "+y": [(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)], "-y": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]}[side]
    return c, [(0, 1), (1, 1), (1, 0), (0, 0)]


class Mesh:
    """Boxes into one static mesh; each face goes to a material slot."""

    def __init__(self, name, materials):
        self.name, self.materials = name, materials
        self.pos, self.nor, self.uv, self.prims = [], [], [], [[] for _ in materials]

    def box(self, lo, hi, mat=0, mats=None, inward=False):
        for side, n in FACES.items():
            corners, uvs = _face(lo, hi, side)
            i0 = len(self.pos) // 3
            for p, t in zip(corners, uvs):
                self.pos += list(p)
                self.nor += [-c for c in n] if inward else list(n)
                self.uv += list(t)
            tri = [i0, i0 + 1, i0 + 2, i0, i0 + 2, i0 + 3]
            self.prims[(mats or {}).get(side, mat)] += tri[::-1] if inward else tri
        return self

    def model(self):
        return {"name": self.name, "pos": self.pos, "nor": self.nor, "uv": self.uv, "images": [],
                "materials": [dict(m, image=None, alpha="OPAQUE") for m in self.materials], "prims": [(ix, k) for k, ix in enumerate(self.prims)]}


def office_glb(desks=True, screens=True):
    """A 9 x 7 m room (y = 0 floor, 3 m ceiling, a doorway in the front wall); with desks: three desks on the back wall marked desk_1..3,
    each with a PC (a thin box whose +z face uses the material "Screen" when screens=True), and three seat_* empties by the front."""
    W, D, H, T = 4.5, 3.5, 3.0, 0.2
    room = Mesh("room", [{"name": "Floor", "color": [0.55, 0.42, 0.3, 1]}, {"name": "Wall", "color": [0.86, 0.82, 0.74, 1]}, {"name": "Ceiling", "color": [0.7, 0.7, 0.72, 1]}])
    room.box((-W, -0.1, -D), (W, 0, D), 0)
    room.box((-W, H, -D), (W, H + 0.1, D), 2)
    room.box((-W - T, 0, -D - T), (W + T, H, -D), 1)          # back
    room.box((-W - T, 0, -D), (-W, H, D), 1)                  # left
    room.box((W, 0, -D), (W + T, H, D), 1)                    # right
    room.box((-W - T, 0, D), (-0.6, H, D + T), 1)             # front, left of the doorway
    room.box((0.6, 0, D), (W + T, H, D + T), 1)               # front, right of it
    room.box((-0.6, 2.2, D), (0.6, H, D + T), 1)              # over the doorway
    meshes, nodes = [room.model()], [{"name": "Room", "mesh": 0}]
    if desks:
        for i, x in enumerate((-2.8, 0.0, 2.8), 1):
            wz = -D        # the empty's origin sits at the wall, +z into the room
            d = Mesh("desk_%d_furniture" % i, [{"name": "Wood", "color": [0.42, 0.27, 0.15, 1]}])
            d.box((-0.75, 0.70, 0.15), (0.75, 0.76, 1.05)).box((-0.72, 0, 0.2), (-0.66, 0.7, 1.0)).box((0.66, 0, 0.2), (0.72, 0.7, 1.0))
            pc = Mesh("pc_%d" % i, [{"name": "Bezel", "color": [0.08, 0.08, 0.09, 1]}, {"name": "Screen" if screens else "Glass", "color": [0.1, 0.1, 0.12, 1]}])
            pc.box((-0.36, 0.84, 0.40), (0.36, 1.27, 0.44), 0, {"+z": 1}).box((-0.04, 0.76, 0.36), (0.04, 0.86, 0.40)).box((-0.15, 0.76, 0.30), (0.15, 0.775, 0.46))
            for m in (d, pc):
                meshes.append(m.model())
                nodes.append({"name": m.name, "mesh": len(meshes) - 1, "translation": (x, 0, wz)})
            nodes.append({"name": "desk_%d" % i, "translation": (x, 0, wz)})
        for i, x in enumerate((-2.5, 0.0, 2.5), 1):
            nodes.append({"name": "seat_%d" % i, "translation": (x, 0, 2.2), "rotation": (0, 1, 0, 0)})      # facing -z, into the room
    return gltf.build_scene(meshes, nodes)


def glb_json(data):
    n = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + n])


class OfficeScreens(unittest.TestCase):
    def test_screen_material_and_empties(self):
        doc = glb_json(office_glb())
        self.assertEqual(doc["asset"]["version"], "2.0")
        names = [m.get("name") for m in doc["materials"]]
        self.assertEqual(names.count("Screen"), 3)
        nodes = {n["name"]: n for n in doc["nodes"]}
        for i in (1, 2, 3):
            self.assertNotIn("mesh", nodes["desk_%d" % i])                    # an empty
            self.assertIn("seat_%d" % i, nodes)
        scr = [i for i, m in enumerate(doc["materials"]) if m.get("name") == "Screen"]
        prims = [p for m in doc["meshes"] for p in m["primitives"] if p["material"] in scr]
        self.assertEqual(len(prims), 3)
        self.assertTrue(all("TEXCOORD_0" in p["attributes"] for p in prims))
        self.assertEqual(doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"], [0.55, 0.42, 0.3, 1.0])

    def test_plain_building_has_no_desks_or_screens(self):
        doc = glb_json(office_glb(desks=False))
        self.assertFalse([n for n in doc["nodes"] if n["name"].startswith(("desk", "seat"))])
        self.assertNotIn("Screen", [m.get("name") for m in doc["materials"]])
        self.assertFalse(any("Screen" == m.get("name") for m in glb_json(office_glb(screens=False))["materials"]))

    def test_picker_lists_it(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "office"))
            with open(os.path.join(d, "office", "test_office.glb"), "wb") as f:
                f.write(office_glb())
            with open(os.path.join(d, "office", "test_office.json"), "w") as f:
                json.dump({"label": "Test office", "start": [0, 2]}, f)
            got = social.office_buildings(d)
            self.assertEqual([(b["key"], b["label"], b["glb"], b["start"]) for b in got], [("custom:test_office", "Test office", "office/test_office.glb", [0, 2])])


if __name__ == "__main__":
    if len(sys.argv) == 2 and not sys.argv[1].startswith("-"):
        os.makedirs(sys.argv[1], exist_ok=True)
        for name, data in (("office_test", office_glb()), ("office_plain", office_glb(desks=False))):
            with open(os.path.join(sys.argv[1], name + ".glb"), "wb") as f:
                f.write(data)
        print("wrote", sys.argv[1])
    else:
        unittest.main()
