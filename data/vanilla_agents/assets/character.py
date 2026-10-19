"""Vanilla character looks from the client's own tables: skin / face / hair / facial hair / underwear (CharSections),
hair and beard geosets (CharHairGeosets, CharacterFacialHairStyles), armour textures and geosets (ItemDisplayInfo),
names (NameGen). The 256x256 skin atlas regions are the 1.12 client's fixed layout."""
from . import atlas, blp, dbc

RACES = {1: "Human", 2: "Orc", 3: "Dwarf", 4: "NightElf", 5: "Scourge", 6: "Tauren", 7: "Gnome", 8: "Troll"}
SEXES = {0: "Male", 1: "Female"}
# (x, y, w, h) on the 256x256 skin atlas
REGION = {"AU": (0, 0, 128, 64), "AL": (0, 64, 128, 64), "HA": (0, 128, 128, 32), "FU": (0, 160, 128, 32), "FL": (0, 192, 128, 64),
          "TU": (128, 0, 128, 64), "TL": (128, 64, 128, 32), "LU": (128, 96, 128, 64), "LL": (128, 160, 128, 64), "FO": (128, 224, 128, 32)}
ITEM_DIRS = {"AU": "ArmUpperTexture", "AL": "ArmLowerTexture", "HA": "HandTexture", "TU": "TorsoUpperTexture", "TL": "TorsoLowerTexture",
             "LU": "LegUpperTexture", "LL": "LegLowerTexture", "FO": "FootTexture"}
ITEM_TEX_COLS = ["AU", "AL", "HA", "TU", "TL", "LU", "LL", "FO"]   # ItemDisplayInfo columns 14..21
# body parts an item covers, by which texture regions it fills (used to sort random display ids into slots)
SLOT_REGIONS = {"chest": {"TU"}, "legs": {"LU", "LL"}, "feet": {"FO"}, "hands": {"HA"}}
PAINT_ORDER = ["legs", "feet", "chest", "hands"]


def model_path(race, sex):
    r, s = RACES[race], SEXES[sex]
    return "Character\\%s\\%s\\%s%s.m2" % (r, s, r, s)


class Tables:
    """The handful of DBC tables a look needs, loaded once."""

    def __init__(self, dd):
        self.dd = dd
        self.sections = [r for r in dbc.load(dd, "CharSections") if not r["flags"] & 1]     # flag 1 = NPC-only skins
        self.hair_geo = dbc.load(dd, "CharHairGeosets")
        self.beards = dbc.load(dd, "CharacterFacialHairStyles")
        self.names = dbc.load(dd, "NameGen")
        self.items = dbc.load(dd, "ItemDisplayInfo")
        self.items_by_id = self.items.by_id()

    def rows(self, race, sex, section, variation=None, color=None):
        return [r for r in self.sections if r["race"] == race and r["sex"] == sex and r["section"] == section
                and (variation is None or r["variation"] == variation) and (color is None or r["color"] == color)]

    def item_slots(self):
        """{slot: [display rows]} for armour that paints the body (random gear pool)."""
        if not hasattr(self, "_slots"):
            out = {k: [] for k in SLOT_REGIONS}
            for r in self.items.rows:
                tex = {ITEM_TEX_COLS[i]: self.items.string(r[14 + i]) for i in range(8) if r[14 + i]}
                for slot, need in SLOT_REGIONS.items():
                    if need <= set(tex) and (slot != "chest" or "TL" in tex):
                        out[slot].append({"id": r[0], "tex": tex, "geo": (r[6], r[7], r[8]), "icon": self.items.string(r[5])})
                        break
            self._slots = out
        return self._slots


def random_look(t, rng, race, sex, gear=True):
    """Every customization option picked at random from what the tables allow for this race and sex."""
    skins = sorted({r["color"] for r in t.rows(race, sex, 0, 0)})
    skin = rng.choice(skins)
    faces = sorted({r["variation"] for r in t.rows(race, sex, 1, color=skin)}) or [0]
    hairs = sorted({r["variation"] for r in t.hair_geo if r["race"] == race and r["sex"] == sex})
    hair = rng.choice(hairs) if hairs else 0
    hcolors = sorted({r["color"] for r in t.rows(race, sex, 3, hair)}) or [0]
    beards = sorted({r["variation"] for r in t.beards if r["race"] == race and r["sex"] == sex})
    look = {"race": race, "sex": sex, "skin": skin, "face": rng.choice(faces), "hair": hair, "hair_color": rng.choice(hcolors),
            "beard": rng.choice(beards) if beards else 0, "gear": {}}
    if gear:
        for slot, pool in t.item_slots().items():
            if pool and rng.random() < (0.95 if slot in ("chest", "legs") else 0.7):
                look["gear"][slot] = rng.choice(pool)["id"]
    return look


