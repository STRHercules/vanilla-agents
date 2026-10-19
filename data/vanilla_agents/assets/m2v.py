"""Vanilla (1.12, M2 version 256) model reader: one self-contained MD20 file with inline views (skins) and inline
animation keys. Produces the shapes export.py turns into a .glb.
Layout: wowdev.wiki M2 (pre-Wrath header, 28-byte M2Track with interpolation ranges, 108-byte bones, 68-byte sequences)."""
import struct

_U16, _U32 = struct.Struct("<H"), struct.Struct("<I")


def u16(d, p):
    return _U16.unpack_from(d, p)[0]


def u32(d, p):
    return _U32.unpack_from(d, p)[0]

H = {"sequences": 0x1C, "bones": 0x34, "vertices": 0x44, "views": 0x4C, "textures": 0x5C, "materials": 0x84,
     "texture_lookup": 0x94, "collision_tris": 0xEC, "collision_verts": 0xF4, "attachments": 0x104}


def arr(m, key):
    o = H[key]
    return u32(m, o), u32(m, o + 4)


def check(m):
    if m[:4] != b"MD20":
        raise ValueError("not an M2 file")
    ver = u32(m, 4)
    if not 256 <= ver <= 263:
        raise ValueError("M2 version %d is not a pre-Wrath model (vanilla is 256)" % ver)
    return ver


def vertices(m):
    n, o = arr(m, "vertices")
    out = []
    for i in range(n):
        f = struct.unpack_from("<3f4B4B3f2f2f", m, o + i * 48)
        out.append({"pos": f[0:3], "w": f[3:7], "b": f[7:11], "nor": f[11:14], "uv": f[14:16]})
    return out


def collision(m):
    """The model's own collision mesh (what the game bumps into): ([x, y, z, ...], [i0, i1, i2, ...]) in model space, or ([], []).
    Many small props (rugs, bottles, candles) have none: in the game you walk through them."""
    ni, oi = arr(m, "collision_tris")
    nv, ov = arr(m, "collision_verts")
    if not ni or not nv or oi + ni * 2 > len(m) or ov + nv * 12 > len(m):
        return [], []
    verts = list(struct.unpack_from("<%df" % (nv * 3), m, ov))
    tris = list(struct.unpack_from("<%dH" % ni, m, oi))
    tris = tris[:len(tris) - len(tris) % 3]
    if any(t >= nv for t in tris):
        return [], []
    return verts, tris


def textures(m):
    """[(type, path)] ; type 0 = a file path, 1 = body skin, 2 = cape/object skin, 6 = hair, 8 = fur, 11-13 = creature skins."""
    n, o = arr(m, "textures")
    out = []
    for i in range(n):
        typ, _flags, ln, off = struct.unpack_from("<4I", m, o + i * 16)
        name = m[off:off + ln].split(b"\0")[0].decode("latin-1") if typ == 0 and ln else ""
        out.append((typ, name))
    return out


def _submesh_size(m, view):
    """Vanilla skin sections are 32 bytes; later pre-Wrath files use 48. Pick the one whose index ranges fit."""
    ns, os_ = u32(m, view + 24), u32(m, view + 28)
    ni = u32(m, view + 8)
    for size in (32, 48):
        ok = ns and all(u16(m, os_ + i * size + 8) + u16(m, os_ + i * size + 10) <= ni for i in range(ns))
        if ok:
            return size
    return 32


def batches(m, view_index=0):
    """Render batches of one inline view: dict(geoset, tris (global vertex indices), tex (index into textures()), blend, flags)."""
    nv, ov = arr(m, "views")
    if view_index >= nv:
        return []
    v = ov + view_index * 44
    nl, ol, ni, oi = u32(m, v), u32(m, v + 4), u32(m, v + 8), u32(m, v + 12)
    ns, os_, nb, ob = u32(m, v + 24), u32(m, v + 28), u32(m, v + 32), u32(m, v + 36)
    size = _submesh_size(m, v)
    nmat, omat = arr(m, "materials")
    ntl, otl = arr(m, "texture_lookup")
    lookup = struct.unpack_from("<%dH" % nl, m, ol)
    idx = struct.unpack_from("<%dH" % ni, m, oi)
    out = []
    for b in range(nb):
        bp = ob + b * 24
        sub, mat, combo = u16(m, bp + 4), u16(m, bp + 10), u16(m, bp + 16)
        if sub >= ns:
            continue
        sp = os_ + sub * size
        start, cnt = u16(m, sp + 8), u16(m, sp + 10)
        if start + cnt > len(idx):
            continue
        out.append({"geoset": u16(m, sp), "tris": [lookup[t] for t in idx[start:start + cnt]],
                    "tex": u16(m, otl + combo * 2) if combo < ntl else -1,
                    "flags": u16(m, omat + mat * 4) if mat < nmat else 0, "blend": u16(m, omat + mat * 4 + 2) if mat < nmat else 0})
    return out


