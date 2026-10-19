"""check-app: proves the settings under app/config/ are complete and consistent before the program starts (DESIGN section 7.3).
Returns plain-language problems (an empty list means all good). Never raises on bad settings; only on a missing/unreadable file."""
import re
from pathlib import Path

from ..events.eli5 import check_templates
from ..events.errors import TemplateError
from ..events.schema import placeholders
from . import schema as S
from .config import CONFIG_DIR, iter_files, load_all

ASSET_EXT = (".png", ".ttf", ".ogg", ".wav", ".mp3", ".glb", ".json")
_PH = re.compile(r"\{([^{}]*)\}")


def _asset_problem(rel, assets):
    if rel.endswith(".blp"):
        return "uses the raw game file %s; use the baked .png (run scripts/bake_ui_kit.py)" % rel
    if not rel.lower().endswith(ASSET_EXT) or rel.startswith(("/", "\\")) or ".." in rel.split("/") or "\\" in rel or ":" in rel:
        return "has a bad asset path %r (use forward slashes, no .., ending in .png/.ttf/.wav/.mp3/.glb)" % rel
    if not (Path(assets) / rel).is_file():
        return "needs the asset %s but it is not in %s. Bake it with: python -m vanilla_agents.assets bake" % (rel, assets)
    return None


def _shape_all(cfg):
    """Shape problems per file plus every reference found: [(file, message)], [(file, path, kind, value)]."""
    probs, refs = [], []
    specs = {"app": S.APP, "theme": S.THEME, "hotkeys": S.HOTKEYS, "metaphors": S.METAPHORS, "workshop": S.WORKSHOP, "sounds": S.SOUNDS, "map": S.MAP, "glossary": S.GLOSSARY, "guide": S.GUIDE}
    for fname, data in iter_files(cfg):
        stem = fname[:-5]
        spec = specs.get(stem)
        if fname.startswith("panels/"):
            spec = S.PANEL
        elif fname == "strings/ui.json":
            spec = {"*": "str"}
        elif fname == "strings/tooltips.json":
            spec = {"*": S.TOOLTIP}
        elif fname == "strings/zones.json":
            spec = {"zones": [{"id": "str", "eli5": "str"}]}
        if spec is None:   # eli5.json has its own checker
            continue
        e, r = [], []
        S.shape(data, spec, "", e, r)
        probs += [(fname, m) for m in e]
        refs += [(fname, p, k, v) for p, k, v in r]
    return probs, refs


def _names(probs, fname, found, wanted, what, exact=True):
    for n in sorted(set(wanted) - set(found)):
        probs.append((fname, "the %s '%s' is missing." % (what, n)))
    if exact:
        for n in sorted(set(found) - set(wanted)):
            probs.append((fname, "the %s '%s' is not something the program knows about." % (what, n)))


