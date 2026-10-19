"""BLP2 textures (palette, raw BGRA, DXT1/3/5; mip 0) -> RGBA, and RGBA -> PNG. Standard library only."""
import struct
import zlib


def _565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def _dxt_color(blk, opaque4):
    c0, c1, bits = struct.unpack_from("<HHI", blk)
    a, b = _565(c0), _565(c1)
    if c0 > c1 or opaque4:
        pal = [a + (255,), b + (255,), tuple((2 * x + y) // 3 for x, y in zip(a, b)) + (255,), tuple((x + 2 * y) // 3 for x, y in zip(a, b)) + (255,)]
    else:
        pal = [a + (255,), b + (255,), tuple((x + y) // 2 for x, y in zip(a, b)) + (255,), (0, 0, 0, 0)]
    return [pal[(bits >> (2 * i)) & 3] for i in range(16)]


def _dxt_alpha(blk, kind):
    if kind == 1:  # DXT3: explicit 4-bit
        return [((blk[i // 2] >> (4 * (i & 1))) & 15) * 17 for i in range(16)]
    a0, a1 = blk[0], blk[1]  # DXT5
    t = [a0, a1] + ([(( 6 - i) * a0 + (i + 1) * a1) // 7 for i in range(6)] if a0 > a1 else [((4 - i) * a0 + (i + 1) * a1) // 5 for i in range(4)] + [0, 255])
    bits = int.from_bytes(blk[2:8], "little")
    return [t[(bits >> (3 * i)) & 7] for i in range(16)]


def decode_blp(d):
    """BLP2 mip 0 -> (w, h, RGBA bytes). Supports palette, raw BGRA, DXT1/3/5."""
    if d[:4] != b"BLP2":
        raise ValueError("not BLP2")
    _, enc, adepth, aenc, _, w, h = struct.unpack_from("<IBBBBII", d, 4)
    off, size = struct.unpack_from("<I", d, 20)[0], struct.unpack_from("<I", d, 84)[0]
    pal = d[148:148 + 1024]
    data = d[off:off + size]
    px = bytearray(w * h * 4)
    if enc == 1:
        for i in range(w * h):
            b, g, r, _ = pal[data[i] * 4:data[i] * 4 + 4]
            a = 255 if adepth == 0 else data[w * h + i] if adepth == 8 else ((data[w * h + i // 2] >> (4 * (i & 1))) & 15) * 17 if adepth == 4 else 255
            px[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    elif enc == 3:
        for i in range(w * h):
            b, g, r, a = data[i * 4:i * 4 + 4]
            px[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    elif enc == 2:
        kind = {0: 0, 1: 1, 7: 2}[aenc]  # DXT1 / DXT3 / DXT5
        bs = 8 if kind == 0 else 16
        p = 0
        for by in range((h + 3) // 4):
            for bx in range((w + 3) // 4):
                blk = data[p:p + bs]
                p += bs
                col = _dxt_color(blk[bs - 8:], kind != 0)
                al = None if kind == 0 else _dxt_alpha(blk[:8], kind)
                for i in range(16):
                    x, y = bx * 4 + i % 4, by * 4 + i // 4
                    if x < w and y < h:
                        r, g, b, a = col[i]
                        px[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((r, g, b, a if al is None else al[i]))
    else:
        raise ValueError(f"unsupported BLP encoding {enc}")
    return w, h, bytes(px)


def to_png(w, h, rgba):
    def chunk(t, b):
        c = struct.pack(">I", len(b)) + t + b
        return c + struct.pack(">I", zlib.crc32(t + b))
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def halve(w, h, px, limit):
    """Box-filter down until the longest side <= limit (keeps glb files small; limit 0 = off)."""
    while limit and max(w, h) > limit and w % 2 == 0 and h % 2 == 0:
        src, nw = px, w // 2
        out = bytearray(nw * (h // 2) * 4)
        for y in range(0, h, 2):
            r0, r1 = y * w * 4, (y + 1) * w * 4
            o = (y // 2) * nw * 4
            for x in range(0, w * 4, 8):
                for c in range(4):
                    out[o + c] = (src[r0 + x + c] + src[r0 + x + 4 + c] + src[r1 + x + c] + src[r1 + x + 4 + c]) >> 2
                o += 4
        px, w, h = bytes(out), nw, h // 2
    return w, h, px


def png_of(blp, limit=0):
    """BLP bytes -> PNG bytes (None stays None)."""
    return to_png(*halve(*decode_blp(blp), limit)) if blp else None
