"""One HTTPS call that returns JSON, for the optional API modes (GitHub REST, Linear GraphQL, Jira Cloud REST).
Secrets travel only in the request headers to that one service. Errors are plain sentences built from the status code and the host,
never from the request (no headers, no token), and every text that leaves here is scrubbed of the secrets it was given.
Tests pass `opener` (a fake urlopen); nothing here is called without one in the test suite."""
import http.client
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MAX_BYTES = 5_000_000
UA = "vanilla-agents"


class HttpError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def scrub(text, secrets=()):
    t = str(text)
    for s in secrets:
        if s and len(s) >= 4:
            t = t.replace(s, "***")
    return t


def _default_opener(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


WHY = {401: "the key or token was refused (wrong or expired)", 403: "access was refused (missing permission or rate limit)",
       404: "not found (wrong name, or the key cannot see it)", 429: "too many requests; try again later"}


def call(url, headers=None, body=None, method=None, opener=None, timeout=15, secrets=(), max_bytes=MAX_BYTES):
    """JSON from `url` (https only). body: a dict sent as JSON. Raises HttpError with a plain, secret-free message."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise HttpError("only https:// addresses are used")
    host = parts.hostname
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"), headers=h)
    try:
        r = (opener or _default_opener)(req, timeout)
        try:
            raw = r.read(max_bytes + 1)
        finally:
            close = getattr(r, "close", None)
            if close:
                close()
    except urllib.error.HTTPError as e:
        raise HttpError("%s answered %s: %s" % (host, e.code, WHY.get(e.code, "an error")), e.code) from None
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:      # V-048: a garbled answer is a plain error too
        reason = getattr(e, "reason", None) or e.__class__.__name__
        raise HttpError(scrub("could not reach %s (%s)" % (host, reason), secrets)) from None
    if len(raw) > max_bytes:
        raise HttpError("%s sent too much" % host)
    try:
        return json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        raise HttpError("%s did not answer with JSON" % host) from None
