"""The game's own options windows (Interface / Video / Sound, from Interface/FrameXML/*OptionsFrame.lua) as
assets/ui/wow_settings.json for the Options panel: real labels and tooltips from GlobalStrings.lua, real CVar names and ranges."""
import re

FRAMES = [("UIOptionsFrame.lua", "UIOPTIONS_MENU", "Gameplay"), ("OptionsFrame.lua", "VIDEOOPTIONS_MENU", "System"),
          ("SoundOptionsFrame.lua", "SOUNDOPTIONS_MENU", "System")]
UI_FALLBACK = {"SETTINGS_CLOSE": "CLOSE", "SETTINGS_DEFAULTS": "DEFAULTS", "GRAPHICS_LABEL": "VIDEOOPTIONS_MENU", "SEARCH": "SEARCH"}
UI_PLAIN = {"SETTINGS_SEARCH_NOTHING_FOUND": "Nothing found", "SETTINGS_SEARCH_RESULTS": "Search results", "SETTING_GROUP_GAMEPLAY": "Gameplay",
            "SETTING_GROUP_SYSTEM": "System", "SETTING_GROUP_ACCESSIBILITY": "Accessibility", "GRAPHICS_QUALITY": "Graphics Quality"}


def global_strings(text):
    out = {}
    for m in re.finditer(r'^(\w+)\s*=\s*"((?:[^"\\]|\\.)*)";', text, re.M):
        out[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\n", "\n")
    return out


def _fields(body):
    f = {}
    for m in re.finditer(r'(\w+)\s*=\s*("[^"]*"|[\w.\-]+)', body):
        v = m.group(2)
        f[m.group(1)] = v[1:-1] if v.startswith('"') else v
    return f


def _num(v, consts):
    try:
        return float(consts.get(v, v))
    except (TypeError, ValueError):
        return None


def category(text, title, group, gs):
    consts = {m.group(1): m.group(2) for m in re.finditer(r"^(\w+)\s*=\s*([\d.]+);", text, re.M)}
    items = []
    head = text.split("\nfunction ")[0]                  # the tables come before the first function
    for line in head.splitlines():                       # check buttons in file order, comment lines become headers
        h = re.match(r"^--\s*([A-Z][\w ]+?)\s*$", line)
        if h and "---" not in line:
            items.append({"kind": "header", "label": h.group(1)})
            continue
        m = re.match(r'^\w+CheckButtons\["(\w+)"\]\s*=\s*\{(.*)\}', line)
        if not m:
            continue
        key, f = m.group(1), _fields(m.group(2))
        tip = gs.get(f.get("tooltipText", ""), "") or gs.get("OPTION_TOOLTIP_" + key, "")
        items.append({"kind": "checkbox", "label": gs.get(key, key.replace("_", " ").title()), "tip": tip,
                      "var": f.get("cvar") or f.get("uvar") or key, "default": f.get("default") == "1"})
    for m in re.finditer(r"\{([^{}]*minValue[^{}]*)\}", text):    # sliders
        f = _fields(m.group(1))
        label_key = f.get("text", "")
        items.append({"kind": "slider", "label": gs.get(label_key, label_key.replace("_", " ").title()),
                      "tip": gs.get(f.get("tooltipText", ""), ""), "var": f.get("cvar") or f.get("func") or label_key,
                      "min": _num(f.get("minValue"), consts), "max": _num(f.get("maxValue"), consts),
                      "step": _num(f.get("valueStep"), consts) or 1})
    while items and items[-1]["kind"] == "header":
        items.pop()
    items = [it for i, it in enumerate(items) if not (it["kind"] == "header" and (i + 1 >= len(items) or items[i + 1]["kind"] == "header"))]
    return {"name": gs.get(title, title), "key": title, "group": group, "items": items}


def build(dd):
    gs = global_strings((dd.read("Interface\\FrameXML\\GlobalStrings.lua") or b"").decode("latin-1"))
    cats = []
    for fname, title, group in FRAMES:
        raw = dd.read("Interface\\FrameXML\\" + fname)
        if raw:
            cats.append(category(raw.decode("latin-1"), title, group, gs))
    ui = {k: gs.get(v, v.title()) for k, v in UI_FALLBACK.items()}
    ui.update(UI_PLAIN)
    return {"ui": ui, "quality": [], "categories": cats, "errors": []}


def tips(dd):
    """The loading-screen tips from GameTips.dbc, without the colour codes and the 'Tip:' prefix (the screen adds a gold one)."""
    from . import dbc
    raw = dd.read("DBFilesClient\\GameTips.dbc")
    if not raw:
        return []
    t = dbc.DBC(raw)
    out = []
    for r in t.rows:
        s = re.sub(r"\|c[0-9a-fA-F]{8}|\|r", "", t.string(r[1])).strip()
        s = re.sub(r"^Tip:\s*", "", s)
        if s:
            out.append(s)
    return out
