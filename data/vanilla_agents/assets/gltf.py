"""Minimal glTF 2.0 binary (.glb) writer: one skinned or static mesh, PNG textures, node-hierarchy bones, animation clips."""
import json
import struct

FLOAT, USHORT, UINT, SHORT = 5126, 5123, 5125, 5122


class Glb:
    def __init__(self):
        self.bin = bytearray()
        self.views, self.accessors = [], []

    def _view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, values, ctype, kind, target=None, minmax=False):
        """values = flat list; kind = SCALAR/VEC2/VEC3/VEC4. Returns accessor index."""
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[kind]
        fmt = {FLOAT: "f", USHORT: "H", UINT: "I", SHORT: "h"}[ctype]
        a = {"bufferView": self._view(struct.pack("<%d%s" % (len(values), fmt), *values), target), "componentType": ctype,
             "count": len(values) // n, "type": kind}
        if ctype == SHORT:
            a["normalized"] = True
        if minmax and values:
            cols = [values[i::n] for i in range(n)]
            a["min"], a["max"] = [min(c) for c in cols], [max(c) for c in cols]
        self.accessors.append(a)
        return len(self.accessors) - 1

    def image(self, png):
        return self._view(png)

    def pack(self, doc):
        doc = dict(doc, buffers=[{"byteLength": len(self.bin)}], bufferViews=self.views, accessors=self.accessors,
                   asset={"version": "2.0", "generator": "vanilla-agents"})
        j = json.dumps(doc, separators=(",", ":")).encode()
        j += b" " * (-len(j) % 4)
        b = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
        return b"glTF" + struct.pack("<II", 2, 12 + 8 + len(j) + 8 + len(b)) + struct.pack("<I4s", len(j), b"JSON") + j + struct.pack("<I4s", len(b), b"BIN\0") + b


def build(model):
    """model: name, pos/nor/uv flat lists (world axes), joints/weights flat (or None), prims [(indices, material)],
    materials [{image: index or None, alpha: OPAQUE|MASK|BLEND}], images [png bytes],
    bones [{parent, pivot (world), key}] or [], clips [(name, [(bone, path, times_s, values flat, step)])]. Returns .glb bytes."""
    g = Glb()
    attrs = {"POSITION": g.accessor(model["pos"], FLOAT, "VEC3", 34962, True), "NORMAL": g.accessor(model["nor"], FLOAT, "VEC3", 34962),
             "TEXCOORD_0": g.accessor(model["uv"], FLOAT, "VEC2", 34962)}
    bones = model.get("bones") or []
    if bones:
        attrs["JOINTS_0"] = g.accessor(model["joints"], USHORT, "VEC4", 34962)
        attrs["WEIGHTS_0"] = g.accessor(model["weights"], FLOAT, "VEC4", 34962)
    prims = [{"attributes": attrs, "indices": g.accessor(ix, UINT, "SCALAR", 34963), "material": mat} for ix, mat in model["prims"]]
    imgs = [{"bufferView": g.image(p), "mimeType": "image/png"} for p in model["images"]]
    mats = []
    for m in model["materials"]:
        d = {"pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}, "alphaMode": m["alpha"], "doubleSided": True}
        if m["alpha"] == "MASK":
            d["alphaCutoff"] = 0.5
        if m["image"] is not None:
            d["pbrMetallicRoughness"]["baseColorTexture"] = {"index": m["image"]}
        if m.get("additive"):
            d["extras"] = {"additive": True}       # M2 blend 3/4 (glows, flames): the browser draws these with additive blending
        mats.append(d)
    nb = len(bones)
    nodes = [{"name": "b%d_k%d" % (i, b["key"]), "translation": [round(c, 6) for c in _rest(bones, i)]} for i, b in enumerate(bones)]
    for i, b in enumerate(bones):
        if b["parent"] >= 0:
            nodes[b["parent"]].setdefault("children", []).append(i)
    mesh_node = {"name": model["name"], "mesh": 0}
    doc = {"scene": 0, "meshes": [{"name": model["name"], "primitives": prims}], "materials": mats, "images": imgs,
           "textures": [{"source": i, "sampler": 0} for i in range(len(imgs))],
           "samplers": [{"magFilter": 9728, "minFilter": 9728, "wrapS": 10497, "wrapT": 10497}]}
    if nb:
        ibm = []
        for b in bones:
            x, y, z = b["pivot"]
            ibm += [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -x, -y, -z, 1]
        mesh_node["skin"] = 0
        doc["skins"] = [{"joints": list(range(nb)), "inverseBindMatrices": _mat4(g, ibm)}]
    nodes.append(mesh_node)
    doc["nodes"] = nodes
    doc["scenes"] = [{"nodes": [i for i, b in enumerate(bones) if b["parent"] < 0] + [nb]}]
    anims, time_acc = [], {}
    for name, chans in model.get("clips", []):
        samplers, channels = [], []
        for bone, path, times, vals, step in chans:
            kind = "VEC4" if path == "rotation" else "VEC3"
            key = tuple(times)              # most channels of a clip share their key times: one accessor for all of them
            if key not in time_acc:
                time_acc[key] = g.accessor(times, FLOAT, "SCALAR", None, True)
            # rotations are unit quaternions: normalized 16-bit numbers (core glTF) are exact enough and half the size of floats
            out = g.accessor([max(-32767, min(32767, round(v * 32767))) for v in vals], SHORT, kind) if path == "rotation" else g.accessor(vals, FLOAT, kind)
            samplers.append({"input": time_acc[key], "output": out, "interpolation": "STEP" if step else "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": bone, "path": path}})
        if channels:
            anims.append({"name": name, "samplers": samplers, "channels": channels})
    if anims:
        doc["animations"] = anims
    return g.pack(doc)


def _mat4(g, flat):
    i = g.accessor(flat, FLOAT, "SCALAR")
    g.accessors[i]["type"], g.accessors[i]["count"] = "MAT4", len(flat) // 16
    return i


def _rest(bones, i):
    b = bones[i]
    if b["parent"] < 0:
        return b["pivot"]
    return tuple(a - c for a, c in zip(b["pivot"], bones[b["parent"]]["pivot"]))


rest_offset = _rest


def build_scene(meshes, nodes):
    """Several static meshes placed by root nodes (a building and its furniture). meshes = model dicts like build() takes
    (no bones; a material may also carry "name" and "color"); nodes = [{name, mesh (index or None), translation, rotation (x, y, z, w), scale}] in scene order."""
    g = Glb()
    out_meshes, mats, imgs = [], [], []
    for mdl in meshes:
        attrs = {"POSITION": g.accessor(mdl["pos"], FLOAT, "VEC3", 34962, True), "NORMAL": g.accessor(mdl["nor"], FLOAT, "VEC3", 34962),
                 "TEXCOORD_0": g.accessor(mdl["uv"], FLOAT, "VEC2", 34962)}
        i0, m0 = len(imgs), len(mats)
        imgs += [{"bufferView": g.image(p), "mimeType": "image/png"} for p in mdl["images"]]
        for m in mdl["materials"]:
            d = {"pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}, "alphaMode": m["alpha"], "doubleSided": True}
            if m["alpha"] == "MASK":
                d["alphaCutoff"] = 0.5
            if m["image"] is not None:
                d["pbrMetallicRoughness"]["baseColorTexture"] = {"index": i0 + m["image"]}
            if m.get("additive"):
                d["extras"] = {"additive": True}
            if m.get("name"):            # V-047: a named material (an office GLB's "Screen")
                d["name"] = str(m["name"])
            if m.get("color"):           # plain colour [r, g, b, a] (0..1) for untextured parts
                d["pbrMetallicRoughness"]["baseColorFactor"] = [float(c) for c in m["color"]]
            mats.append(d)
        prims = [{"attributes": attrs, "indices": g.accessor(ix, UINT, "SCALAR", 34963), "material": m0 + mat} for ix, mat in mdl["prims"] if ix]
        out_meshes.append({"name": mdl["name"], "primitives": prims})
    doc_nodes, kids = [], set()
    for n in nodes:
        d = {"name": n["name"]}
        if n.get("children"):            # e.g. a doodad's hidden collision mesh, placed with its parent
            d["children"] = list(n["children"])
            kids.update(n["children"])
        if n.get("mesh") is not None and out_meshes[n["mesh"]]["primitives"]:
            d["mesh"] = n["mesh"]
        for k in ("translation", "rotation", "scale"):
            if n.get(k) is not None:
                d[k] = [round(float(c), 6) for c in n[k]]
        doc_nodes.append(d)
    doc = {"scene": 0, "scenes": [{"nodes": [i for i in range(len(doc_nodes)) if i not in kids]}], "nodes": doc_nodes, "meshes": out_meshes, "materials": mats,
           "images": imgs, "textures": [{"source": i, "sampler": 0} for i in range(len(imgs))],
           "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]}
    return g.pack(doc)
