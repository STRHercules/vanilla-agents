"""Vanilla M2 -> .glb bytes. Game-file access is injected (read_png) so tests can use synthetic bytes."""
import math

from . import gltf, m2v

def w(v):
    """WoW axes (x fwd, y left, z up) -> glTF (-y, z, -x): a proper rotation, model faces -Z."""
    return (-v[1], v[2], -v[0])


def wq(q):
    x, y, z = w(q[:3])
    n = math.sqrt(x * x + y * y + z * z + q[3] ** 2) or 1.0
    return (x / n, y / n, z / n, q[3] / n)


def _clip(name, seq_i, bs, wb):
    """Channels for sequence seq_i: [(bone, path, times_s, flat values, step)]."""
    chans = []
    for i, b in enumerate(bs):
        for path, key in (("translation", "trans"), ("rotation", "rot"), ("scale", "scale")):
            interp, glob, seqs = b[key]
            if glob >= 0 or seq_i >= len(seqs) or not seqs[seq_i][0]:
                continue  # ponytail: global-sequence tracks (looping props) are skipped; fine for characters
            times, vals = seqs[seq_i]
            keep, last = [], -1
            for t, v in zip(times, vals):
                if t > last:
                    keep.append((t, v))
                    last = t
            flat = []
            for _, v in keep:
                if path == "translation":
                    off = gltf.rest_offset(wb, i)
                    flat += [a + c for a, c in zip(off, w(v))]
                elif path == "rotation":
                    flat += wq(v)
                else:
                    flat += (abs(v[1]), abs(v[2]), abs(v[0]))
            ts, n = [t / 1000.0 for t, _ in keep], {"translation": 3, "rotation": 4, "scale": 3}[path]
            if len(ts) > 1 and max(abs(flat[j] - flat[j % n]) for j in range(len(flat))) < 1e-4:
                ts, flat = ts[:1], flat[:n]      # a channel that never moves in this clip: one key is the same pose, a fraction of the bytes
            chans.append((i, path, ts, flat, interp == 0))
    return (name, chans)


def _world_bones(bs):
    return [{"parent": b["parent"], "pivot": w(b["pivot"]), "key": b["key"]} for b in bs]


def convert(m2_bytes, spec, read_png=lambda tex_type, path: None):
    """spec: name, geosets (list or None = all), clips [{name, id}], anim_ids [ids], skinned (default True), view (default 0).
    read_png(texture type, file path) -> png bytes or None. Returns (glb bytes, report dict)."""
    model, rep = mesh(m2_bytes, spec, read_png)
    return gltf.build(model), rep


def mesh(m2_bytes, spec, read_png=lambda tex_type, path: None):
    """The model dict gltf.build() takes (see there), plus a report: verts, tris, bones, images, clips, geosets, seq."""
    m = m2_bytes
    m2v.check(m)
    verts = m2v.vertices(m)
    texs = m2v.textures(m)
    keep = spec.get("geosets")
    keep = None if keep is None else set(keep)
    bl = [b for b in m2v.batches(m, spec.get("view", 0)) if keep is None or b["geoset"] in keep]
    used = {}
    for b in bl:
        for v in b["tris"]:
            used.setdefault(v, len(used))
    order = sorted(used, key=used.get)
    skinned = spec.get("skinned", True)
    seqs = m2v.sequences(m)
    bs, clips, wb, info = [], [], [], {}
    if skinned:
        want = {}
        for c in spec.get("clips", []):
            i = next((i for i, s in enumerate(seqs) if s[0] == c["id"]), None)
            if i is not None:
                want[c["name"]] = i
        seen = {}
        for i, s in enumerate(seqs):          # spec "anim_ids": every variation of these ids, named a<id>_<n> (n = n-th sequence with that id)
            n = seen[s[0]] = seen.get(s[0], -1) + 1
            if s[0] in spec.get("anim_ids", ()):
                want.setdefault("a%d_%d" % (s[0], n), i)
                info["a%d_%d" % (s[0], n)] = {"id": s[0], "var": n, "dur": s[3] - s[2], "flags": s[4], "freq": s[5], "blend": s[8], "next": s[6], "alias": s[7]}
        bs = m2v.bones(m)
        wb = _world_bones(bs)
        clips = [_clip(n, i, bs, wb) for n, i in want.items()]
    pos, nor, uv, jn, wt = [], [], [], [], []
    for gi in order:
        v = verts[gi]
        pos += w(v["pos"])
        nor += w(v["nor"])
        uv += v["uv"]
        if skinned:
            tot = sum(v["w"])
            jn += [min(j, len(bs) - 1) if tot else 0 for j in v["b"]]
            wt += [x / tot for x in v["w"]] if tot else [1.0, 0, 0, 0]
    images, img_ix, mats, mat_ix, prims = [], {}, [], {}, []
    for b in bl:
        typ, path = texs[b["tex"]] if 0 <= b["tex"] < len(texs) else (-1, "")
        key = (typ, path, b["blend"])
        if key not in mat_ix:
            png = read_png(typ, path) if typ >= 0 else None
            if not png and spec.get("drop_untextured"):      # particle-emitter boxes and helper geometry: no texture = not meant to be seen
                mat_ix[key] = None
                continue
            ii = None
            if png:
                ii = img_ix.setdefault((typ, path), len(images))
                if ii == len(images):
                    images.append(png)
            mats.append({"image": ii, "alpha": "OPAQUE" if b["blend"] == 0 else "MASK" if b["blend"] == 1 else "BLEND", "additive": b["blend"] in (3, 4)})
            mat_ix[key] = len(mats) - 1
        if mat_ix[key] is None:
            continue
        prims.append(([used[t] for t in b["tris"]], mat_ix[key]))
    model = {"name": spec["name"], "pos": pos, "nor": nor, "uv": uv, "joints": jn, "weights": wt, "prims": prims, "materials": mats,
             "images": images, "bones": wb if skinned else [], "clips": clips}
    rep = {"verts": len(order), "tris": sum(len(p[0]) // 3 for p in prims), "bones": len(bs), "images": len(images),
           "clips": [c[0] for c in clips if c[1]], "geosets": sorted({b["geoset"] for b in bl}), "seq": info}
    return model, rep