def check_app(cfg=None, assets_dir=None, config_dir=CONFIG_DIR):
    cfg = cfg if cfg is not None else load_all(config_dir)
    probs, refs = _shape_all(cfg)
    ui, tips, theme = cfg["strings"]["ui"], cfg["strings"]["tooltips"], cfg["theme"]
    assets = assets_dir or cfg["app"].get("assets_dir", "")
    if cfg["app"].get("host") != "127.0.0.1":
        probs.append(("app.json", "host must be 127.0.0.1 so only this computer can reach the program."))
    for f, p, kind, v in refs:
        if kind == "asset":
            m = _asset_problem(v, assets)
            if m:
                probs.append((f, "'%s' %s." % (p, m)))
        elif kind == "uikey" and v not in ui:
            probs.append((f, "'%s' points to the sentence '%s' which is not in strings/ui.json." % (p, v)))
        elif kind == "tipkey" and v not in tips:
            probs.append((f, "'%s' points to the tooltip '%s' which is not in strings/tooltips.json." % (p, v)))
        elif kind == "frame" and v not in theme.get("frames", {}):
            probs.append((f, "'%s' uses the frame '%s' which is not defined in theme.json frames." % (p, v)))
        elif kind == "hotkey" and v not in cfg["hotkeys"]:
            probs.append((f, "'%s' uses the hotkey name '%s' which is not in hotkeys.json." % (p, v)))
    colors = theme.get("colors", {})
    _names(probs, "theme.json", colors, S.COLOR_NAMES, "colour", exact=False)
    _names(probs, "theme.json", theme.get("frames", {}), S.FRAME_NAMES, "frame", exact=False)
    _names(probs, "theme.json", theme.get("fonts", {}), ["body"], "font", exact=False)
    _names(probs, "hotkeys.json", cfg["hotkeys"], S.HOTKEY_NAMES, "hotkey")
    keys = [v for v in cfg["hotkeys"].values() if isinstance(v, str)]
    if len(set(keys)) != len(keys):
        probs.append(("hotkeys.json", "two panels use the same key."))
    for n, v in cfg["hotkeys"].items():
        if isinstance(v, str) and not (len(v) == 1 and v == v.lower() or v in ("enter", "escape") or v in ("f%d" % i for i in range(1, 13))):
            probs.append(("hotkeys.json", "'%s' must be one lower-case letter, f1..f12, or enter/escape (got %r)." % (n, v)))
    _names(probs, "panels", cfg["panels"], S.PANEL_NAMES, "panel file")
    for n, p in cfg["panels"].items():
        allowed = S.PANEL_KEYS.get(n)
        if not allowed or not isinstance(p, dict):
            continue
        for grp, names in allowed.items():
            _names(probs, "panels/%s.json" % n, p.get(grp, {}), names, "setting in '%s'" % grp) if isinstance(p.get(grp, {}), dict) else None
        if "slots" in p and n != "action_bar":
            probs.append(("panels/%s.json" % n, "only the action bar has 'slots'."))
        if n == "action_bar" and isinstance(p.get("slots"), list):
            slots = [x for x in p["slots"] if isinstance(x, dict)]
            for x in slots:
                if x.get("id") not in S.ACTION_SLOTS:
                    probs.append(("panels/action_bar.json", "the button '%s' is not one the action bar knows (%s)." % (x.get("id"), ", ".join(S.ACTION_SLOTS))))
                k = x.get("key")
                if k is not None and not (isinstance(k, str) and len(k) == 1 and k.isdigit()):
                    probs.append(("panels/action_bar.json", "the button '%s' has the key %r: use one digit, 0 to 9." % (x.get("id"), k)))
            ks = [x.get("key") for x in slots if x.get("key") is not None]
            if len(set(ks)) != len(ks):
                probs.append(("panels/action_bar.json", "two action bar buttons use the same number key."))
            ids = [x.get("id") for x in slots]
            if len(set(ids)) != len(ids):
                probs.append(("panels/action_bar.json", "an action bar button is there twice."))
        if "spells" in p and n != "spellbook":
            probs.append(("panels/%s.json" % n, "only the spellbook has 'spells'."))
        if n in ("spellbook", "backpack"):
            _book(n, p, probs)
        if n == "raid_frames":
            for k, v in p.get("options", {}).items():
                if isinstance(v, int) and v < 1:
                    probs.append(("panels/raid_frames.json", "'options.%s' must be at least 1." % k))
    _metaphors(cfg, probs, colors)
    for k, s in ui.items():
        if isinstance(s, str):
            bad = set(_PH.findall(s)) - S.UI_VARS
            if bad or re.sub(_PH, "", s).count("{") or re.sub(_PH, "", s).count("}"):
                probs.append(("strings/ui.json", "'%s' uses unknown placeholder(s) %s; allowed: %s." % (k, sorted(bad) or "(stray brace)", sorted(S.UI_VARS))))
    for k, t in tips.items():
        if isinstance(t, dict) and any("{" in str(x) or "}" in str(x) for x in t.values()):
            probs.append(("strings/tooltips.json", "'%s' contains a brace; tooltips are plain text." % k))
    try:
        check_templates(cfg["strings"]["eli5"])
    except TemplateError as e:
        probs.append(("strings/eli5.json", "%s. Allowed words for each event type are listed in vanilla_agents/events/schema.py." % e))
    if isinstance(cfg["workshop"].get("desks"), list) and not cfg["workshop"]["desks"]:
        probs.append(("workshop.json", "'desks' needs at least one desk."))
    if "default" not in cfg["workshop"].get("roles", {"default": 1}):
        probs.append(("workshop.json", "'roles' needs a 'default' entry for agents with an unknown role."))
    ws = cfg["workshop"]
    if ws.get("renderer") not in (None, "2d", "3d", "inn"):
        probs.append(("workshop.json", "'renderer' must be 2d, 3d or inn."))
    _inn(ws.get("inn"), probs)
    _sounds(cfg["sounds"], probs)
    for term, plain in cfg["glossary"]["terms"].items():       # D-363: every jargon word needs plain words that are not just itself
        if not plain.strip() or plain.strip().lower() == term.lower():
            probs.append(("glossary.json", "the word '%s' needs plain words that say something different." % term))
    _route(cfg, probs)
    _tiers(cfg, probs)
    _map(cfg, probs, colors)
    _guide(cfg, probs)
    return ["%s: %s" % x for x in probs]


