"""Approval tiers (D-370..D-399). Pure rules, no model: from a quest's own words and signals decide
  never: pushing, public publishing, deleting outside the project, spending real money -> never done AUTOMATICALLY; the owner can still say Yes himself
  ask:   not small, risky (risky words, out-of-scope or checker paths, a failed check, unknown size or risk, a pending question) -> the owner decides (Yes / Change it / Explain it more)
  auto:  small AND low risk AND in scope -> goes ahead, recorded in approvals.py as who='auto' with the reason, undoable until the quest is done.
Rules live in app.json#tiers; wording in strings/ui.json tier.*. Only quests that come from the project's current plan (the docs, with the size and risk in quests.json) are ever auto-approved.
NEVER is a label and a gate on the automatic yes. It is not enforcement: an agent that has the shell can still run any command (docs/LIMITATIONS.md)."""
import re
import time

from ..verify.redact import redact
from ..onboard import idea, plainify
from . import approvals, route
from .verify_job import ideas_root, slug_of

_PATHISH = re.compile(r"`([^`\n]+)`")
_PATHLIKE = re.compile(r"[\w.\-*]*[\\/][\w.\-*\\/]*|[\w\-]+\.[A-Za-z]\w{0,5}\b")       # anything with a slash, or a file name with an extension, backticked or not
_OUTSIDE = re.compile(r"(?<![\w.])(?:[A-Za-z]:[\\/]|\.\.[\\/]|~[\\/]|/(?:home|etc|usr|var|users|c|mnt)/)", re.I)
_PLAN_TTL = 2.0      # seconds the quest list read from the docs is reused by the window (a decision to approve always re-reads)


def _word(w):
    """Whole word, with a plural or an -ing/-ed ending allowed (push, pushes, pushing; delete, deleting). 'pushback' is not 'push' and 'author' is not 'auth'."""
    w = w.lower()
    alts = [re.escape(w) + r"(?:s|es|d|ed|ing)?"]
    if w.endswith("e"):
        alts.append(re.escape(w[:-1]) + "ing")
    return r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)"


def _has(words, low):
    return next((w for w in words if re.search(_word(w), low)), None)


def _scope_hit(text, oos, route_rules):
    """'intent' | 'trusted' | '': does the quest's own text point at a place the Idea says to leave alone, or at the checker's files? Not only backticked paths (D-383)."""
    paths = {p.strip().replace("\\", "/") for p in _PATHISH.findall(text) + _PATHLIKE.findall(text)}
    why = {r[0] for r in route.classify([{"path": p, "lines": 0} for p in paths if p], route_rules, out_of_scope=oos)["reasons"]} if paths else set()
    low = text.lower()
    for pat in oos:      # a plain mention of the folder ('tidy the legacy folder') counts too
        lit = re.split(r"[*?\[]", pat)[0].strip("/").split("/")[0]
        if len(lit) >= 3 and re.search(r"(?<![\w-])" + re.escape(lit.lower()) + r"(?![\w-])", low):
            why.add("intent")
    return "intent" if "intent" in why else "trusted" if "trusted" in why else ""


def decide(task, rules, words, route_rules, out_of_scope=()):
    """-> {tier: never|ask|auto, code, why (plain words)}. `words` = strings/ui.json; `task` = a quest dict
    (title, details, why, size, risk, status, sent_back, vetoed, explain_pending, change_pending)."""
    text = " ".join(str(task.get(k) or "") for k in ("title", "details", "why"))
    low = text.lower()
    for cat, ws in rules["never"].items():
        if cat == "delete":
            hit = _has(ws, low) and _OUTSIDE.search(text)       # deleting INSIDE the project is only 'risky'
        else:
            hit = _has(ws, low)
        if hit:
            return {"tier": "never", "code": cat, "why": words["tier.never_" + cat]}
    ask = lambda code, **v: {"tier": "ask", "code": code, "why": words["tier.ask_" + code].format(**v)}
    if task.get("vetoed"):
        return ask("vetoed")
    if task.get("size") in rules["ask_sizes"]:
        return ask("big")
    if task.get("risk") == "high":
        return ask("high")
    w = _has(rules["risky_words"], low)
    if w:
        return ask("word", word=w)
    hit = _scope_hit(text, out_of_scope, route_rules)
    if hit:
        return ask("scope" if hit == "intent" else "trusted")
    if task.get("status") == "failed" or task.get("sent_back"):
        return ask("failed")
    if task.get("explain_pending") or task.get("change_pending"):      # a question or a change is open: nothing starts by itself until it is answered (D-386)
        return ask("asked")
    if task.get("size") != "small" or task.get("risk") != "low":      # D-383: automatic means small AND low risk; anything else, or unknown, asks
        return ask("unknown")
    return {"tier": "auto", "code": "auto", "why": words["tier.auto"]}


def _scope(ctx):
    proj = ctx.state.project
    e = ctx.verify.entry(proj) if proj else None
    return (proj, e, idea.out_of_scope(ideas_root(ctx.cfg["app"]), slug_of(e)) if e else []) if proj else (None, None, [])


