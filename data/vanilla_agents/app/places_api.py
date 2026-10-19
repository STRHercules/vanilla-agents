"""Zones window (V-042): GET /api/places joins the baked zones (places/index.json, or the zone list itself before the places step
is baked), the baked buildings (models/buildings/index.json, each with its zone) and your own office GLBs, plus a picture of each inn
if one was taken. POST /api/inn_photo saves that picture: the window photographs an inn the first time you open it (a JPEG of the
3D view, at most PHOTO_MAX bytes) into <assets>/places/photos/<key>.jpg. Nothing leaves your PC."""
import base64
import json
import re
from pathlib import Path

from . import social

PHOTO_MAX = 300_000
KEY = re.compile(r"^(custom:)?[A-Za-z0-9_\-]{1,60}\Z")      # \Z: no trailing newline in a file name (V-048)
ZONE_LIST = Path(__file__).resolve().parent.parent / "assets" / "places.json"


def _json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def photo_path(assets_dir, key):
    return Path(assets_dir) / "places" / "photos" / (key.replace(":", "_") + ".jpg")


def places(assets_dir):
    a = Path(assets_dir)
    baked = _json(a / "places" / "index.json", None)
    if not isinstance(baked, dict):
        src = _json(ZONE_LIST, {"zones": []})
        baked = {"zones": [{k: z[k] for k in ("id", "name", "continent", "light", "faction") if k in z} for z in src.get("zones", [])], "loading": {}}
    buildings = _json(a / "models" / "buildings" / "index.json", [])
    buildings = [dict(b) for b in buildings if isinstance(b, dict) and b.get("key")] + social.office_buildings(a)
    for b in buildings:
        p = photo_path(a, b["key"])
        if p.is_file():
            b["photo"] = "places/photos/%s?v=%d" % (p.name, int(p.stat().st_mtime))
        if b.get("custom"):
            b.setdefault("zone", "own")
    return {"zones": baked.get("zones", []), "loading": baked.get("loading", {}), "buildings": buildings}


def save_photo(assets_dir, key, data):
    """Store one inn picture; raises ValueError for a bad key or anything that is not a small JPEG."""
    if not isinstance(key, str) or not KEY.match(key):
        raise ValueError("key must be a building key")
    if not isinstance(data, str) or len(data) > PHOTO_MAX * 4 // 3 + 64:
        raise ValueError("picture missing or too big")
    if data.startswith("data:"):
        head, _, data = data.partition(",")
        if head != "data:image/jpeg;base64":
            raise ValueError("picture must be a JPEG")
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise ValueError("picture is not base64")
    if not raw.startswith(b"\xff\xd8\xff") or len(raw) > PHOTO_MAX:
        raise ValueError("picture must be a JPEG of at most %d KB" % (PHOTO_MAX // 1000))
    p = photo_path(assets_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    tmp.write_bytes(raw)
    tmp.replace(p)
    return "places/photos/" + p.name