def sequences(m):
    """[(id, variation, start_ms, end_ms, flags, frequency, next_variation, alias, blend_ms)] (68-byte records, timeline-absolute times)."""
    n, o = arr(m, "sequences")
    out = []
    for i in range(n):
        p = o + i * 68
        sid, var, start, end = struct.unpack_from("<HHII", m, p)
        flags = u32(m, p + 16)
        freq = struct.unpack_from("<h", m, p + 20)[0]
        nxt, alias = struct.unpack_from("<hH", m, p + 64)
        out.append((sid, var, start, end, flags, freq, nxt, alias, u32(m, p + 32)))
    return out


def _quat_float(d, p):
    return struct.unpack_from("<4f", d, p)


def _track(m, p, seqs, width, rd):
    """-> (interp, global_seq, [(times relative to the sequence start, values) per sequence])."""
    interp, glob = u16(m, p), struct.unpack_from("<h", m, p + 2)[0]
    nr, orr, nt, ot, nv, ov = struct.unpack_from("<6I", m, p + 4)
    times = struct.unpack_from("<%dI" % nt, m, ot) if nt else ()
    if nt != nv:
        return interp, glob, [([], []) for _ in seqs]
    out = []
    if glob >= 0:              # global sequence (looping props): one key list on its own clock
        return interp, glob, [(list(times), [rd(m, ov + k * width) for k in range(nv)])]
    if nr == 0:                # no per-sequence ranges: no animation on this track
        return interp, glob, [([], []) for _ in seqs]
    for i, s in enumerate(seqs):
        if i >= nr:
            out.append(([], []))
            continue
        a, b = struct.unpack_from("<2I", m, orr + i * 8)
        if b < a or b >= nt:
            out.append(([], []))
            continue
        start, end = s[2], s[3]
        # only keys inside this sequence's own time window: some tracks list keys of other sequences in the range, and one stray key
        # 85 s down the timeline stretches the clip so the game's 0.67 s run plays as a near-still pose
        ks = [k for k in range(a, b + 1) if start <= times[k] <= end]
        if not ks:                  # nothing inside: hold the last key before the start (or the range's first) as a pose
            before = [k for k in range(a, b + 1) if times[k] <= start]
            ks = [before[-1] if before else a]
            out.append(([0], [rd(m, ov + ks[0] * width)]))
            continue
        out.append(([times[k] - start for k in ks], [rd(m, ov + k * width) for k in ks]))
    return interp, glob, out


def bones(m):
    """108-byte bone records -> [{key, parent, pivot, trans, rot, scale}]."""
    n, o = arr(m, "bones")
    seqs = sequences(m)
    v3 = lambda d, p: struct.unpack_from("<3f", d, p)
    res = []
    for i in range(n):
        p = o + i * 108
        res.append({"key": struct.unpack_from("<i", m, p)[0], "parent": struct.unpack_from("<h", m, p + 8)[0],
                    "trans": _track(m, p + 12, seqs, 12, v3), "rot": _track(m, p + 40, seqs, 16, _quat_float),
                    "scale": _track(m, p + 68, seqs, 12, v3), "pivot": v3(m, p + 96)})
    return res


def attachments(m):
    """{attachment id: (bone index, (x, y, z))} from 48-byte records."""
    n, o = arr(m, "attachments")
    out = {}
    for i in range(n):
        p = o + i * 48
        out[u32(m, p)] = (u16(m, p + 4), struct.unpack_from("<3f", m, p + 8))
    return out