_NOT_JS = re.compile(r"\(\?P|\(\?#|\(\?[aiLmsux]+[):]|\\[AZ]")


def pattern_problem(p):
    """V-043: why a command/name pattern cannot be used by both Python and the browser's RegExp, or None."""
    if not isinstance(p, str) or not p.strip():
        return "is empty; it would match everything"
    if _NOT_JS.search(p):
        return "uses Python-only syntax ((?P, (?#, inline flags like (?i), \\A or \\Z) that the browser does not understand"
    try:
        r = re.compile(p, re.I)
    except re.error as e:
        return "is not a valid pattern (%s)" % e
    if r.search(""):
        return "matches empty text, so it would match every command"
    return None


def _inn(inn, probs):
    """V-043: the inn's task patterns (tests/builds, shipping) and doodad names must be valid in Python and JS; light budgets per quality 0..3."""
    if not isinstance(inn, dict):
        return
    f, t, li = "workshop.json", inn.get("tasks") or {}, inn.get("lights") or {}
    for grp in ("test", "ship"):
        pats = t.get(grp)
        if isinstance(pats, list) and not pats:
            probs.append((f, "'inn.tasks.%s' needs at least one pattern." % grp))
        for i, p in enumerate(pats if isinstance(pats, list) else []):
            m = pattern_problem(p)
            if m:
                probs.append((f, "'inn.tasks.%s[%d]' %s." % (grp, i, m)))
    for path, p in (("inn.tasks.anvil", t.get("anvil")), ("inn.tasks.drop", t.get("drop")), ("inn.lights.names", li.get("names"))):
        m = pattern_problem(p) if p is not None else None
        if m:
            probs.append((f, "'%s' %s." % (path, m)))
    for k in ("hold_s", "back_s", "ask_every_s"):
        if isinstance(t.get(k), (int, float)) and not isinstance(t.get(k), bool) and t[k] <= 0:
            probs.append((f, "'inn.tasks.%s' must be more than 0." % k))
    mx = li.get("max")
    if isinstance(mx, list) and (len(mx) != 4 or any(not isinstance(n, int) or isinstance(n, bool) or n < 0 or n > 16 for n in mx)):
        probs.append((f, "'inn.lights.max' must list 4 whole numbers from 0 to 16 (lights at quality low, fair, good, high)."))
    for k in ("intensity", "distance", "pick_s", "merge"):
        if isinstance(li.get(k), (int, float)) and not isinstance(li.get(k), bool) and li[k] <= 0:
            probs.append((f, "'inn.lights.%s' must be more than 0." % k))
    if isinstance(li.get("flicker"), (int, float)) and not 0 <= li["flicker"] <= 1:
        probs.append((f, "'inn.lights.flicker' must be between 0 and 1."))


