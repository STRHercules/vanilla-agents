"""V-051 server calls of the Addons window. Every POST needs the window key (server.py); none of them is an MCP tool or a phone call.
GET  /api/addons                    the installed addons + `load` (what the window starts: enabled and valid only)
GET  /addons/<id>/<file>, /addons/<name>.js   an ENABLED addon's files
POST /api/addons/enable             {key, enabled}
POST /api/addons/remove             {key}                   moves it to <assets>/addons/.removed/<id>-<time>/
POST /api/addons/install            {zip: base64, replace?} a .zip from your disk (route-specific body limit)
POST /api/addons/package            {key}                   <assets>/addons/.packages/<id>-<version>.zip + <id>.yml
POST /api/addons/browse             {}                      the Workshop's Vanilla Agents addons (the only network use)
POST /api/addons/workshop_install   {id, replace?}          download (https, size limit), sha256 check, install"""
import base64
import binascii

from . import addon_workshop, addon_zip, addons

POST_CALLS = ("/api/addons/enable", "/api/addons/remove", "/api/addons/install", "/api/addons/package", "/api/addons/browse", "/api/addons/workshop_install")
FILE_EXT = (".js", ".css", ".png", ".jpg", ".json", ".ogg", ".wav", ".mp3", ".woff2", ".ttf")


def opts(cfg):
    o = cfg["app"].get("addons") or {}
    return {"index_url": o.get("workshop_index_url") or addon_workshop.INDEX_URL, "timeout": o.get("timeout_seconds", 15), "limits": addon_zip.limits(cfg["app"])}


def body_limit(cfg):
    return opts(cfg)["limits"]["max_zip_bytes"] * 4 // 3 + 1024


def get(ctx):
    return {**addons.payload(ctx.assets_dir, ctx.settings_file), "max_zip_bytes": opts(ctx.cfg)["limits"]["max_zip_bytes"]}


def _opener(ctx):
    return getattr(ctx, "addons_opener", None) or addon_workshop.default_opener()


def post(ctx, call, body):
    """(status, reply). 409 = that id is installed already (the window then offers Replace)."""
    o, a, s = opts(ctx.cfg), ctx.assets_dir, ctx.settings_file
    replace = body.get("replace", False)
    if not isinstance(replace, bool):
        raise ValueError("replace must be true or false")
    try:
        if call == "/api/addons/enable":
            addons.set_enabled(a, s, body.get("key"), body.get("enabled"))
            return 200, get(ctx)
        if call == "/api/addons/remove":
            moved = addons.remove(a, s, body.get("key"))
            return 200, {**get(ctx), "moved_to": moved}
        if call == "/api/addons/install":
            z = body.get("zip")
            if not isinstance(z, str) or len(z) > body_limit(ctx.cfg):
                raise ValueError("zip must be the file as base64 (at most %d KB)" % (o["limits"]["max_zip_bytes"] // 1024))
            try:
                raw = base64.b64decode(z.partition(",")[2] if z.startswith("data:") else z, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("zip is not base64")
            r = addon_zip.install(a, s, raw, replace, o["limits"])
            return 200, {**get(ctx), "result": r}
        if call == "/api/addons/package":
            r = addon_workshop.package(a, s, body.get("key"))
            return 200, {**get(ctx), "package": r}
        if call == "/api/addons/browse":
            ids = {x["id"] for x in addons.scan(a, s) if not x["legacy"]}
            return 200, addon_workshop.browse(o["index_url"], _opener(ctx), ids, o["timeout"])
        if call == "/api/addons/workshop_install":
            r = addon_workshop.install_from(o["index_url"], _opener(ctx), a, s, body.get("id"), replace, o["limits"], o["timeout"])
            return 200, {**get(ctx), "result": r}
    except addon_zip.Exists as e:
        return 409, {"error": str(e), "exists": True}
    raise ValueError("no such call")
