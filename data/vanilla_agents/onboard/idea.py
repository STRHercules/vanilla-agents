"""The project's Idea (D-340..D-349): what we build, what we do not, what done means. One IDEA.md per project, kept OUTSIDE the repo
(<ideas_root>/<slug>/IDEA.md) so an agent working in the repo cannot rewrite its own goals. Drafted from the repo's own docs by plain
heading/list extraction (no model). Approved through the existing plan-approved record (approvals.py): the approval key holds a hash of
the text, so any later edit shows as 'Idea changed'. Stdlib only."""
import hashlib
import json
import os
import re
from pathlib import Path

SECTIONS = (("goal", "Goal"), ("non_goals", "Not building"), ("done_means", "Done means"), ("rules", "Rules"), ("out_of_scope", "Out-of-scope paths"))
DOCS = tuple(x + n for n in ("README*", "CLAUDE.md", "HANDOFF*", "OVERVIEW*", "*ROADMAP*", "*PLAN*", "DESIGN*", "NEXT*STEPS*", "TODO*") for x in ("", "docs/"))
PLAN_FILES = re.compile(r"^(roadmap|plan|todo|next[-_ ]?steps?)|roadmap\.", re.I)      # a whole file of plan items (other docs: only lists under a plan-like heading)
HEAD = {   # section -> heading words that feed it
    "goal": re.compile(r"\b(goal|purpose|vision|mission|overview|about|what (is|we)|summary)\b", re.I),
    "non_goals": re.compile(r"non[- ]?goals?|not (building|doing|in scope|planned)|out of scope|won'?t (do|build)|will not", re.I),
    "done_means": re.compile(r"definition of done|done means|done when|^[\d. ]*definition\b|acceptance|success criteria|exit criteria|complete when", re.I),
    "rules": re.compile(r"\brules?\b|constraints?|conventions?|guidelines?|principles?|must not|\bnever\b|\balways\b|do not", re.I),
}
PLAN_HEAD = re.compile(r"next|roadmap|to-?do|\bplan\b|steps|milestones?|remaining|backlog|priorit", re.I)
NOT_PLAN = re.compile(r"definition|\bdone\b|decisions? i need|questions?|review flags|non[- ]?goals?|out of scope|principles|\brules?\b|scope", re.I)   # lists under these are not quests
TITLE_COLS = re.compile(r"^(task|item|step|milestone|title|work|what|goal)s?$", re.I)
NUM_COLS = re.compile(r"^(#|no\.?|n|step|nr)$", re.I)
NOTE = re.compile(r"\bNOT done\b|\bDONE since\b|\bwhat exists\b")
_STATE = re.compile(r"^(partial|todo|open|next|in progress)\b\s*(\([^)]*\))?\s*[:-]?\s*", re.I)
NO_TOUCH = re.compile(r"\b(do not|don't|never|must not)\b.{0,20}\b(touch|edit|modify|change|delete)\b", re.I)
PATHISH = re.compile(r"^[\w.*\-]+(/[\w.*\-/]*)+$|^[\w\-]+\.\w{1,6}$|^\*\*?[\w/.*-]*$")
MAX_ITEMS, MAX_QUESTS, MAX_LEN = 12, 60, 160
SIZES, RISKS = ("small", "medium", "big"), ("low", "high")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[( |x|X)\]\s+)?(.+?)\s*$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _plain(s):
    """Markdown noise out of one line: links, emphasis, backticks kept (paths need them), trailing colons."""
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1", r"\2", s)
    return re.sub(r"\s+", " ", s).strip(" :")[:MAX_LEN]


def _docs(repo, ignore=()):
    """[(relative path, text)] for the doc files that exist, in DOCS order, no repeats. Only the repo's top two levels, plain .md/.txt files, 200 KB at most.
    `ignore` = file names or relative paths to leave out (per-project ignore_docs, D-362)."""
    skip = {x.lower().replace("\\", "/") for x in ignore}
    seen, out = set(), []
    for pat in DOCS:
        for p in sorted(Path(repo).glob(pat)):
            rel = p.relative_to(repo).as_posix()
            if p.is_file() and p.suffix.lower() in ("", ".md", ".txt") and rel not in seen and p.name.lower() not in skip and rel.lower() not in skip and p.stat().st_size <= 200_000:
                seen.add(rel)
                try:
                    out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
    return out