def _route(cfg, probs):
    """Small/big work rules (D-308): limits must be positive, the trusted list must not be empty (an empty list would let fast skip holdouts), reasons need wording."""
    r, f = cfg["app"].get("route"), "app.json"
    if not isinstance(r, dict):
        return
    for k in ("max_fast_files", "max_fast_lines"):
        if isinstance(r.get(k), int) and not isinstance(r.get(k), bool) and r[k] < 1:
            probs.append((f, "'route.%s' must be at least 1." % k))
    if isinstance(r.get("trusted_paths"), list) and not r["trusted_paths"]:
        probs.append((f, "'route.trusted_paths' cannot be empty: work on the checker's own code must always take the full route."))
    a = cfg["app"]
    for k in ("approvals_max_tasks", "approvals_max_projects"):
        if isinstance(a.get(k), int) and not isinstance(a.get(k), bool) and a[k] < 1:
            probs.append((f, "'%s' must be at least 1." % k))
    ui = cfg["strings"]["ui"]
    for k in ("fast", "full") + tuple("why_" + w for w in ("small", "unknown", "trusted", "paths", "files", "lines", "intent")):
        if "route." + k not in ui:
            probs.append(("strings/ui.json", "the sentence 'route.%s' is missing." % k))
    for k in ("sentback.hidden", "sentback.change"):
        if k not in ui:
            probs.append(("strings/ui.json", "the sentence '%s' is missing." % k))
    for k in ("route_fast", "route_full"):
        if k not in cfg["strings"]["tooltips"]:
            probs.append(("strings/tooltips.json", "the tooltip '%s' is missing." % k))


def _tiers(cfg, probs):
    """Approval tiers (D-370): every never-category and every reason needs wording; an empty keyword would match every quest."""
    t, f, ui = cfg["app"].get("tiers"), "app.json", cfg["strings"]["ui"]
    if not isinstance(t, dict):
        return
    for k in ("ask_sizes", "risky_words"):
        if any(isinstance(w, str) and not w.strip() for w in t.get(k, [])):
            probs.append((f, "'tiers.%s' has an empty word; it would match every quest." % k))
    for cat, words in (t.get("never") or {}).items():
        if any(isinstance(w, str) and not w.strip() for w in words):
            probs.append((f, "'tiers.never.%s' has an empty word; it would match every quest." % cat))
        if "tier.never_" + cat not in ui:
            probs.append(("strings/ui.json", "the sentence 'tier.never_%s' is missing (needed by tiers.never.%s)." % (cat, cat)))
    for k in ("auto", "ask_big", "ask_high", "ask_word", "ask_scope", "ask_trusted", "ask_failed", "ask_unknown", "ask_vetoed"):
        if "tier." + k not in ui:
            probs.append(("strings/ui.json", "the sentence 'tier.%s' is missing." % k))


