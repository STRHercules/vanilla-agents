"""Bake everything the program shows from a WoW 1.12 Data folder (the .MPQ files) into the assets folder (outside git).
Private use only: the art stays on this PC. Every step is idempotent; re-run a step after changing its recipe.

    python -m vanilla_agents.assets bake [skin|ui|room|models|workers|music|inn|all] [--data-dir D] [--out A]"""
import json
import os
import random
import re
import shutil

from . import blp, character, export
from .mpq import DataDir

HERE = os.path.dirname(os.path.abspath(__file__))
RECIPES = os.path.join(HERE, "models.json")
# AnimationData ids baked for every hero: stand walk run strafe back jump fall, sit on the ground (down/loop/up), sit in a chair (low/med/high),
# and the emotes the inn plays: talk, eat, work, bow, wave, cheer, laugh, point, yes, no
# every unarmed character animation the workshop can use (weapon, bow, rifle, mount, swim and fishing ids left out to keep files small):
# moving, jumping/falling, turning in place, all emotes, sitting/sleeping/kneeling, reading (UseStanding), spell casting, combat-ready, death
ANIM_IDS = [0, 1, 3, 4, 5, 8, 11, 12, 13, 14, 16, 25, 30, 37, 38, 39, 40, 50, 51, 52, 53, 54, 55, 56, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70,
            73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 113, 114, 115, 116, 119, 120, 121,
            123, 124, 125, 128, 129, 136, 143, 185, 186, 187, 195, 199]
CLIPS = [{"name": "stand", "id": 0}, {"name": "walk", "id": 4}, {"name": "sit", "id": 103}, {"name": "sit_low", "id": 102},
         {"name": "sit_high", "id": 104}, {"name": "sit_ground", "id": 97}]
CLASSES = {1: ["warrior", "paladin", "rogue", "priest", "mage", "warlock"], 2: ["warrior", "hunter", "rogue", "shaman", "warlock"],
           3: ["warrior", "paladin", "hunter", "rogue", "priest"], 4: ["warrior", "hunter", "rogue", "priest", "druid"],
           5: ["warrior", "rogue", "priest", "mage", "warlock"], 6: ["warrior", "hunter", "shaman", "druid"],
           7: ["warrior", "rogue", "mage", "warlock"], 8: ["warrior", "hunter", "rogue", "priest", "shaman", "mage"]}
LEVELS = [5, 10, 15, 20, 30, 40, 45, 50, 55, 60]   # spread over the pool so every difficulty band has workers


def log(*a):
    print(*a, flush=True)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)


class Finder:
    """File lookup by base name ('InnBarrel.m2') or full path, case-insensitive."""

    def __init__(self, dd):
        self.dd = dd
        self._by_base = None

    def path(self, name):
        if "\\" in name or "/" in name:
            return name.replace("/", "\\")
        if self._by_base is None:
            self._by_base = {}
            for n in self.dd.names():
                self._by_base.setdefault(n.split("\\")[-1].lower(), n)
        return self._by_base.get(name.lower())


# ---- interface art, fonts, sounds -------------------------------------------------------------------------------------------------------

def ui_assets(cfg):
    """Every game file the config references, plus app/config/ui_kit.json: [(game path, output path)]."""
    from ..app.check_app import _shape_all
    from ..app.config import CONFIG_DIR
    with open(os.path.join(CONFIG_DIR, "ui_kit.json"), encoding="utf-8") as f:
        wanted = set(json.load(f)["assets"])
    _probs, refs = _shape_all(cfg)
    wanted |= {v for _f, _p, kind, v in refs if kind == "asset"}
    wanted |= {s["path"] for s in cfg["sounds"].get("sounds", []) if isinstance(s, dict) and s.get("path")}
    out = set()
    for a in wanted:
        if a.startswith(("models/", "skin/")) or a.endswith(".json"):
            continue                       # made by the model / worker / inn / skin steps
        src = a[:-4] + ".blp" if a.endswith(".png") else a
        out.add((src, src[:-4] + ".png" if src.endswith(".blp") else src))
    return sorted(out)


