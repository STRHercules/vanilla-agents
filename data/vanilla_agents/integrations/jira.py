"""Jira Cloud API mode (optional): open issues through Jira's REST API (v3) with your e-mail and an API token
(id.atlassian.com > Security > API tokens). Both go only to your https://<site>.atlassian.net, as HTTP basic auth; never logged or shown back."""
import base64
from urllib.parse import quote, urlsplit

from . import httpjson
from .board import make_item

DEFAULT_JQL = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"


class JiraError(Exception):
    pass


def check_site(site):
    s = str(site or "").strip().rstrip("/")
    p = urlsplit(s)
    if p.scheme != "https" or not p.hostname or p.path not in ("", "/") or p.query or "@" in s:
        raise JiraError("the Jira site must look like https://yourteam.atlassian.net")
    return s


def adf_text(node, out=None):
    """Plain text of an Atlassian Document Format description (v3 sends descriptions as a JSON tree)."""
    out = [] if out is None else out
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        if node.get("type") == "text":
            out.append(str(node.get("text", "")))
        for c in node.get("content") or []:
            adf_text(c, out)
        if node.get("type") in ("paragraph", "heading", "listItem"):
            out.append(" ")
    elif isinstance(node, list):
        for c in node:
            adf_text(c, out)
    return "".join(out)


def _get(site, email, token, path, opener, timeout):
    if not (email and token):
        raise JiraError("fill in your Jira e-mail and API token in Options > Integrations first")
    auth = base64.b64encode(("%s:%s" % (email, token)).encode("utf-8")).decode("ascii")
    try:
        return httpjson.call(check_site(site) + path, {"Authorization": "Basic " + auth}, opener=opener, timeout=timeout, secrets=(token, auth))
    except httpjson.HttpError as e:
        raise JiraError(httpjson.scrub(e, (token, auth))) from None


def whoami(site, email, token, opener=None, timeout=15):
    return str((_get(site, email, token, "/rest/api/3/myself", opener, timeout) or {}).get("displayName") or "?")


def issues(site, email, token, jql="", limit=50, opener=None, timeout=15, excerpt_chars=240):
    s = check_site(site)
    d = _get(s, email, token, "/rest/api/3/search/jql?maxResults=%d&fields=summary,description,labels&jql=%s" % (max(1, min(100, limit)), quote(jql or DEFAULT_JQL)), opener, timeout)
    out = []
    for i in (d or {}).get("issues") or []:
        f = i.get("fields") or {}
        try:
            out.append(make_item("jira", i.get("key"), f.get("summary"), adf_text(f.get("description")), "%s/browse/%s" % (s, i.get("key")) if i.get("key") else "",
                                 "issue", f.get("labels") or [], excerpt_chars))
        except ValueError:
            continue
    return out