def _blocks(text):
    """[(heading, [lines])]: lines under each heading (heading '' = before the first); fenced code skipped."""
    out, fence = [("", [])], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = _HEADING.match(ln)
        if m:
            out.append((m.group(2), []))
        else:
            out[-1][1].append(ln)
    return out


def _tables(lines):
    """[(header cells, [row cells])] for each markdown table in the lines (a header row, a |---| row, then rows)."""
    out, cur = [], None
    for ln in lines:
        if not ln.lstrip().startswith("|"):
            cur = None
            continue
        cells = [_plain(c) for c in ln.strip().strip("|").split("|")]
        if re.fullmatch(r"[\s:|-]+", ln):
            continue
        if cur is None:
            cur = (cells, [])
            out.append(cur)
        else:
            cur[1].append(cells)
    return out


def _items(lines):
    return [_plain(m.group(2)) for m in map(_BULLET.match, lines) if m and not m.group(1) == "x" and not m.group(1) == "X"]


def _paragraph(lines):
    for chunk in re.split(r"\n\s*\n", "\n".join(lines)):
        t = _plain(" ".join(chunk.split()))
        if len(t) > 20 and not chunk.lstrip().startswith(("|", "![", "<", "[!", ">", "-", "*", "+")) and not re.match(r"^\d+[.)]\s", chunk.lstrip()):
            return t
    return ""


def _paths(items):
    """Backtick spans that look like paths or globs (src/, *.lock, Cargo.toml)."""
    out = []
    for it in items:
        for span in re.findall(r"`([^`]+)`", it):
            if PATHISH.match(span.strip()) and span.strip() not in out:
                out.append(span.strip())
    return out


def draft(repo):
    """Idea sections drawn from the repo's own docs: {goal, non_goals, done_means, rules, out_of_scope, sources}. Missing parts stay empty ('' / [])."""
    d = {"goal": "", "non_goals": [], "done_means": [], "rules": [], "out_of_scope": [], "sources": []}
    for rel, text in _docs(repo):
        blocks, used = _blocks(text), False
        title = next((h for h, _ in blocks if h), "")
        if not d["goal"] and rel.lower().startswith(("readme", "claude")):
            intro = _paragraph(blocks[0][1]) or next((_paragraph(ls) for h, ls in blocks if h and (h == title or HEAD["goal"].search(h)) and _paragraph(ls)), "")
            if intro:
                d["goal"], used = intro, True
        for h, ls in blocks:
            if not h:
                continue
            for hd, rows in _tables(ls):     # a scope table: a row marked OUT in any later column is a non-goal
                got = [r[0] for r in rows if r and r[0] and any(re.match(r"out\b", c, re.I) for c in r[1:]) and r[0] not in d["non_goals"]]
                d["non_goals"] += got
                used = used or bool(got)
            for key in ("non_goals", "done_means", "rules"):
                if HEAD[key].search(h) and not (key == "done_means" and re.search(r"\b(not|isn'?t)\b", h, re.I)):
                    got = _items(ls) + [r[0] for hd, rows in _tables(ls) for r in rows if r and r[0] and len(hd) < 3]   # bullets, plus one-column-ish tables
                    d[key] += [x for x in got if x and x not in d[key]]
                    used = used or bool(got)
                    break
        d["sources"].append(rel) if used else None
    for k in ("non_goals", "done_means", "rules"):
        d[k] = d[k][:MAX_ITEMS]
    d["out_of_scope"] = _paths(d["non_goals"] + [r for r in d["rules"] if NO_TOUCH.search(r)])[:MAX_ITEMS]
    return d


def render(d, when=""):
    """IDEA.md text. The first comment line says where it came from; it is not part of the approved text."""
    out = ["# Idea", "<!-- drafted from: %s%s. Edit freely; you must approve the Idea again after an edit. -->" % (", ".join(d.get("sources") or ["nothing found"]), " on " + when if when else "")]
    for key, name in SECTIONS:
        out += ["", "## " + name]
        v = d.get(key)
        out += [v or "(write one sentence)"] if key == "goal" else ["- " + x for x in v] or ["- (none yet)"]
    return "\n".join(out) + "\n"