def _metaphors(cfg, probs, colors):
    m, f = cfg["metaphors"], "metaphors.json"
    if not isinstance(m.get("events"), dict) or not isinstance(m.get("task_states"), dict):
        return
    _names(probs, f, m["events"], S.EVENT_TYPES, "event type")
    _names(probs, f, m["task_states"], S.STATUSES, "quest state")
    _names(probs, f, m.get("agent_states", {}), S.AGENT_STATES, "agent state")
    toasts = m.get("toasts", {})
    for et, e in m["events"].items():
        if not isinstance(e, dict):
            continue
        for key, ok, what in (("channel", S.CHANNELS, "chat channel"), ("task_status", S.STATUSES, "quest state"), ("agent_state", S.AGENT_STATES, "agent state")):
            if key in e and e[key] not in ok:
                probs.append((f, "event '%s' uses the %s '%s'; allowed: %s." % (et, what, e[key], ", ".join(ok))))
        if "toast" in e and e["toast"] not in toasts:
            probs.append((f, "event '%s' uses the toast '%s' which is not defined under 'toasts'." % (et, e["toast"])))
    for where in (m["task_states"], toasts):
        for n, e in where.items():
            if isinstance(e, dict) and e.get("color") not in colors:
                probs.append((f, "'%s' uses the colour '%s' which is not in theme.json colors." % (n, e.get("color"))))
    for n, e in m["task_states"].items():
        if isinstance(e, dict) and e.get("marker") and e["marker"] not in cfg["theme"].get("markers", {}):
            probs.append((f, "quest state '%s' uses the marker '%s' which is not in theme.json markers." % (n, e["marker"])))
    _names(probs, f, m.get("item_quality", {}), S.ITEM_STATES, "item quality")
    _names(probs, f, m.get("spell_marks", {}), S.SPELL_MARKS, "spell result mark")
    _names(probs, f, m.get("auras", {}), S.AURAS, "buff/debuff icon")
    for n, e in m.get("auras", {}).items():
        if isinstance(e, dict) and e.get("kind") not in S.AURA_KINDS:
            probs.append((f, "aura '%s' has kind '%s'; allowed: %s." % (n, e.get("kind"), ", ".join(S.AURA_KINDS))))
    for where in (m.get("item_quality", {}), m.get("spell_marks", {})):
        for n, e in where.items():
            for k in ("border", "color"):
                if isinstance(e, dict) and k in e and e[k] not in colors:
                    probs.append((f, "'%s' uses the colour '%s' which is not in theme.json colors." % (n, e[k])))
    for n, e in m.get("item_quality", {}).items():
        if isinstance(e, dict) and isinstance(e.get("outcomes"), list):
            for o in e["outcomes"]:
                if o not in S.OUTCOMES:
                    probs.append((f, "item quality '%s' lists the outcome '%s' which the checker never returns; allowed: %s." % (n, o, ", ".join(S.OUTCOMES))))
    for ch in S.CHANNELS:
        if "chat_" + ch not in colors:
            probs.append(("theme.json", "the chat channel '%s' needs the colour 'chat_%s'." % (ch, ch)))


def _book(n, p, probs):
    f, o = "panels/%s.json" % n, p.get("options", {})
    for k in ("columns", "slots") + (("max_runs", "max_files") if n == "backpack" else ()):
        if isinstance(o.get(k), int) and not isinstance(o.get(k), bool) and o[k] < 1:
            probs.append((f, "'options.%s' must be at least 1." % k))
    if isinstance(o.get("columns"), int) and isinstance(o.get("slots"), int) and o["slots"] % max(o["columns"], 1):
        probs.append((f, "'options.slots' should be a whole number of rows (a multiple of 'options.columns')."))
    if n == "spellbook":
        ids = [s.get("id") for s in p.get("spells", []) if isinstance(s, dict)]
        for i in sorted({x for x in ids if ids.count(x) > 1}):
            probs.append((f, "the spell '%s' is listed twice." % i))


def event_words(event_type):
    """Words a plain-language template for this event type may use (for error messages and docs)."""
    return sorted(placeholders(event_type))


def _sounds(sd, probs):
    f = "sounds.json"
    if not (isinstance(sd.get("sounds"), list) and isinstance(sd.get("events"), dict) and isinstance(sd.get("master"), dict) and isinstance(sd.get("triggers"), dict)):
        return
    ids = [s.get("id") for s in sd["sounds"] if isinstance(s, dict)]
    for i in sorted({x for x in ids if ids.count(x) > 1}):
        probs.append((f, "the sound id '%s' is used twice." % i))
    _names(probs, f, sd["events"], S.SOUND_EVENTS, "sound event")
    _names(probs, f, set(sd["triggers"].values()), S.SOUND_EVENTS, "sound event in triggers")
    for et in sd["triggers"]:
        if et not in S.EVENT_TYPES:
            probs.append((f, "triggers names the event type '%s' which is not something the program knows about." % et))
    vols = [("master.volume", sd["master"].get("volume"))] + [("events.%s.volume" % n, e.get("volume")) for n, e in sd["events"].items() if isinstance(e, dict)]
    for n, v in vols:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not 0 <= v <= 1:
            probs.append((f, "'%s' must be between 0 and 1 (got %s)." % (n, v)))
    for n, e in sd["events"].items():
        if isinstance(e, dict) and isinstance(e.get("sound"), str) and e["sound"] not in ids:
            probs.append((f, "event '%s' uses the sound '%s' which is not in the sounds list." % (n, e["sound"])))
    if isinstance(sd.get("usage_low_pct"), int) and not 0 <= sd["usage_low_pct"] <= 100:
        probs.append((f, "'usage_low_pct' must be between 0 and 100."))


