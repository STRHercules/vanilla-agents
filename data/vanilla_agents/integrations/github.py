"""GitHub: open issues and pull requests as bounty proposals, and the latest GitHub Actions runs of a branch (read-only).
Two ways in, chosen in Options: the `gh` command (preferred; uses its own sign-in, always an argument list with a timeout, never a
shell) or the REST API with a token the owner pasted (sent only to that GitHub host, never logged or shown back).
Every class takes a fake runner / opener in tests: the test suite never touches the network."""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime

from . import httpjson
from .board import make_item

PR_MODES = ("off", "review_requested", "assigned", "authored", "all")
GH_PR_ARGS = {"review_requested": ["--search", "review-requested:@me"], "assigned": ["--assignee", "@me"], "authored": ["--author", "@me"], "all": []}
REST_PR_QUERY = {"review_requested": "review-requested:{login}", "assigned": "assignee:{login}", "authored": "author:{login}", "all": ""}
MAX_LABELS = 5
FAILED = ("failure", "timed_out", "startup_failure")


class GitHubError(Exception):
    pass


def run_subprocess(args, timeout, cwd=None):
    """(exit code, stdout, stderr). An argument list, never a shell; gh's prompts and colours off."""
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "GH_NO_UPDATE_NOTIFIER": "1", "GH_SPINNER_DISABLED": "1"}
    r = subprocess.run(args, capture_output=True, timeout=timeout, cwd=cwd, env=env, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def iso_ts(s):
    """'2026-10-19T08:00:00Z' -> unix seconds (0 when missing)."""
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def _labels(d):
    return [x.get("name", "") if isinstance(x, dict) else str(x) for x in d.get("labels") or []]


def issue_item(d, kind="issue"):
    return make_item("github", "#%s" % d.get("number", ""), d.get("title"), d.get("body") or "", d.get("url") or d.get("html_url") or "", kind, _labels(d))


def run_item(d):
    """One workflow run, from gh (camelCase) or REST (snake_case)."""
    g = lambda *ks: next((d[k] for k in ks if d.get(k) not in (None, "")), "")
    status, concl = str(g("status")).lower(), str(g("conclusion")).lower()
    return {"name": str(g("workflowName", "name"))[:80] or "CI", "status": status, "conclusion": concl,
            "at": iso_ts(g("updatedAt", "updated_at") if status == "completed" else g("createdAt", "created_at", "run_started_at")),
            "url": str(g("url", "html_url")) if str(g("url", "html_url")).startswith("https://") else "", "branch": str(g("headBranch", "head_branch"))}


def ci_summary(runs, branch=""):
    """The CI line for a branch: the newest run of each workflow; failed beats running beats passed.
    {state: passed|failed|running|cancelled|none, name, at, url, branch, workflows: n}."""
    latest = {}
    for r in sorted(runs, key=lambda r: r["at"], reverse=True):
        if branch and r["branch"] and r["branch"] != branch:
            continue
        latest.setdefault(r["name"], r)
    rs = list(latest.values())
    if not rs:
        return {"state": "none", "name": "", "at": 0, "url": "", "branch": branch, "workflows": 0}
    pick = lambda xs: max(xs, key=lambda r: r["at"])
    failed = [r for r in rs if r["status"] == "completed" and r["conclusion"] in FAILED]
    running = [r for r in rs if r["status"] in ("queued", "in_progress", "waiting", "requested", "pending")]
    passed = [r for r in rs if r["status"] == "completed" and r["conclusion"] in ("success", "neutral", "skipped")]
    state, r = ("failed", pick(failed)) if failed else ("running", pick(running)) if running else ("passed", pick(passed)) if passed else ("cancelled", pick(rs))
    return {"state": state, "name": r["name"], "at": r["at"], "url": r["url"], "branch": branch or r["branch"], "workflows": len(rs)}


class GhSource:
    """Through the `gh` command. repo = {host, owner, repo}."""
    how = "gh"

    def __init__(self, repo, runner=run_subprocess, which=shutil.which, timeout=20, cwd=None):
        self.repo, self.runner, self.which, self.timeout, self.cwd = repo, runner, which, timeout, cwd
        h = repo["host"]
        self.r = "%s/%s" % (repo["owner"], repo["repo"]) if h == "github.com" else "%s/%s/%s" % (h, repo["owner"], repo["repo"])

    def _gh(self, args):
        exe = self.which("gh")
        if not exe:
            raise GitHubError("the gh command is not installed (get it from cli.github.com, or use a token instead)")
        try:
            code, out, err = self.runner([exe] + args, self.timeout, self.cwd)
        except subprocess.TimeoutExpired:
            raise GitHubError("gh took longer than %d seconds" % self.timeout) from None
        except OSError as e:
            raise GitHubError("gh could not start (%s)" % e.__class__.__name__) from None
        if code != 0:
            low = (err or "").lower()
            if "auth login" in low or "not logged" in low or "authentication" in low:
                raise GitHubError("gh is not signed in: run  gh auth login  once in a terminal")
            first = next((l.strip() for l in (err or "").splitlines() if l.strip()), "exit code %s" % code)
            raise GitHubError("gh: " + first[:160])
        try:
            return json.loads(out or "null")
        except ValueError:
            raise GitHubError("gh did not answer with JSON (is it an old version?)") from None

    def whoami(self):
        d = self._gh(["api", "user"] + (["--hostname", self.repo["host"]] if self.repo["host"] != "github.com" else []))
        return str((d or {}).get("login") or "?")

    def issues(self, labels, limit):
        base = ["issue", "list", "-R", self.r, "--state", "open", "--json", "number,title,body,labels,url", "--limit", str(limit)]
        out = {}
        for group in ([[lb] for lb in labels[:MAX_LABELS]] or [[]]):      # several labels = any of them (one call each)
            for d in self._gh(base + sum((["--label", x] for x in group), [])) or []:
                it = issue_item(d)
                out[it["id"]] = it
        return list(out.values())

    def prs(self, mode, limit):
        if mode not in GH_PR_ARGS:
            return []
        d = self._gh(["pr", "list", "-R", self.r, "--state", "open", "--json", "number,title,body,labels,url", "--limit", str(limit)] + GH_PR_ARGS[mode])
        return [issue_item(x, "pr") for x in d or []]

    def runs(self, branch, limit=10):
        d = self._gh(["run", "list", "-R", self.r, "--json", "name,workflowName,status,conclusion,createdAt,updatedAt,url,headBranch", "--limit", str(limit)] + (["--branch", branch] if branch else []))
        return [run_item(x) for x in d or []]


class RestSource:
    """Through the REST API with the owner's token (github.com: api.github.com; Enterprise: https://<host>/api/v3)."""
    how = "token"

    def __init__(self, repo, token, opener=None, timeout=15):
        if not token:
            raise GitHubError("paste a GitHub token in Options > Integrations first")
        self.repo, self.token, self.opener, self.timeout = repo, token, opener, timeout
        self.api = "https://api.github.com" if repo["host"] == "github.com" else "https://%s/api/v3" % repo["host"]
        self._login = None

    def _get(self, path):
        try:
            return httpjson.call(self.api + path, {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                                 opener=self.opener, timeout=self.timeout, secrets=(self.token,))
        except httpjson.HttpError as e:
            raise GitHubError(httpjson.scrub(e, (self.token,))) from None

    def whoami(self):
        if self._login is None:
            self._login = str((self._get("/user") or {}).get("login") or "?")
        return self._login

    def issues(self, labels, limit):
        from urllib.parse import quote
        base = "/repos/%s/%s/issues?state=open&per_page=%d" % (self.repo["owner"], self.repo["repo"], min(100, limit))
        out = {}
        for lb in (labels[:MAX_LABELS] or [None]):
            for d in self._get(base + ("&labels=" + quote(lb, safe="") if lb else "")) or []:
                if isinstance(d, dict) and "pull_request" not in d:          # the issues list includes pull requests: those come from prs()
                    it = issue_item(d)
                    out[it["id"]] = it
        return list(out.values())[:limit]

    def prs(self, mode, limit):
        from urllib.parse import quote
        if mode not in REST_PR_QUERY:
            return []
        q = "repo:%s/%s is:pr is:open %s" % (self.repo["owner"], self.repo["repo"], REST_PR_QUERY[mode].format(login=self.whoami()) if mode != "all" else "")
        d = self._get("/search/issues?per_page=%d&q=%s" % (min(100, limit), quote(q.strip())))
        return [issue_item(x, "pr") for x in (d or {}).get("items", [])][:limit]

    def runs(self, branch, limit=10):
        from urllib.parse import quote
        d = self._get("/repos/%s/%s/actions/runs?per_page=%d%s" % (self.repo["owner"], self.repo["repo"], limit, "&branch=" + quote(branch, safe="") if branch else ""))
        return [run_item(x) for x in (d or {}).get("workflow_runs", [])]


class FakeSource:
    """DEV ONLY (env VANILLA_INTEGRATIONS_FAKE=<fixture.json>): answers like gh from a JSON file, for demos and screenshots.
    Fixture: {"login", "repo": "owner/name", "branch", "issues": [gh issue JSON], "prs": [...], "runs": [gh run JSON]}."""
    how = "demo"

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self.d = json.load(f)

    def whoami(self):
        return str(self.d.get("login") or "demo")

    def issues(self, labels, limit):
        rows = [d for d in self.d.get("issues", []) if not labels or set(labels) & set(_labels(d))]
        return [issue_item(d) for d in rows[:limit]]

    def prs(self, mode, limit):
        return [] if mode not in GH_PR_ARGS else [issue_item(d, "pr") for d in self.d.get("prs", [])[:limit]]

    def runs(self, branch, limit=10):
        now = time.time()
        rs = []
        for d in self.d.get("runs", [])[:limit]:
            r = run_item(d)
            if "ago_seconds" in d:          # fixture times relative to now, so the demo always reads "4 min ago"
                r["at"] = int(now - d["ago_seconds"])
            rs.append(r)
        return rs