def bake_ui(dd, out, cfg):
    res = {}
    for src, dest in ui_assets(cfg):
        data = dd.read(src)
        if data is None:
            res[src] = "missing"
            continue
        try:
            write(os.path.join(out, *dest.split("/")), blp.png_of(data) if src.endswith(".blp") else data)
            res[src] = "ok"
        except Exception as e:         # one bad file must not stop the rest
            res[src] = "error: %s" % e
    from . import settings
    write(os.path.join(out, "ui", "wow_settings.json"), json.dumps(settings.build(dd), indent=1, ensure_ascii=False).encode("utf-8"))
    write(os.path.join(out, "ui", "tips.json"), json.dumps(settings.tips(dd), indent=0, ensure_ascii=False).encode("utf-8"))
    bad = {k: v for k, v in res.items() if v != "ok"}
    log("ui: %d files baked%s" % (len(res) - len(bad), "" if not bad else ", problems: %s" % bad))
    return res


# ---- the window skin (frames cut from the owner's mockup sheet; committed in app/skin) ---------------------------------------------------

SKIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "app", "skin")


def bake_skin(out):
    n = 0
    for f in sorted(os.listdir(SKIN)):
        if f.endswith(".png"):
            with open(os.path.join(SKIN, f), "rb") as src:
                write(os.path.join(out, "skin", f), src.read())
            n += 1
    log("skin: %d frame images copied" % n)


# ---- room textures (office renderer) ------------------------------------------------------------------------------------------------------

ROOM = {"floor": "Dungeons\\Textures\\floor\\BM_SHDWFANG_WOODPLANKS_01.blp", "wall": "Dungeons\\Textures\\brick\\MM_DUSKWOOD_BRICK_01.blp"}


def bake_room(dd, out):
    for name, src in ROOM.items():
        data = dd.read(src)
        if data is None:
            log("room: %s is not in the data, %s texture skipped" % (src, name))
            continue
        write(os.path.join(out, "textures", name + ".png"), blp.png_of(data, 512))
    log("room: floor and wall textures baked")


# ---- static props and the default characters -----------------------------------------------------------------------------------------------

def _png_reader(dd, limit, overrides=None):
    def read(typ, path):
        if overrides and typ in overrides:
            return overrides[typ]
        return blp.png_of(dd.read(path), limit) if path and dd.has(path) else None
    return read


