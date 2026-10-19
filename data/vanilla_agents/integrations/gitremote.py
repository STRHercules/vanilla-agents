"""Which GitHub repository the open project belongs to, from plain file reads of its git config (never runs git).
Remote forms: https://github.com/o/r(.git), https://user:pw@host/o/r, ssh://git@host(:port)/o/r.git, git@host:o/r.git, git://host/o/r.
GitHub Enterprise hosts count when their name contains 'github' or when the owner names the host in Options. Credentials inside a
remote address are never kept or returned."""
import os
import re

from ..events import worktree

_SCP = re.compile(r"^(?:[A-Za-z0-9._-]+@)?([A-Za-z0-9.-]+):(?!//)/?([^\s]+)$")
_URL = re.compile(r"^(?:https?|ssh|git|git\+ssh|ssh\+git)://(?:[^@/\s]+@)?([A-Za-z0-9.-]+)(?::\d+)?/([^\s]+)$", re.I)
_PART = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
PREFERRED = ("origin", "upstream")


def parse_remote(url):
    """{host, owner, repo} for a remote address, or None. The host is lower-case; '.git' and a trailing '/' are dropped."""
    u = (url or "").strip()
    m = _URL.match(u) or _SCP.match(u)
    if not m:
        return None
    host, path = m.group(1).lower(), m.group(2).strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(_PART.match(p) and p not in (".", "..") for p in parts):
        return None
    if host == "ssh.github.com" or host == "www.github.com":
        host = "github.com"
    return {"host": host, "owner": parts[0], "repo": parts[1]}


def is_github(host, extra_host=""):
    h = (host or "").lower()
    return h == "github.com" or "github" in h or (bool(extra_host) and h == extra_host.lower())


def remotes(config_text):
    """{name: url} from a git config file's text ([remote "name"] url = ...)."""
    out, cur = {}, None
    for raw in (config_text or "").splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.match(r'^\[\s*remote\s+"([^"]+)"\s*\]$', line)
        if m:
            cur = m.group(1)
            continue
        if line.startswith("["):
            cur = None
            continue
        if cur and "=" in line:
            k, v = line.split("=", 1)
            if k.strip().lower() == "url" and cur not in out:
                out[cur] = v.strip().strip('"')
    return out


def config_path(project):
    """The git config file of the checkout holding `project` (main checkout, linked worktree or submodule), or None."""
    loc = worktree.locate(project) if project else {}
    if not loc:
        return None
    g = os.path.join(loc["root"], ".git")
    if os.path.isdir(g):
        return os.path.join(g, "config")
    try:
        with open(g, encoding="utf-8", errors="replace") as f:
            line = f.read(4096).strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gd = line[7:].strip()
    gd = os.path.normpath(gd if os.path.isabs(gd) else os.path.join(loc["root"], gd))
    try:
        with open(os.path.join(gd, "commondir"), encoding="utf-8") as f:
            common = f.read(4096).strip()
        gd = os.path.normpath(common if os.path.isabs(common) else os.path.join(gd, common))
    except OSError:
        pass
    return os.path.join(gd, "config")


def find(project, extra_host=""):
    """{host, owner, repo, remote, slug, web} of the project's GitHub remote (origin first, then upstream, then any), or None."""
    p = config_path(project)
    if not p:
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read(200_000)
    except OSError:
        return None
    rs = remotes(text)
    order = [n for n in PREFERRED if n in rs] + sorted(n for n in rs if n not in PREFERRED)
    for name in order:
        r = parse_remote(rs[name])
        if r and is_github(r["host"], extra_host):
            return {**r, "remote": name, "slug": "%s/%s" % (r["owner"], r["repo"]), "web": "https://%s/%s/%s" % (r["host"], r["owner"], r["repo"])}
    return None


def branch(project):
    """The checked-out branch of the project ('' when unknown or detached)."""
    b = (worktree.locate(project) or {}).get("branch", "") if project else ""
    return "" if b.startswith("detached") else b


def has_actions(project):
    """True when the checkout has GitHub Actions workflow files (.github/workflows/*.yml or *.yaml)."""
    loc = worktree.locate(project) if project else {}
    d = os.path.join(loc["root"], ".github", "workflows") if loc else ""
    try:
        return bool(d) and any(n.lower().endswith((".yml", ".yaml")) for n in os.listdir(d))
    except OSError:
        return False
