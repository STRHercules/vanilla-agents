"""Plain words for quests (D-360..D-369). No model: a global jargon dictionary (app/config/glossary.json) swaps known jargon for plain words, a per-project
hand-written file (<ideas_root>/<slug>/quests.json) overrides whole quests, and anything still full of jargon is flagged needs_plain so the Idea panel can say
'N quests need plain words'. Stdlib only."""
import json
import os
import re

from . import idea

SIZES, RISKS = idea.SIZES, idea.RISKS
# code-looking words we cannot explain: `backticks`, ALLCAPS (2+ letters), snake_case, a::path, file.rs, camelCase/CamelCase with a second capital
_JARGON = re.compile(r"`[^`]+`|\b[A-Z][A-Z0-9]{1,}[a-z]?\b|\b[A-Za-z0-9]+_[A-Za-z0-9_]+\b|\b\w+::\w+|\b[\w-]+\.(?:rs|py|js|json|toml|md|adt|m2|wmo)\b|\b[a-z]+[A-Z]\w*\b|\b[A-Z][a-z]+[A-Z]\w*\b")


def _lookup(glossary):
    return {k.lower(): v for k, v in (glossary or {}).items()}


def plainify(title, glossary):
    """(plain text, needs_plain). Known jargon (any case, plural 's' allowed) becomes its plain words; unknown code-looking words stay and set needs_plain."""
    g = _lookup(glossary)
    text = re.sub(r"`([^`]+)`", lambda m: m.group(1) if m.group(1).lower() in g else m.group(0), title)
    for term in sorted(g, key=len, reverse=True):
        text = re.sub(r"(?<![\w`])%s(?:s|es)?(?![\w`])" % re.escape(term), lambda m, t=term: g[t], text, flags=re.I)
    return text, bool(_JARGON.search(text))


def load_overrides(ideas_root, slug):
    """(per-quest overrides {quest id: {plain, why, size, risk}}, ignore_docs [names]) from <ideas_root>/<slug>/quests.json; ({}, []) when missing or unreadable."""
    try:
        with open(os.path.join(ideas_root, slug, "quests.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}, []
    if not isinstance(raw, dict):
        return {}, []
    ign = raw.get("ignore_docs")
    return {k: v for k, v in raw.items() if isinstance(v, dict)}, [x for x in ign if isinstance(x, str)] if isinstance(ign, list) else []


def plan(repo, slug, ideas_root, glossary):
    """Every open plan item as {id, title (plain), details (original text), source, why, size, risk, needs_plain}. The id is stable (hash of the original text)."""
    ov, ign = load_overrides(ideas_root, slug) if ideas_root else ({}, [])
    out = []
    for title, src in idea.quests(repo, ign):
        qid = idea.quest_id(slug, title)
        o = ov.get(qid, {})
        plain = o.get("plain") if isinstance(o.get("plain"), str) and o["plain"].strip() else ""
        auto, needs = plainify(title, glossary)
        out.append({"id": qid, "title": plain.strip() or auto, "details": title, "source": src, "needs_plain": False if plain else needs,
                    "why": o["why"].strip() if isinstance(o.get("why"), str) else "",
                    "size": o["size"] if o.get("size") in SIZES else "", "risk": o["risk"] if o.get("risk") in RISKS else ""})
    return out


def needs_plain_count(items):
    return sum(1 for q in items if q["needs_plain"])