def _map(cfg, probs, colors):
    """World map settings (D-262): state names/colours, grid and caps must make sense."""
    m, f = cfg["map"], "map.json"
    if not isinstance(m.get("states"), dict) or not isinstance(m.get("grid"), dict):
        return
    _names(probs, f, m["states"], S.MAP_STATES, "map state")
    for n, e in m["states"].items():
        if isinstance(e, dict) and e.get("color") not in colors:
            probs.append((f, "map state '%s' uses the colour '%s' which is not in theme.json colors." % (n, e.get("color"))))
    for k in ("cols", "rows"):
        if isinstance(m["grid"].get(k), int) and m["grid"][k] < 1:
            probs.append((f, "'grid.%s' must be at least 1." % k))
    c, r = m["grid"].get("cols"), m["grid"].get("rows")
    if all(isinstance(x, int) and not isinstance(x, bool) for x in (c, r, m.get("zone_max"))) and not 2 <= m["zone_max"] <= c * r:
        probs.append((f, "'zone_max' must be between 2 and cols x rows (%d) so every zone has a cell." % (c * r)))
    for k in ("git_max_bytes", "activity_max_zones", "activity_max_projects"):
        if isinstance(m.get(k), int) and not isinstance(m.get(k), bool) and m[k] < 1:
            probs.append((f, "'%s' must be at least 1." % k))
    for k in ("git_timeout_seconds", "activity_keep_days", "refresh_seconds"):
        if isinstance(m.get(k), (int, float)) and not isinstance(m.get(k), bool) and m[k] <= 0:
            probs.append((f, "'%s' must be more than 0." % k))
    ids = [z.get("id") for z in cfg["strings"]["zones"].get("zones", []) if isinstance(z, dict)] if isinstance(cfg["strings"]["zones"], dict) else []
    for i in sorted({x for x in ids if ids.count(x) > 1}):
        probs.append(("strings/zones.json", "the zone '%s' is described twice." % i))


def _guide(cfg, probs):
    """Feature guide (V-037): unique ids, real windows, every window has a topic, every hotkey is in the Keyboard reference.
    Settings named by topics are checked against the Options window code by tests/test_guide.py (JS, not config)."""
    g, f = cfg.get("guide"), "guide.json"
    if not isinstance(g, dict) or not isinstance(g.get("chapters"), list):
        return
    chapters = [c for c in g["chapters"] if isinstance(c, dict)]
    topics = [t for c in chapters for t in c.get("topics", []) if isinstance(t, dict)]
    for what, ids in (("chapter", [c.get("id") for c in chapters]), ("topic", [t.get("id") for t in topics])):
        for i in sorted({x for x in ids if ids.count(x) > 1}, key=str):
            probs.append((f, "the %s id '%s' is used twice." % (what, i)))
    for t in topics:
        if t.get("panel") is not None and t["panel"] not in cfg["panels"]:
            probs.append((f, "topic '%s' opens the window '%s' which is not in panels/." % (t.get("id"), t["panel"])))
        if isinstance(t.get("body"), list) and not t["body"]:
            probs.append((f, "topic '%s' needs at least one paragraph." % t.get("id")))
    covered = {t.get("panel") for t in topics}
    for n in sorted(set(cfg["panels"]) - covered):
        probs.append((f, "the window '%s' has no topic in the guide (add one with \"panel\": \"%s\")." % (n, n)))
    ref = [c for c in chapters if c.get("id") == S.GUIDE_KEYS_CHAPTER]
    listed = {k for c in ref for t in c.get("topics", []) if isinstance(t, dict) for k in t.get("keys", [])}
    if not ref:
        probs.append((f, "the Keyboard reference chapter (id '%s') is missing." % S.GUIDE_KEYS_CHAPTER))
    for k in sorted(set(cfg["hotkeys"]) - listed) if ref else ():
        probs.append((f, "the hotkey '%s' is not listed in the Keyboard reference chapter." % k))