def bake_models(dd, out, tables=None, only=()):
    find = Finder(dd)
    with open(RECIPES, encoding="utf-8") as f:
        recipes = json.load(f)
    for rec in recipes:
        if only and rec["name"] not in only:
            continue
        path = character.model_path(rec["look"]["race"], rec["look"]["sex"]) if "look" in rec else find.path(rec["m2"])
        if not path or not dd.has(path):
            log("models: %s skipped, %s is not in the data" % (rec["name"], rec.get("m2")))
            continue
        tex = None
        spec = dict(rec, skinned=rec.get("skinned", False), drop_untextured="look" not in rec)
        if "look" in rec:                 # a character: paint its skin from the tables
            tables = tables or character.Tables(dd)
            look = dict(rec["look"], gear={})
            tex = character.textures(tables, look)
            spec.update(skinned=True, geosets=sorted(character.geosets(tables, look)), clips=CLIPS, anim_ids=ANIM_IDS)
        glb, rep = export.convert(dd.read(path), spec, _png_reader(dd, rec.get("tex_max", 256), tex))
        write(os.path.join(out, "models", rec["name"] + ".glb"), glb)
        log("models: %s %d KB, %d tris%s" % (rec["name"], len(glb) // 1024, rep["tris"], ", clips " + ",".join(rep["clips"]) if rep["clips"] else ""))
    return tables


# ---- the worker pool (random characters, stable per seed) ---------------------------------------------------------------------------------

def bake_workers(dd, out, count=30, seed=7, tables=None):
    tables = tables or character.Tables(dd)
    rng = random.Random(seed)
    wdir = os.path.join(out, "models", "workers")
    manifest, used = [], set()
    for n in range(count):
        race = list(character.RACES)[n % len(character.RACES)] if n < len(character.RACES) * 2 else rng.choice(list(character.RACES))
        sex = (n // len(character.RACES)) % 2 if n < len(character.RACES) * 2 else rng.randint(0, 1)
        r = random.Random(seed * 1000 + n)            # per worker: re-baking one worker gives the same look
        look = character.random_look(tables, r, race, sex)
        who = character.name_for(tables, r, race, sex, used)
        used.add(who)
        cls = r.choice(CLASSES[race])
        name = "w%02d_%s_%s_%s" % (n, character.RACES[race].lower(), "mf"[sex], cls)
        spec = {"name": name, "geosets": sorted(character.geosets(tables, look)), "clips": CLIPS, "anim_ids": ANIM_IDS}
        glb, rep = export.convert(dd.read(character.model_path(race, sex)), spec, _png_reader(dd, 256, character.textures(tables, look)))
        write(os.path.join(wdir, name + ".glb"), glb)
        manifest.append({"file": "models/workers/%s.glb" % name, "race": character.RACES[race].lower(), "sex": "mf"[sex], "class": cls,
                         "name": who, "seed": seed * 1000 + n, "level": LEVELS[n % len(LEVELS)], "look": look, "attach": [], "scale": 1.0,
                         "seq": rep["seq"]})
        log("workers: %s (%s) %d KB" % (name, who, len(glb) // 1024))
    write(os.path.join(wdir, "manifest.json"), json.dumps(manifest, indent=1).encode())
    keep = {os.path.basename(m["file"]) for m in manifest} | {"manifest.json"}
    for f in os.listdir(wdir):                        # drop workers from an older, bigger pool
        if f.endswith(".glb") and f not in keep:
            os.remove(os.path.join(wdir, f))
    weapons = os.path.join(out, "models", "weapons", "manifest.json")
    if not os.path.exists(weapons):
        write(weapons, b"[]")
    return tables


# ---- jukebox playlist (the game's own mp3 music) ------------------------------------------------------------------------------------------

def _title(path):
    parts = path.split("\\")
    name = os.path.splitext(parts[-1])[0]
    name = re.sub(r"[_-]+", " ", name).strip()
    zone = parts[-2] if len(parts) > 3 else ""
    return ("%s: %s" % (zone, name) if zone and zone.lower() not in name.lower() else name)[:70]


def bake_music(dd, out, cap_mb=260):
    # tavern tunes first so the cap never drops them (each building picks its music by group / title, see buildings.json "music")
    tracks = sorted((n for n in dd.names("sound\\music\\*.mp3")), key=lambda n: ("tavern" not in n.lower(), n.lower()))
    mdir = os.path.join(out, "sound", "music")
    manifest, total = [], 0
    for i, path in enumerate(tracks):
        data = dd.read(path)
        if not data or total + len(data) > cap_mb * 1048576:
            continue
        fn = "%03d.mp3" % i
        write(os.path.join(mdir, fn), data)
        total += len(data)
        manifest.append({"file": "sound/music/" + fn, "title": _title(path), "id": i, "group": path.split("\\")[-2]})
    write(os.path.join(mdir, "manifest.json"), json.dumps(manifest, indent=1).encode())
    log("music: %d tracks, %d MB" % (len(manifest), total // 1048576))


# ---- hero voices (the game's own character emote lines) ---------------------------------------------------------------------------------

VOICE_WORDS = {"hello": "Hello", "goodbye": "Goodbye", "yes": "Yes", "no": "No", "cheer": "Cheer", "congrats": "Congratulations", "thanks": "ThankYou",
               "laugh": "Laugh", "wait": "WaitHere", "follow": "FollowMe", "sigh": "Sigh", "yawn": "Yawn", "oom": "OutOfMana", "help": "Help"}


def bake_voices(dd, out):
    """sound/voices/<race>_<m|f>_<word>N.wav + manifest {"human_m": {"hello": [files...]}} for the talk window and emotes."""
    names = dd.names("sound\\character\\*.wav")
    manifest, n = {}, 0
    for race in character.RACES.values():
        for sex, s in (("Male", "m"), ("Female", "f")):
            key, prefix = "%s_%s" % (race.lower(), s), (race + sex).lower()
            for word, token in VOICE_WORDS.items():
                hits = sorted(x for x in names if x.split("\\")[-1].lower().startswith(prefix + token.lower()) and re.match(r"\d*\.wav$", x.split("\\")[-1][len(prefix + token):], re.I))
                for i, path in enumerate(hits[:4]):
                    fn = "%s_%s%d.wav" % (key, word, i + 1)
                    write(os.path.join(out, "sound", "voices", fn), dd.read(path))
                    manifest.setdefault(key, {}).setdefault(word, []).append("sound/voices/" + fn)
                    n += 1
    write(os.path.join(out, "sound", "voices", "manifest.json"), json.dumps(manifest, indent=1).encode())
    log("voices: %d lines for %d race/sex pairs" % (n, len(manifest)))


# ---- driver -------------------------------------------------------------------------------------------------------------------------------

STEPS = ("skin", "ui", "room", "models", "workers", "music", "voices", "inn", "buildings", "places")
# V-029: raise a step's number whenever its output changes shape; the window then says which steps to bake again.
# (models/workers 2: collision meshes + 78 animations + fixed clip lengths; inn/buildings 2: collision meshes; ui 2: whisper sound; music 2: tavern first; buildings 3: 11 more inns + zone/town/inn in the index (V-042))
BAKE_VERSIONS = {"skin": 1, "ui": 2, "room": 1, "models": 2, "workers": 2, "music": 2, "voices": 1, "inn": 2, "buildings": 3, "places": 2}
STAMP = "bake-versions.json"


def stale_steps(out):
    """Bake steps whose output in `out` is older than this code (or missing a stamp while the output exists)."""
    try:
        with open(os.path.join(out, STAMP), encoding="utf-8") as f:
            have = json.load(f)
    except (OSError, ValueError):
        have = {}
    marker = {"ui": "ui", "models": "models/prop_jukebox.glb", "workers": "models/workers/manifest.json", "music": "sound/music/manifest.json",
              "voices": "sound/voices/manifest.json", "inn": "models/inn.glb", "buildings": "models/buildings/index.json", "places": "models/buildings/index.json"}   # places: new in V-042, so a missing stamp counts as stale once buildings exist
    return [s for s, v in BAKE_VERSIONS.items() if s in marker and os.path.exists(os.path.join(out, marker[s])) and have.get(s, 0 if s == "places" else 1) < v]


def run(steps, data_dir, out, cfg, count=30, seed=7, only=()):
    if not data_dir or not os.path.isdir(data_dir):
        raise SystemExit("Set data_dir to your WoW 1.12 Data folder (the one with the .MPQ files) in vanilla.local.json, or pass --data-dir.")
    os.makedirs(out, exist_ok=True)
    tables = None
    with DataDir(data_dir) as dd:
        for s in steps:
            if s == "skin":
                bake_skin(out)
            elif s == "ui":
                bake_ui(dd, out, cfg)
            elif s == "room":
                bake_room(dd, out)
            elif s == "models":
                tables = bake_models(dd, out, tables)
            elif s == "workers":
                tables = bake_workers(dd, out, count, seed, tables)
            elif s == "music":
                bake_music(dd, out)
            elif s == "voices":
                bake_voices(dd, out)
            elif s == "inn":
                from . import wmo
                wmo.bake_inn(dd, out)
            elif s == "buildings":
                from . import wmo
                wmo.bake_buildings(dd, out, log, only)
            elif s == "places":
                from . import places
                places.bake_places(dd, out, log)
            stamp_path = os.path.join(out, STAMP)          # this step is now up to date
            try:
                with open(stamp_path, encoding="utf-8") as f:
                    stamp = json.load(f)
            except (OSError, ValueError):
                stamp = {}
            stamp[s] = BAKE_VERSIONS.get(s, 1)
            write(stamp_path, json.dumps(stamp, indent=1).encode())
    return 0


def clean(out):
    """Delete the whole assets folder (it can always be re-baked)."""
    if os.path.isdir(out):
        shutil.rmtree(out)