def parse(text):
    """IDEA.md text -> {goal, non_goals, done_means, rules, out_of_scope}. Unknown headings and the '(none yet)' placeholders are ignored."""
    names = {n.lower(): k for k, n in SECTIONS}
    d, cur = {"goal": "", "non_goals": [], "done_means": [], "rules": [], "out_of_scope": []}, None
    for ln in text.replace("\r\n", "\n").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            cur = names.get(m.group(1).strip().lower())
        elif cur and not ln.strip().startswith("<!--") and ln.strip():
            if cur == "goal":
                d["goal"] = (d["goal"] + " " + ln.strip()).strip() if not ln.strip().startswith("(write") else d["goal"]
            else:
                b = _BULLET.match(ln)
                v = (b.group(2) if b else ln.strip()).strip()
                if v and not v.startswith("(none"):
                    d[cur].append(v.strip("`") if cur == "out_of_scope" else v)
    return d


def idea_hash(d):
    """Fingerprint of the Idea's meaning: the parsed sections, so comments, spacing and line endings do not count as a change."""
    parts = [d["goal"]] + ["%s:%s" % (k, "|".join(d[k])) for k in ("non_goals", "done_means", "rules", "out_of_scope")]
    if d.get("quests"):
        parts.append("quests:" + "|".join(d["quests"]))       # D-383: the quests' size and risk are part of the approval; absent without quests.json, so older approvals keep their hash
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def quest_meta(ideas_root, slug):
    """D-383: ["<quest id>:<size>:<risk>"] (sorted) from the project's quests.json. Size and risk decide which quests start by themselves, so they are part of what the owner approves."""
    try:
        with open(os.path.join(ideas_root, slug, "quests.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    return sorted("%s:%s:%s" % (k, v.get("size") if v.get("size") in SIZES else "", v.get("risk") if v.get("risk") in RISKS else "") for k, v in raw.items() if isinstance(v, dict) and (v.get("size") or v.get("risk")))


def key_of(d):
    """Approval record id (a quest-id-shaped string kept in approvals.json). The 'idea:' prefix is reserved: it never becomes a quest."""
    return "idea:" + idea_hash(d)[:12]


def path_of(ideas_root, slug):
    return os.path.join(ideas_root, slug, "IDEA.md")


def read(ideas_root, slug):
    """Parsed Idea for a project, or None if it has none."""
    try:
        d = parse(Path(path_of(ideas_root, slug)).read_text(encoding="utf-8"))
    except OSError:
        return None
    d["quests"] = quest_meta(ideas_root, slug)
    return d


def write_draft(ideas_root, slug, repo, when=""):
    """Draft IDEA.md from the repo's docs unless the project already has one (never overwrites human edits). Returns True when written."""
    p = path_of(ideas_root, slug)
    if os.path.exists(p):
        return False
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(draft(repo), when))
    os.replace(tmp, p)
    return True


def status(d, approved_keys):
    """'approved' (this exact text was approved), 'changed' (an earlier text was approved, this one was not) or 'unapproved' (never approved)."""
    keys = [k for k in approved_keys if str(k).startswith("idea:")]
    return "approved" if key_of(d) in keys else "changed" if keys else "unapproved"


def summary(d, words, auto=()):
    """D-377: the one mandatory yes, in plain words, from the Idea's own sections:
    'If you say yes: we build X. We will NOT build Y. It is done when Z.' `words` = strings/ui.json (idea.summary*).
    D-384: then which quests will start by themselves (`auto` = their plain titles), so the yes is not blind to them."""
    j = lambda items, none: "; ".join(x.rstrip(".") for x in items) or words[none]
    out = words["idea.summary"].format(goal=(d["goal"] or words["idea.summary_goal"]).rstrip("."), nots=j(d["non_goals"], "idea.summary_nots"), done=j(d["done_means"], "idea.summary_done"))
    names = "; ".join(x.rstrip(".") for x in list(auto)[:8]) + (" ..." if len(auto) > 8 else "")
    return out + " " + (words["idea.auto_none"] if not auto else words["idea.auto_one"].format(names=names) if len(auto) == 1 else words["idea.auto_many"].format(n=len(auto), names=names))


def out_of_scope(ideas_root, slug):
    """Out-of-scope path patterns from the project's IDEA.md (empty when there is none)."""
    d = read(ideas_root, slug)
    return [x + "**" if x.endswith("/") else x for x in d["out_of_scope"]] if d else []     # 'legacy/' means everything under it


def quests(repo, ignore_docs=()):
    """Open plan items from roadmap/next-steps style files: numbered items and unchecked boxes. In a file named roadmap/plan/todo/next-steps every
    list counts; in any other doc only lists under a heading like Next / Roadmap / To do / Plan / Steps. Done boxes ([x]) are skipped, and so are status notes about
    finished work ('...; NOT done: ...', 'DONE since', a lowercase note with a ';', D-361). ignore_docs leaves whole files out. [(title, source file)]."""
    out, seen = [], set()

    def add(t, rel):
        t = _STATE.sub("", t)
        if len(t) > 3 and not NOTE.search(t) and not (t[0].islower() and ";" in t) and not re.match(r"(done|complete)", t, re.I) and t.lower() not in seen:
            seen.add(t.lower())
            out.append((t, rel))

    docs = sorted(_docs(repo, ignore_docs), key=lambda d: not PLAN_FILES.search(Path(d[0]).name))     # real plan files first: their order is the plan's order
    for rel, text in docs:
        blocks = _blocks(text)
        whole = bool(PLAN_FILES.search(Path(rel).name)) and not any(h and PLAN_HEAD.search(h) for h, _ in blocks[2:])    # a plan file with plan headings: those only (the title line does not count)
        for h, ls in blocks:
            if not (whole or (h and PLAN_HEAD.search(h))) or (h and NOT_PLAN.search(h) and not PLAN_HEAD.search(h)):
                continue
            planned = bool(h and PLAN_HEAD.search(h)) and not whole      # plain bullets count only under a Next/Roadmap heading of an ordinary doc
            for ln in ls:
                m = _BULLET.match(ln)
                if m and m.group(1) not in ("x", "X") and (_NUMBERED.match(ln) or m.group(1) == " " or planned):
                    if not (ln.lstrip()[:1] in "-*+" and m.group(1) is None and not planned):
                        add(_plain(m.group(2)), rel)
            for hd, rows in _tables(ls):     # a plan table: a '#'/Step column or a Task/Item column; DONE rows are finished work
                col = next((i for i, c in enumerate(hd) if TITLE_COLS.match(c)), 1 if hd and NUM_COLS.match(hd[0]) and len(hd) > 1 else None)
                for r in rows:
                    if col is not None and len(r) > col and r[col] and not re.match(r"done", r[col], re.I):
                        add(r[col], rel)
    return out[:MAX_QUESTS]


def quest_id(slug, title):
    return "%s:q:%s" % (slug, hashlib.sha256(re.sub(r"\W+", " ", title.lower()).strip().encode("utf-8")).hexdigest()[:10])


def seed_quests(repo, slug, feed, have, items=None):
    """Announce every plan item as a quest (task_created through the normal feed). `have` = quest ids already shown; those are skipped, so seeding twice adds nothing.
    `items` = plainify.plan(...) dicts (plain title, why, size, risk, details); without it the raw doc text is the title. Returns the number added."""
    n = 0
    for q in items if items is not None else [{"id": quest_id(slug, t), "title": t} for t, _ in quests(repo)]:
        qid = q["id"]
        if qid not in have:
            data = {"title": q["title"]}
            if q.get("details") and q["details"] != q["title"]:
                data["details"] = q["details"]
            data.update({k: q[k] for k in ("why", "size", "risk") if q.get(k)})
            if q.get("needs_plain"):
                data["needs_plain"] = "1"
            feed.append("task_created", task=qid, data=data)
            have.add(qid)
            n += 1
    return n