def geosets(t, look):
    """Geoset ids to draw: body, hair, beard groups, bare hands/feet/ears/legs unless gear changes them."""
    race, sex = look["race"], look["sex"]
    hair = next((r["geoset"] for r in t.hair_geo if (r["race"], r["sex"], r["variation"]) == (race, sex, look["hair"])), 0)
    b = next((r for r in t.beards if (r["race"], r["sex"], r["variation"]) == (race, sex, look["beard"])), None)
    g = {0: 0, 1: 101 + (b["geo1"] if b else 0), 2: 201 + (b["geo3"] if b else 0), 3: 301 + (b["geo2"] if b else 0),
         4: 401, 5: 501, 7: 701, 8: 0, 9: 0, 13: 1301}
    items = t.items_by_id
    for slot, did in look.get("gear", {}).items():
        r = items.get(did)
        if not r:
            continue
        g0, g2 = r[6], r[8]          # geoset groups 1 and 3 of the item (group 2 is unused for these slots)
        if slot == "hands" and g0:
            g[4] = 401 + g0
        elif slot == "feet" and g0:
            g[5] = 501 + g0
        elif slot == "chest":
            if g0:
                g[8] = 801 + g0
            if g2:
                g[13] = 1301 + g2
        elif slot == "legs":
            if g0:
                g[9] = 901 + g0
            if g2:
                g[13] = 1301 + g2
    return {0, hair} | {v for k, v in g.items() if v}     # 0 = the body itself


def _paint(t, px, path, region):
    raw = t.dd.read(path) if path else None
    if not raw:
        return False
    w, h, lp = blp.decode_blp(raw)
    x, y, rw, rh = REGION[region]
    if (w, h) != (rw, rh):          # some layers are stored at another size: nearest-neighbour fit to the region
        lp = bytes(lp[((j * h // rh) * w + (i * w // rw)) * 4 + c] for j in range(rh) for i in range(rw) for c in range(4))
        w, h = rw, rh
    atlas.over(px, 256, lp, w, h, x, y)
    return True


def item_texture(t, name, region, sex):
    base = "Item\\TextureComponents\\%s\\%s" % (ITEM_DIRS[region], name)
    for suffix in ("_%s.blp" % ("F" if sex else "M"), "_U.blp", ".blp"):
        if t.dd.has(base + suffix):
            return base + suffix
    return None


def skin_atlas(t, look):
    """Composited 256x256 RGBA body texture (texture type 1)."""
    race, sex = look["race"], look["sex"]
    base = t.rows(race, sex, 0, 0, look["skin"])
    w, h, px = blp.decode_blp(t.dd.read(base[0]["tex1"]))
    px = bytearray(px)
    if (w, h) != (256, 256):
        raise ValueError("unexpected skin size %dx%d" % (w, h))
    face = t.rows(race, sex, 1, look["face"], look["skin"])
    if face:
        _paint(t, px, face[0]["tex1"], "FL")
        _paint(t, px, face[0]["tex2"], "FU")
    beard = t.rows(race, sex, 2, look["beard"], look["hair_color"])
    if beard:
        _paint(t, px, beard[0]["tex1"], "FL")
        _paint(t, px, beard[0]["tex2"], "FU")
    hair = t.rows(race, sex, 3, look["hair"], look["hair_color"])
    if hair:
        _paint(t, px, hair[0]["tex2"], "FL")
        _paint(t, px, hair[0]["tex3"], "FU")
    under = t.rows(race, sex, 4, 0, look["skin"])
    if under:
        _paint(t, px, under[0]["tex1"], "LU")
        _paint(t, px, under[0]["tex2"], "TU")
    items = t.items_by_id
    for slot in PAINT_ORDER:
        r = items.get(look.get("gear", {}).get(slot))
        if not r:
            continue
        for i, region in enumerate(ITEM_TEX_COLS):
            if r[14 + i]:
                _paint(t, px, item_texture(t, t.items.string(r[14 + i]), region, sex), region)
    return bytes(px)


def hair_texture(t, look):
    hair = t.rows(look["race"], look["sex"], 3, look["hair"], look["hair_color"])
    p = hair[0]["tex1"] if hair and hair[0]["tex1"] else None
    return t.dd.read(p) if p else None


def name_for(t, rng, race, sex, taken=()):
    pool = [r["name"] for r in t.names if r["race"] == race and r["sex"] == sex and r["name"] not in taken]
    return rng.choice(pool) if pool else "%s %d" % (RACES[race], rng.randint(1, 999))


def textures(t, look):
    """{texture type: png bytes} for a model's replaceable textures: 1 body, 6 hair, 8 fur (tauren/fur races use the hair texture)."""
    out = {1: blp.to_png(256, 256, skin_atlas(t, look))}
    hair = hair_texture(t, look)
    if hair:
        out[6] = out[8] = blp.png_of(hair)
    return out
