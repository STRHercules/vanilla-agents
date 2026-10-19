"""The Idea panel's calls (D-344): read the project's IDEA.md, draft it if missing, approve it (and seed the plan's quests). The file lives outside the repo;
approval goes through approvals.py and the feed exactly like a quest's plan approval, keyed by a hash of the text so an edit shows as 'changed'."""
import time

from ..events.feed import Feed
from ..onboard import idea, plainify
from . import approvals, tiers
from .verify_job import ideas_root, slug_of


def _where(ctx):
    proj = ctx.state.project
    e = ctx.verify.entry(proj) if proj else None
    return (proj, e, ideas_root(ctx.cfg["app"]), slug_of(e)) if e else (proj, None, None, None)


def payload(ctx):
    """{state: none|missing|unapproved|changed|approved, goal, non_goals, done_means, rules, out_of_scope}. 'none' = the project is not set up."""
    proj, e, root, slug = _where(ctx)
    if not e:
        return {"state": "none"}
    d = idea.read(root, slug)
    if d is None:
        return {"state": "missing"}
    items = plainify.plan(e["repo"], slug, root, ctx.cfg["glossary"]["terms"])
    auto = [i["title"] for i in tiers.autos(ctx)]
    return {"state": idea.status(d, approvals.load(ctx.cfg["app"]["approvals_path"], proj)), **d, "key": idea.key_of(d), "auto": auto,
            "summary": idea.summary(d, ctx.cfg["strings"]["ui"], auto), "needs_plain": plainify.needs_plain_count(items)}


def draft(ctx):
    proj, e, root, slug = _where(ctx)
    if not e:
        raise ValueError("this project is not set up yet; add it first")
    idea.write_draft(root, slug, e["repo"], time.strftime("%Y-%m-%d"))
    return payload(ctx)


def approve(ctx, key, who="you"):
    """Record approval of the Idea exactly as the person saw it (`key` = the Idea key the window showed, D-384), announce it in the feed, then show the plan's items as quests (never twice).
    Refused when the Idea has no goal or has changed since it was shown."""
    proj, e, root, slug = _where(ctx)
    d = idea.read(root, slug) if e else None
    if d is None:
        raise ValueError("there is no Idea to approve yet")
    if not d["goal"].strip():
        raise ValueError("The Idea has no goal yet. Write one sentence about what we are building, then approve it.")
    a = ctx.cfg["app"]
    if key != idea.key_of(d):
        raise ValueError("The Idea changed after you looked at it. Look at it again, then approve.")
    ctx.state.refresh()
    if approvals.add(a["approvals_path"], proj, key, "Idea", time.time(), a["approvals_max_tasks"], a["approvals_max_projects"], who=who, why="approved by " + who):
        ctx.state.feed.append("plan_approved", task=key, data={"title": "the Idea", "who": who})
    ctx.state.refresh()
    with ctx.state.lock:
        have = set(ctx.state.f.tasks)
    added = idea.seed_quests(e["repo"], slug, Feed(ctx.feed_path), have, plainify.plan(e["repo"], slug, root, ctx.cfg["glossary"]["terms"]))
    ctx.state.refresh()
    return {**payload(ctx), "seeded": added, "auto_approved": tiers.sweep(ctx)}
