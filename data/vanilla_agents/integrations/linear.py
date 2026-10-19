"""Linear API mode (optional): open issues through Linear's GraphQL API with a personal API key (Linear > Settings > Security &
access > Personal API keys). The key goes only to api.linear.app in the Authorization header; never logged or shown back."""
from . import httpjson
from .board import make_item

URL = "https://api.linear.app/graphql"
QUERY = """query Bounties($first: Int!, $filter: IssueFilter) { viewer { name } issues(first: $first, filter: $filter, orderBy: updatedAt) {
 nodes { identifier title description url labels { nodes { name } } } } }"""


class LinearError(Exception):
    pass


def _call(key, query, variables, opener, timeout):
    if not key:
        raise LinearError("paste a Linear API key in Options > Integrations first")
    try:
        d = httpjson.call(URL, {"Authorization": key}, {"query": query, "variables": variables}, opener=opener, timeout=timeout, secrets=(key,))
    except httpjson.HttpError as e:
        raise LinearError(httpjson.scrub(e, (key,))) from None
    if not isinstance(d, dict) or d.get("errors"):
        msg = ((d or {}).get("errors") or [{}])[0].get("message", "an error") if isinstance(d, dict) else "an unexpected answer"
        raise LinearError(httpjson.scrub("Linear answered: %s" % str(msg)[:160], (key,)))
    return d.get("data") or {}


def whoami(key, opener=None, timeout=15):
    return str(((_call(key, "query { viewer { name } }", {}, opener, timeout).get("viewer")) or {}).get("name") or "?")


def issues(key, team="", mine=True, limit=50, opener=None, timeout=15, excerpt_chars=240):
    """(viewer name, [items]) for open issues (not completed / canceled), optionally only one team's (team key like ENG) and only yours."""
    f = {"state": {"type": {"nin": ["completed", "canceled"]}}}
    if team:
        f["team"] = {"key": {"eq": team}}
    if mine:
        f["assignee"] = {"isMe": {"eq": True}}
    d = _call(key, QUERY, {"first": max(1, min(100, limit)), "filter": f}, opener, timeout)
    out = []
    for n in ((d.get("issues") or {}).get("nodes") or []):
        try:
            out.append(make_item("linear", n.get("identifier"), n.get("title"), n.get("description") or "", n.get("url") or "", "issue",
                                 [x.get("name", "") for x in ((n.get("labels") or {}).get("nodes") or [])], excerpt_chars))
        except ValueError:
            continue
    return str((d.get("viewer") or {}).get("name") or "?"), out