def current(ctx, force=False):
    """{quest id: plan item} for THIS project, read from its docs and quests.json as they are now (D-383). The window reuses it for a couple of seconds; deciding always re-reads."""
    proj, e, _ = _scope(ctx)
    if not e:
        return {}
    got, now = getattr(ctx, "_plan", None), time.monotonic()
    if not force and got and got[0] == proj and now - got[1] < _PLAN_TTL:
        return got[2]
    items = {i["id"]: i for i in plainify.plan(e["repo"], slug_of(e), ideas_root(ctx.cfg["app"]), ctx.cfg["glossary"]["terms"])}
    ctx._plan = (proj, now, items)
    return items


def _fresh(task, item):
    """The quest as the plan says it is NOW (wording, size, risk); a seeded copy in the append-only feed may be old (D-383)."""
    return {**task, **{k: item[k] for k in ("title", "details", "why", "size", "risk")}} if item else task


def annotate(ctx, snap):
    """Add `tier` ({tier, code, why}) and `vetoable` to every quest in a snapshot (copies; D-373). Quests the owner already approved carry no tier."""
    rules = ctx.cfg["app"].get("tiers")
    if not rules or not rules["enabled"]:
        return snap
    oos, out, items = _scope(ctx)[2], [], current(ctx)
    ui, rr = ctx.cfg["strings"]["ui"], ctx.cfg["app"]["route"]
    for t in snap["tasks"]:
        auto = t.get("approved_by") == "auto" and t.get("approved")
        d = None if t.get("approved") else decide(_fresh(t, items.get(t["id"])), rules, ui, rr, oos)
        if d and d["tier"] == "auto" and t["id"] not in items:       # not in the project's plan: it will never start by itself, so do not say it will
            d = {"tier": "ask", "code": "unknown", "why": ui["tier.ask_unknown"]}
        out.append({**t, "tier": d,
                    "vetoable": bool(auto and t["status"] != "complete")})        # D-376/D-385: undo works until the quest is done
    return {**snap, "tasks": out}


def _task(ctx, task_id):
    ctx.state.refresh()
    with ctx.state.lock:
        t = ctx.state.f.tasks.get(task_id)
        t = dict(t) if t else None
    if t is None:
        raise ValueError("there is no quest called %s" % task_id)
    return t


def autos(ctx, tasks=None):
    """[plan item] that would start by themselves, or that do in `tasks` ({id: seeded quest}). Only quests of the project's current plan can (D-383)."""
    rules, a = ctx.cfg["app"].get("tiers"), ctx.cfg["app"]
    oos = _scope(ctx)[2]
    if not rules or not rules["enabled"]:
        return []
    out = []
    for qid, item in current(ctx, force=True).items():
        t = (tasks or {}).get(qid, {"id": qid, "status": "new"})
        if tasks is not None and qid not in tasks or t.get("approved") or t.get("vetoed"):
            continue
        if decide(_fresh(t, item), rules, ctx.cfg["strings"]["ui"], a["route"], oos)["tier"] == "auto":
            out.append(item)
    return out


def sweep(ctx):
    """Auto-approve every seeded quest of this project's current plan that decides 'auto', once the Idea is approved (D-371). Returns how many."""
    a = ctx.cfg["app"]
    proj, e, _ = _scope(ctx)
    if not a.get("tiers") or not a["tiers"]["enabled"] or not e:
        return 0
    d = idea.read(ideas_root(a), slug_of(e))
    if d is None or idea.status(d, approvals.load(a["approvals_path"], proj)) != "approved":
        return 0
    ctx.state.refresh()
    with ctx.state.lock:
        have = {k: dict(t) for k, t in ctx.state.f.tasks.items()}
    n = 0
    for item in autos(ctx, have):
        r = decide(_fresh(have[item["id"]], item), a["tiers"], ctx.cfg["strings"]["ui"], a["route"], _scope(ctx)[2])
        if approvals.add(a["approvals_path"], proj, item["id"], item["title"], time.time(), a["approvals_max_tasks"], a["approvals_max_projects"], who="auto", why=r["why"]):
            ctx.state.feed.append("quest_auto_approved", task=item["id"], data={"title": item["title"], "why": r["why"]})
            n += 1
    ctx.state.refresh()
    return n


def veto(ctx, task_id, who="you"):
    """Undo an automatic yes until the quest is done (D-376, D-385)."""
    t, a = _task(ctx, task_id), ctx.cfg["app"]
    if t.get("approved_by") != "auto" or not t.get("approved") or t["status"] == "complete":
        raise ValueError("this quest cannot be undone any more")
    if approvals.veto(a["approvals_path"], ctx.state.project, task_id, time.time()):
        ctx.state.feed.append("quest_vetoed", task=task_id, data={"title": t["title"], "who": who})
    ctx.state.refresh()


def request(ctx, task_id, kind, note=""):
    """'Explain it more' / 'Change it' (D-374): one event in the feed for an interactive Claude session to answer (scripts/answer_explain.py). No model call here.
    'Change it' also takes the quest back to the ask tier (D-386): an automatic yes is undone and nothing is auto-approved until the change is made."""
    if kind not in ("explain", "change"):
        raise ValueError("kind must be explain or change")
    t = _task(ctx, task_id)
    if kind == "change" and t.get("approved") and t.get("approved_by") == "auto" and t["status"] != "complete":
        veto(ctx, task_id)
    data = {"title": t["title"]}
    note = redact(str(note or "").strip())[:300]
    if kind == "change" and note:
        data["note"] = note          # what the owner wants different, in his own words (shown to the Claude session that makes the change)
    ctx.state.feed.append(kind + "_requested", task=task_id, data=data)
    ctx.state.refresh()
