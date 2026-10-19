"""V-044 server calls of the integration hub (all POSTs need the window key; none of them is an agent / MCP tool):
GET  /api/integrations           settings without secrets + status lines
POST /api/integrations           save settings {settings}
POST /api/integrations/test      sign in with what is typed {service, settings?}
POST /api/integrations/refresh   refresh one source now {source: github|linear|jira}
POST /api/integrations/import    a Jira / Linear CSV export {text, kind?: auto|jira|linear} (size-limited)
POST /api/bounty/quest           the owner's "Make it a quest" {id}: a normal, unapproved quest whose why names the source and link
POST /api/bounty/hide            hide one imported bounty {id}"""
from ..events.feed import Feed
from ..integrations import isettings
from ..integrations.board import label_of
from ..integrations.hub import DEFAULTS, Hub
from . import social

GET_CALLS = ("/api/integrations",)
POST_CALLS = ("/api/integrations", "/api/integrations/test", "/api/integrations/refresh", "/api/integrations/import", "/api/bounty/quest", "/api/bounty/hide")


def make_hub(ctx):
    return Hub(ctx.settings_file, lambda: ctx.state.project, opts(ctx.cfg))


def opts(cfg):
    return {**DEFAULTS, **(cfg["app"].get("integrations") or {})}


def body_limit(cfg):
    return opts(cfg)["max_file_bytes"] * 2 + 4096


def get(ctx):
    hub = ctx.integ
    return {**isettings.view(hub.settings()), "status": hub.view(ctx.state.snapshot()["tasks"])["integrations"]["status"],
            "max_file_bytes": hub.o["max_file_bytes"], "fake": bool(hub._fake())}


def make_quest(ctx, item_id):
    """The owner's click: one imported proposal becomes a normal quest (task_created in the feed, unapproved like any other)."""
    if not isinstance(item_id, str):
        raise ValueError("id must be text")
    b = ctx.integ.board()
    it = b.get(item_id)
    if it is None:
        raise ValueError("that bounty is not on the board any more (closed upstream?)")
    if item_id in b.made:
        raise ValueError("that bounty is already a quest")
    tasks = ctx.state.snapshot()["tasks"]
    if it["url"] and any(it["url"] in str(t.get("why") or "").split() for t in tasks):
        raise ValueError("there is already a quest for %s" % it["url"])
    why = "%s: %s" % (label_of(it), it["url"] or "from an imported file")
    if it["excerpt"]:
        why += " - " + it["excerpt"][:300]
    qid = social.owner_quest(Feed(ctx.feed_path), ctx.cfg, ctx.state.project, it["title"], why)
    b.mark_made(item_id, qid)
    ctx.integ._bump()
    return qid


def post(ctx, call, body):
    hub = ctx.integ
    if call == "/api/integrations":
        isettings.save(ctx.settings_file, body.get("settings"), hub.o["refresh_minutes"])
        hub._bump()
        return get(ctx)
    if call == "/api/integrations/test":
        return hub.test(body.get("service"), body.get("settings") if isinstance(body.get("settings"), dict) else None)
    if call == "/api/integrations/refresh":
        if body.get("source") not in ("github", "linear", "jira"):
            raise ValueError("source must be github, linear or jira")
        hub.refresh(body["source"])
        return get(ctx)
    if call == "/api/integrations/import":
        r = hub.import_file(body.get("text"), body.get("kind") or "auto")
        return {**get(ctx), "imported": r}
    if call == "/api/bounty/quest":
        qid = make_quest(ctx, body.get("id"))
        ctx.state.refresh()
        return {**ctx.snap(), "quest": qid}
    if call == "/api/bounty/hide":
        if not isinstance(body.get("id"), str):
            raise ValueError("id must be text")
        hub.board().hide(body["id"])
        hub._bump()
        return ctx.snap()
    raise ValueError("no such call")
