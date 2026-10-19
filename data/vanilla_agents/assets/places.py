"""Zones (V-042): one picture per zone from the game's own world map (12 tiles of 256x256 joined, cut to the painted 1002x668 part,
halved, every explored-area overlay from WorldMapOverlay.dbc drawn on top), plus the two continent loading screens. Output: places/<zone>.png, places/load_<continent>.png and places/index.json
(the zones from places.json with the pictures that were found). Buildings say which zone they are in (buildings.json "zone").
V-046: each zone also gets "lighting", the game's own light at its inn's town (lighting.py, from Light/LightIntBand/LightFloatBand.dbc)."""
import json
import os

from . import blp

HERE = os.path.dirname(os.path.abspath(__file__))
PLACES = os.path.join(HERE, "places.json")
TILE, COLS, ROWS, MAP_W, MAP_H = 256, 4, 3, 1002, 668


def load():
    with open(PLACES, encoding="utf-8") as f:
        return json.load(f)


def join_tiles(tiles):
    """12 decoded tiles [(w, h, rgba)] in reading order -> (MAP_W, MAP_H, rgba) of the painted part, opaque."""
    W = TILE * COLS
    px = bytearray(W * TILE * ROWS * 4)
    for i, (w, h, rgba) in enumerate(tiles):
        if (w, h) != (TILE, TILE):
            raise ValueError("map tile %d is %dx%d, expected %dx%d" % (i + 1, w, h, TILE, TILE))
        ox, oy = (i % COLS) * TILE, (i // COLS) * TILE
        for y in range(TILE):
            o = ((oy + y) * W + ox) * 4
            px[o:o + TILE * 4] = rgba[y * TILE * 4:(y + 1) * TILE * 4]
    out = bytearray(MAP_W * MAP_H * 4)
    for y in range(MAP_H):
        out[y * MAP_W * 4:(y + 1) * MAP_W * 4] = px[y * W * 4:y * W * 4 + MAP_W * 4]
    out[3::4] = b"\xff" * (MAP_W * MAP_H)
    return MAP_W, MAP_H, bytes(out)


def blend(dst, dw, dh, src, sw, sh, ox, oy, cw=None, ch=None):
    """Draw src (rgba, sw x sh) over dst (rgba bytearray, dw x dh) at (ox, oy) with its alpha; only the first cw x ch of src."""
    cw, ch = min(sw, cw or sw), min(sh, ch or sh)
    for y in range(ch):
        dy = oy + y
        if not 0 <= dy < dh:
            continue
        for x in range(cw):
            dx = ox + x
            if not 0 <= dx < dw:
                continue
            si, di = (y * sw + x) * 4, (dy * dw + dx) * 4
            a = src[si + 3]
            if a == 255:
                dst[di:di + 3] = src[si:si + 3]
            elif a:
                for c in range(3):
                    dst[di + c] = (src[si + c] * a + dst[di + c] * (255 - a)) // 255


def overlays(dd):
    """{map folder: [(texture name, width, height, x, y)]}: the explored-area pictures of every zone (WorldMapOverlay.dbc)."""
    from . import dbc
    try:
        areas, over = dbc.load(dd, "WorldMapArea"), dbc.load(dd, "WorldMapOverlay")
    except dbc.DBCError:
        return {}
    names = {r[0]: areas.string(r[3]) for r in areas.rows}
    out = {}
    for r in over.rows:
        name = over.string(r[8])
        if name and r[1] in names:
            out.setdefault(names[r[1]].lower(), []).append((name, r[9], r[10], over.int(r[11]), over.int(r[12])))
    return out


def zone_picture(dd, folder, extra=()):
    """PNG bytes of a zone's map picture with every area explored (about 600x400), or None when the game has no such map."""
    tiles = []
    for n in range(1, COLS * ROWS + 1):
        data = dd.read("Interface\\WorldMap\\%s\\%s%d.blp" % (folder, folder, n))
        if data is None:
            return None
        tiles.append(blp.decode_blp(data))
    w, h, px = join_tiles(tiles)
    px = bytearray(px)
    for name, tw, th, ox, oy in extra:
        cols = -(-tw // TILE)
        for k in range(cols * -(-th // TILE)):
            data = dd.read("Interface\\WorldMap\\%s\\%s%d.blp" % (folder, name, k + 1))
            if data is None:
                continue
            sw, sh, src = blp.decode_blp(data)
            cx, cy = (k % cols) * TILE, (k // cols) * TILE
            blend(px, w, h, src, sw, sh, ox + cx, oy + cy, tw - cx, th - cy)
    return blp.to_png(*blp.halve(w, h, bytes(px), 600))


def zone_records(zones, light):
    """places/index.json zone records: the fields the app needs plus "lighting" when the game's light was found (V-046)."""
    out = []
    for z in zones:
        rec = {k: z[k] for k in ("id", "name", "continent", "light", "faction")}
        if z["id"] in light:
            rec["lighting"] = light[z["id"]]
        out.append(rec)
    return out


def bake_places(dd, out, log=print):
    from .bake import write
    from . import lighting
    p = load()
    zones, extra = [], overlays(dd)
    light = lighting.bake_lighting(lighting.from_data_dir(dd), p["zones"], log)
    for z, rec in zip(p["zones"], zone_records(p["zones"], light)):
        png = None
        for folder in (z["map"], z.get("fallback_map")):
            if folder and png is None:
                try:
                    png = zone_picture(dd, folder, extra.get(folder.lower(), ()))
                except ValueError as e:
                    log("places: %s: %s" % (z["id"], e))
        if png:
            write(os.path.join(out, "places", z["id"] + ".png"), png)
            rec["picture"] = "places/%s.png" % z["id"]
        zones.append(rec)
    loads = {}
    for cont, path in p["loading"].items():
        data = dd.read(path)
        if data:
            write(os.path.join(out, "places", "load_%s.png" % cont), blp.png_of(data, 1024))
            loads[cont] = "places/load_%s.png" % cont
    write(os.path.join(out, "places", "index.json"), json.dumps({"zones": zones, "loading": loads}, indent=1, ensure_ascii=False).encode("utf-8"))
    log("places: %d zones, %d with a picture, %d loading screens" % (len(zones), sum(1 for z in zones if "picture" in z), len(loads)))
    return zones
