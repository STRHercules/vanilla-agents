"""Zone light from the game's own lighting tables (V-046). Light.dbc places light spheres on each continent; every sphere points at
LightParams rows whose colours (LightIntBand.dbc) and distances (LightFloatBand.dbc) change over the day. For each zone of
places.json ("pos" = where its inn's town is, "map_id" = continent) we pick the light the game would use there and sample it at
noon, dusk and midnight. Output (places/index.json "lighting"): {"light", "params", "weight", "day"|"dusk"|"night": {"sky",
"sky_top", "ambient", "diffuse", "sun", "fog", "fog_end"}} with colours as '#rrggbb' and fog_end in yards.

1.12.1 layouts (wowdev.wiki, checked against the 1.12 tables: field counts, ids, plausible values):
  Light.dbc (12 fields): id, map, x, y, z, falloff start, falloff end, 5 LightParams ids (clear, clear underwater, storm,
    storm underwater, death). Position and falloff are in 1/36 yard; x and z are measured from the map corner, so
    world X (north) = 17066.666 - z / 36 and world Y (west) = 17066.666 - x / 36; y / 36 is the height. A light at 0,0,0 with no
    falloff is the map's default (light 1 for Eastern Kingdoms). 197 of 206 continent lights land inside a WorldMapArea zone this
    way, against 107 with x and z swapped.
  LightIntBand.dbc (34 fields): id, count, 16 times, 16 colours; 18 bands per LightParams, id = params * 18 - 17 + band.
    Times are half-minutes of a 2880 day (720 = 06:00, 1440 = noon); colours are 0x00RRGGBB ints (B, G, R, A in the file).
    Bands: 0 direct (diffuse) light, 1 ambient, 2 sky top, 3 sky middle, 4 middle to horizon, 5 above horizon, 6 horizon,
    7 fog, 8 ?, 9 sun, 10 sun halo, 11 cloud, 12 ?, 13 ?, 14/15 ocean light/dark, 16/17 river light/dark.
  LightFloatBand.dbc (34 fields): same shape with floats, 6 bands per params (id = params * 6 - 5 + band):
    0 fog end (1/36 yard), 1 fog start scaler, 2 glow, 3 cloud density, 4 ?, 5 ?."""
import os
import struct

from .dbc import DBC, DBCError

DAY = 2880
TIMES = {"day": 1440, "dusk": 2340, "night": 0}          # noon, 19:30, midnight
OFFSET = 17066.666
BAND = {"diffuse": 0, "ambient": 1, "sky_top": 3, "sky": 6, "fog": 7, "sun": 9}
F = DBC.float


def light_rows(t):
    """Light.dbc rows -> [{id, map, x, y, start, end, params}] in world coordinates and yards."""
    out = []
    for r in t.rows:
        px, pz, s, e = F(r[2]), F(r[4]), F(r[5]), F(r[6])
        default = px == 0 and pz == 0 and e == 0
        out.append({"id": r[0], "map": r[1], "x": None if default else OFFSET - pz / 36, "y": None if default else OFFSET - px / 36,
                    "start": s / 36, "end": e / 36, "params": list(r[7:12]), "default": default})
    return out


def pick(lights, map_id, x, y):
    """(light, weight, default light): the light the game would use at world (x, y). weight 1 inside falloff start, falling to 0 at
    falloff end; the strongest light wins, a smaller one on a tie (it is the more specific place). None when nothing covers it."""
    default = next((L for L in lights if L["map"] == map_id and L["default"]), None)
    best, bw = None, 0.0
    for L in lights:
        if L["map"] != map_id or L["default"] or L["end"] <= 0:
            continue
        d = ((x - L["x"]) ** 2 + (y - L["y"]) ** 2) ** 0.5
        if d >= L["end"]:
            continue
        w = 1.0 if d <= L["start"] else (L["end"] - d) / max(L["end"] - L["start"], 1e-6)
        if w > bw + 1e-9 or (best is not None and abs(w - bw) <= 1e-9 and L["end"] < best["end"]):
            best, bw = L, w
    return best, bw, default


def keys(row):
    n = min(row[1], 16)
    return list(row[2:2 + n]), list(row[18:18 + n])


def sample(times, values, t, lerp):
    """Value at time t (0..2880) between keyframes, wrapping past midnight; None without keyframes."""
    if not times:
        return None
    if len(times) == 1:
        return values[0]
    pairs = sorted(zip(times, values))
    for i, (k, v) in enumerate(pairs):
        if k > t:
            pk, pv = pairs[i - 1] if i else (pairs[-1][0] - DAY, pairs[-1][1])
            break
    else:
        pk, pv = pairs[-1]
        k, v = pairs[0][0] + DAY, pairs[0][1]
    f = 0.0 if k == pk else (t - pk) / (k - pk)
    return lerp(pv, v, f)


