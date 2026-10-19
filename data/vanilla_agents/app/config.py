"""Loads every file under app/config/ into one dict. Pure file reading: no checking (check_app does that)."""
import json
import os
from pathlib import Path

from ..contracts import VanillaError

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "app" / "config"
WEB_DIR = ROOT / "app" / "web"
TOP_FILES = ("app", "theme", "hotkeys", "metaphors", "workshop", "sounds", "map", "glossary", "guide")
STRING_FILES = ("ui", "tooltips", "eli5", "zones")


class ConfigError(VanillaError):
    code = "app_config"


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        raise ConfigError("The settings file %s is missing or cannot be opened." % path, code="config_missing")
    except ValueError as e:
        raise ConfigError("The settings file %s is not valid JSON (%s). Check commas and quotes." % (path, e), code="config_bad_json")


LOCAL_FILE = ROOT / "vanilla.local.json"   # per-PC overrides of app.json (data_dir, assets_dir, port...); gitignored


def expand(v):
    """'~/x' -> the user's home folder; lists and dicts are expanded item by item. Other values are returned as is."""
    if isinstance(v, str):
        return os.path.expanduser(v) if v.startswith("~") else v
    if isinstance(v, list):
        return [expand(x) for x in v]
    if isinstance(v, dict):
        return {k: expand(x) for k, x in v.items()}
    return v


def local_overrides(env=os.environ):
    """app.json keys from VANILLA_LOCAL or vanilla.local.json next to the repo; {} when there is none."""
    p = Path(env.get("VANILLA_LOCAL") or LOCAL_FILE)
    return _read(p) if p.is_file() else {}


def load_all(config_dir=CONFIG_DIR, local=True):
    """{'app':..., 'theme':..., 'panels': {name: ...}, 'strings': {'ui':..., 'tooltips':..., 'eli5':...}, ...}
    app.json is merged with the local override file (only for the shipped config folder) and '~' paths are expanded."""
    d = Path(config_dir)
    cfg = {n: _read(d / (n + ".json")) for n in TOP_FILES}
    if local and Path(config_dir) == CONFIG_DIR:
        cfg["app"].update(local_overrides())
    cfg["app"] = expand(cfg["app"])
    cfg["map"] = expand(cfg["map"])
    cfg["panels"] = {p.stem: _read(p) for p in sorted((d / "panels").glob("*.json"))}
    cfg["strings"] = {n: _read(d / "strings" / (n + ".json")) for n in STRING_FILES}
    return cfg


def iter_files(cfg):
    """(config-relative file name, data) for every config file, e.g. ('panels/chat.json', {...})."""
    for n in TOP_FILES:
        yield n + ".json", cfg[n]
    for n, v in cfg["panels"].items():
        yield "panels/%s.json" % n, v
    for n, v in cfg["strings"].items():
        yield "strings/%s.json" % n, v
