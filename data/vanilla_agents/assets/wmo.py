"""Vanilla WMO (v17) building -> one .glb: node 0 = the building's walls/floors, then one node per placed doodad, in the
order of <name>.spots.json (the inn renderer finds chairs, tables and benches by these names). Doodad set = the one the
world map uses for this building (Goldshire Inn in Elwynn: set 2) plus the always-on default set 0."""
import json
import os
import struct

from . import blp, export, gltf, m2v

INN = {"root": "World\\wmo\\Azeroth\\Buildings\\GoldshireInn\\GoldshireInn.wmo", "name": "inn", "sets": [0, 2]}


def chunks(data):
    out, p = {}, 0
    while p + 8 <= len(data):
        tag = data[p:p + 4][::-1].decode("latin-1")
        size = struct.unpack_from("<I", data, p + 4)[0]
        out.setdefault(tag, data[p + 8:p + 8 + size])
        p += 8 + size
    return out


def _str(block, off):
    end = block.find(b"\0", off)
    return block[off:end if end >= 0 else None].decode("latin-1")


def root(data):
    c = chunks(data)
    n_groups = struct.unpack_from("<I", c["MOHD"], 4)[0]
    motx = c["MOTX"]
    mats = []
    for i in range(len(c["MOMT"]) // 64):
        flags, _shader, blend, tex1 = struct.unpack_from("<4I", c["MOMT"], i * 64)
        mats.append({"flags": flags, "blend": blend, "tex": _str(motx, tex1)})
    sets = [{"name": c["MODS"][i * 32:i * 32 + 20].split(b"\0")[0].decode(), "start": struct.unpack_from("<I", c["MODS"], i * 32 + 20)[0],
             "count": struct.unpack_from("<I", c["MODS"], i * 32 + 24)[0]} for i in range(len(c["MODS"]) // 32)]
    modn, defs = c["MODN"], []
    for i in range(len(c["MODD"]) // 40):
        name_ofs, x, y, z, qx, qy, qz, qw, sc = struct.unpack_from("<I3f4ff", c["MODD"], i * 40)
        defs.append({"name": _str(modn, name_ofs & 0xFFFFFF), "pos": (x, y, z), "rot": (qx, qy, qz, qw), "scale": sc})
    return {"groups": n_groups, "materials": mats, "sets": sets, "doodads": defs}


def group(data):
    """Render geometry of one group file: verts, normals, uvs, and per-batch (material, triangle indices)."""
    c = chunks(data)
    mogp = c["MOGP"][68:]                    # the group's own sub-chunks follow its 68-byte header
    s = chunks(mogp)
    verts = [struct.unpack_from("<3f", s["MOVT"], i * 12) for i in range(len(s["MOVT"]) // 12)]
    nors = [struct.unpack_from("<3f", s["MONR"], i * 12) for i in range(len(s.get("MONR", b"")) // 12)] or [(0, 0, 1)] * len(verts)
    uvs = [struct.unpack_from("<2f", s["MOTV"], i * 8) for i in range(len(s.get("MOTV", b"")) // 8)] or [(0, 0)] * len(verts)
    idx = struct.unpack_from("<%dH" % (len(s["MOVI"]) // 2), s["MOVI"])
    batches = []
    for i in range(len(s.get("MOBA", b"")) // 24):
        start, count = struct.unpack_from("<IH", s["MOBA"], i * 24 + 12)
        mat = s["MOBA"][i * 24 + 23]
        batches.append((mat, list(idx[start:start + count])))
    return {"verts": verts, "nors": nors, "uvs": uvs, "batches": batches}


def _quat(q):
    return export.wq(q)


def building_mesh(dd, path, info, tex_limit=256):
    base = path[:-4]
    pos, nor, uv, prims, mats, images, mat_ix = [], [], [], [], [], [], {}
    for gi in range(info["groups"]):
        g = group(dd.read("%s_%03d.wmo" % (base, gi)))
        off = len(pos) // 3
        for v, n, t in zip(g["verts"], g["nors"], g["uvs"]):
            pos += export.w(v)
            nor += export.w(n)
            uv += t
        for mat, tris in g["batches"]:
            if mat not in mat_ix:
                m = info["materials"][mat] if mat < len(info["materials"]) else {"tex": "", "blend": 0}
                png = blp.png_of(dd.read(m["tex"]), tex_limit) if m["tex"] and dd.has(m["tex"]) else None
                if png:
                    images.append(png)
                mats.append({"image": len(images) - 1 if png else None, "alpha": "OPAQUE" if m["blend"] == 0 else "MASK" if m["blend"] == 1 else "BLEND"})
                mat_ix[mat] = len(mats) - 1
            prims.append(([off + t for t in tris], mat_ix[mat]))
    merged = {}
    for ix, m in prims:                   # one primitive per material keeps the file and draw calls small
        merged.setdefault(m, []).extend(ix)
    return {"name": "walls", "pos": pos, "nor": nor, "uv": uv, "prims": [(ix, m) for m, ix in merged.items()], "materials": mats, "images": images}


def _collision_mesh(data, m2, meshes):
    """Append the M2's collision triangles as an untextured mesh; its index, or None when the model has none (walk-through)."""
    if not data or "particleemitter" in m2.lower():
        return None
    try:
        verts, tris = m2v.collision(data)
    except (struct.error, ValueError):
        return None
    if not tris:
        return None
    pos = []
    for i in range(0, len(verts), 3):
        pos += export.w(verts[i:i + 3])
    n = len(pos) // 3
    meshes.append({"name": "col_" + m2.split("\\")[-1], "pos": pos, "nor": [0.0, 1.0, 0.0] * n, "uv": [0.0, 0.0] * n,
                   "prims": [(tris, 0)], "materials": [{"image": None, "alpha": "OPAQUE"}], "images": []})
    return len(meshes) - 1


def bake_building(dd, out, spec, log=print, folder="models"):
    path = spec["root"]
    if not dd.has(path):
        log("buildings: %s skipped (%s is not in the data)" % (spec["name"], path))
        return False
    info = root(dd.read(path))
    meshes, nodes, spots, mesh_of, col_of, extra = [building_mesh(dd, path, info)], [{"name": "walls", "mesh": 0}], [], {}, {}, []
    for si in spec["sets"]:
        if si >= len(info["sets"]):
            continue
        st = info["sets"][si]
        for d in info["doodads"][st["start"]:st["start"] + st["count"]]:
            m2 = os.path.splitext(d["name"])[0] + ".m2"
            if m2.lower() not in mesh_of:
                data = None if "particleemitter" in m2.lower() else dd.read(m2)   # smoke/fire emitters: the game draws particles, their M2 box is a placeholder
                mi = None
                if data:
                    try:
                        mdl, _rep = export.mesh(data, {"name": m2.split("\\")[-1], "skinned": False, "drop_untextured": True},
                                                lambda typ, p: blp.png_of(dd.read(p), 128) if p and dd.has(p) else None)
                        meshes.append(mdl)
                        mi = len(meshes) - 1
                    except Exception as e:      # an odd doodad must not stop the building
                        log("inn: skipped %s (%s)" % (m2, e))
                mesh_of[m2.lower()] = mi
                col_of[m2.lower()] = _collision_mesh(data, m2, meshes)
            node = {"name": m2.split("\\")[-1], "mesh": mesh_of[m2.lower()], "translation": export.w(d["pos"]),
                    "rotation": _quat(d["rot"]), "scale": [d["scale"]] * 3}
            if col_of[m2.lower()] is not None:       # the game's own collision shape, a hidden child (placed after all doodads)
                extra.append({"name": "col_" + m2.split("\\")[-1], "mesh": col_of[m2.lower()], "parent": len(nodes)})
            nodes.append(node)
            spots.append({"name": m2.replace("\\", "/").lower(), "pos": export.w(d["pos"]), "scale": d["scale"]})
    for e in extra:                  # children go after every doodad so inn.children[1 + q] still means doodad q
        nodes[e["parent"]].setdefault("children", []).append(len(nodes))
        nodes.append({"name": e["name"], "mesh": e["mesh"]})
    glb = gltf.build_scene(meshes, nodes)
    from .bake import write
    write(os.path.join(out, folder, spec["name"] + ".glb"), glb)
    write(os.path.join(out, folder, spec["name"] + ".spots.json"), json.dumps(spots, indent=0).encode())
    log("%s: %s %d KB, %d doodads, %d unique models" % (folder.split("/")[-1], spec["name"], len(glb) // 1024, len(spots), len(meshes) - 1))
    return True


BUILDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buildings.json")


INDEX_KEYS = ("key", "label", "zone", "town", "inn", "start", "desk_y", "music")     # what the picker and the Zones window get (V-042)


def bake_buildings(dd, out, log=print, only=()):
    """Every building in buildings.json -> models/buildings/<key>.glb + spots, and models/buildings/index.json for the picker."""
    with open(BUILDINGS, encoding="utf-8") as f:
        recipes = json.load(f)
    index = []
    for r in recipes:
        glb = os.path.join(out, "models", "buildings", r["key"] + ".glb")
        if only and r["key"] not in only:
            ok = os.path.exists(glb)           # baked earlier: stays in the picker
        else:
            ok = bake_building(dd, out, {"root": r["root"], "name": r["key"], "sets": r["sets"]}, log, folder="models/buildings")
        if ok:
            index.append({k: r[k] for k in INDEX_KEYS if k in r})
    from .bake import write
    write(os.path.join(out, "models", "buildings", "index.json"), json.dumps(index, indent=1).encode())
    return index


def bake_inn(dd, out, log=print):
    bake_building(dd, out, INN, log)