def mix_rgb(a, b, f):
    return tuple(int(round(((a >> s) & 255) * (1 - f) + ((b >> s) & 255) * f)) for s in (16, 8, 0))


def lerp_int(a, b, f):
    r, g, bl = mix_rgb(a, b, f)
    return (r << 16) | (g << 8) | bl


def hexc(v):
    """0x00RRGGBB int (a BGRA colour of the file read little-endian) -> '#rrggbb'."""
    return "#%06x" % (v & 0xFFFFFF)


def params_at(ints, floats, pid, t):
    """{sky, sky_top, ambient, diffuse, sun, fog: int colour, fog_end: yards} of LightParams pid at time t; None when missing."""
    out = {}
    for name, b in BAND.items():
        row = ints.get(pid * 18 - 17 + b)
        v = sample(*keys(row), t, lerp_int) if row else None
        if v is None:
            if name in ("sky", "ambient", "diffuse"):
                return None
            continue
        out[name] = v
    row = floats.get(pid * 6 - 5)
    if row:
        ts, vs = keys(row)
        fe = sample(ts, [F(v) for v in vs], t, lambda a, b, f: a + (b - a) * f)
        if fe is not None:
            out["fog_end"] = round(fe / 36, 1)
    return out


def zone_lighting(tables, map_id, x, y):
    """The "lighting" record of a zone at world (x, y) on continent map_id; tables = {name: DBC} for Light, LightIntBand,
    LightFloatBand. A light covering the place only partly is mixed with the continent default by its weight."""
    lights = tables["_lights"] if "_lights" in tables else light_rows(tables["Light"])
    ints, floats = tables["LightIntBand"].by_id(), tables["LightFloatBand"].by_id()
    L, w, default = pick(lights, map_id, x, y)
    if L is None:
        L, w = default, 1.0
    if L is None:
        return None
    rec = {"light": L["id"], "params": L["params"][0], "weight": round(w, 3)}
    for name, t in TIMES.items():
        a = params_at(ints, floats, L["params"][0], t)
        if a is None:
            return None
        if w < 1 and default is not None:
            b = params_at(ints, floats, default["params"][0], t)
            if b:
                for k in a:
                    if k in b:
                        a[k] = round(b[k] + (a[k] - b[k]) * w, 1) if k == "fog_end" else lerp_int(b[k], a[k], w)
        rec[name] = {k: (v if k == "fog_end" else hexc(v)) for k, v in a.items()}
    return rec


def load_tables(read):
    """read(table name) -> bytes or None. {name: DBC} of the three lighting tables, or None when one is missing or broken."""
    out = {}
    for n in ("Light", "LightIntBand", "LightFloatBand"):
        raw = read(n)
        if raw is None:
            return None
        try:
            out[n] = DBC(raw)
        except (DBCError, struct.error):
            return None
    out["_lights"] = light_rows(out["Light"])
    return out


def from_data_dir(dd):
    return load_tables(lambda n: dd.read("DBFilesClient\\%s.dbc" % n))


def from_folder(path):
    """The tables from a folder of loose .dbc files (Light.dbc, ...): the same bake without the MPQs."""
    def read(n):
        p = os.path.join(path, n + ".dbc")
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return f.read()
    return load_tables(read)


def bake_lighting(tables, zones, log=print):
    """{zone id: lighting} for the zones with "pos" and "map_id" that the tables cover."""
    out = {}
    if not tables:
        log("places: no lighting tables, zones keep the inn's own light")
        return out
    for z in zones:
        pos, m = z.get("pos"), z.get("map_id")
        if not (isinstance(pos, list) and len(pos) == 2 and isinstance(m, int)):
            continue
        rec = zone_lighting(tables, m, float(pos[0]), float(pos[1]))
        if rec:
            out[z["id"]] = rec
    log("places: game light for %d of %d zones" % (len(out), len(zones)))
    return out


def main(argv=None):
    """python3 -m vanilla_agents.assets.lighting <folder with Light.dbc, LightIntBand.dbc, LightFloatBand.dbc> [out.json]:
    the zones' lighting from loose tables, printed (or written) as {zone id: lighting}."""
    import json
    import sys
    from . import places
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(main.__doc__)
        return 2
    rec = bake_lighting(from_folder(args[0]), places.load()["zones"], log=lambda m: print(m, file=sys.stderr))
    text = json.dumps(rec, indent=1)
    if len(args) > 1:
        with open(args[1], "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
