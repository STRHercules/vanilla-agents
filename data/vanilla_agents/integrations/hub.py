"""The integration hub of one window: refreshes the switched-on sources (manually and every N minutes on a daemon thread), keeps a
status per source for the window (who is signed in, how many items, the last error), keeps the CI line for the open project's branch,
and imports CSV files. It never creates a quest (the server's POST /api/bounty/quest does, on the owner's click).
DEV ONLY: env VANILLA_INTEGRATIONS_FAKE=<fixture.json> makes GitHub answer from that file (see github.FakeSource); for demos/screenshots."""
import os
import threading
import time

from . import csvimport, github, gitremote, isettings, jira, linear
from .board import Board, board_path

FAKE_ENV = "VANILLA_INTEGRATIONS_FAKE"
DEFAULTS = {"timeout_seconds": 15, "max_items": 100, "max_file_bytes": 2_000_000, "refresh_minutes": 10, "ci_seconds": 120, "excerpt_chars": 240}


def _blank():
    return {"ok": None, "who": "", "how": "", "count": 0, "at": 0, "error": ""}


class Hub:
    def __init__(self, settings_file, project_of, opts=None, env=os.environ, runner=github.run_subprocess, which=None, opener=None, clock=time.time):
        self.settings_file, self.project_of, self.env = settings_file, project_of, env
        self.o = {**DEFAULTS, **(opts or {})}
        self.runner, self.which, self.opener, self.clock = runner, which, opener, clock
        self.work_dir = os.path.dirname(os.path.abspath(settings_file))
        self.status = {k: _blank() for k in ("github", "ci", "linear", "jira", "file")}
        self.version, self.ci, self.last = 0, None, {}
        self._boards, self.lock, self.run_lock = {}, threading.Lock(), threading.Lock()

    # ---- helpers ----
    def settings(self):
        return isettings.load(self.settings_file, self.o["refresh_minutes"])

    def _key(self):
        return str(self.project_of() or "")

    def board(self):
        p = board_path(self.work_dir, self.project_of())
        with self.lock:
            if str(p) not in self._boards:
                self._boards[str(p)] = Board(p, self.clock)
            return self._boards[str(p)]

    def _bump(self):
        self.version += 1

    def _set(self, name, **kw):
        self.status[name] = {**_blank(), **kw, "at": int(self.clock())}
        self._bump()

    def _fake(self):
        f = self.env.get(FAKE_ENV, "")
        return github.FakeSource(f) if f else None

    def repo(self, s):
        """The GitHub repository of the open project, or (dev-only fake data) the fixture's."""
        r = gitremote.find(self.project_of(), s["github"]["host"]) if self.project_of() else None
        fk = self._fake()
        if r is None and fk is not None:
            owner, _, name = str(fk.d.get("repo") or "demo/demo").partition("/")
            r = {"host": "github.com", "owner": owner, "repo": name or "demo", "remote": "demo", "slug": "%s/%s" % (owner, name), "web": "https://github.com/%s/%s" % (owner, name)}
        return r

    def source(self, s, repo):
        fk = self._fake()
        if fk is not None:
            return fk
        g = s["github"]
        if g["mode"] == "token":
            if repo["host"] not in ("github.com", g["host"]):      # V-048: the token goes to github.com or the Enterprise host named in Options only, never to a remote's look-alike host
                raise github.GitHubError("this project's GitHub remote is on %s: a token is only sent to github.com or the Enterprise host you name in Options" % repo["host"])
            return github.RestSource(repo, g["token"], self.opener, self.o["timeout_seconds"])
        kw = {"which": self.which} if self.which else {}
        return github.GhSource(repo, self.runner, timeout=max(5, self.o["timeout_seconds"] + 5), cwd=self.project_of() or None, **kw)

    # ---- GitHub ----
    def refresh_github(self, s=None, ci=True):
        s = s or self.settings()
        if not s["github"]["enabled"]:
            raise ValueError("switch GitHub import on first (Options > Integrations)")
        with self.run_lock:
            self.last["github", self._key()] = self.clock()
            repo = self.repo(s)
            if repo is None:
                self._set("github", ok=False, error="this project has no GitHub remote (git remote origin pointing at GitHub)")
                return self.status["github"]
            try:
                src = self.source(s, repo)
                who = src.whoami()
                g, n, b = s["github"], self.o["max_items"], self.board()
                b.replace("github", "issues", src.issues(g["labels"], n) if g["issues"] else [])
                b.replace("github", "prs", src.prs(g["prs"], n) if g["prs"] != "off" else [])
                self._set("github", ok=True, who=who, how=src.how, count=b.counts()["github"], repo=repo["slug"])
            except (github.GitHubError, ValueError, OSError) as e:
                self._set("github", ok=False, error=str(e), repo=repo["slug"])
        if ci:
            self.refresh_ci(s)
        return self.status["github"]

    def ci_possible(self, s):
        return bool(s["github"]["enabled"]) and (self._fake() is not None or gitremote.has_actions(self.project_of()))

    def refresh_ci(self, s=None):
        s = s or self.settings()
        self.last["ci", self._key()] = self.clock()
        if not self.ci_possible(s):
            if self.ci is not None:
                self.ci = None
                self._bump()
            return None
        repo = self.repo(s)
        if repo is None:
            return None
        fk = self._fake()
        branch = gitremote.branch(self.project_of()) or (str(fk.d.get("branch") or "") if fk else "")
        try:
            runs = self.source(s, repo).runs(branch, 10)
            ci = {**github.ci_summary(runs, branch), "project": self._key()}
            self.status["ci"] = {**_blank(), "ok": True, "at": int(self.clock())}
        except (github.GitHubError, ValueError, OSError) as e:
            ci = {"state": "unknown", "name": "", "at": 0, "url": "", "branch": branch, "workflows": 0, "error": str(e), "project": self._key()}
            self.status["ci"] = {**_blank(), "ok": False, "error": str(e), "at": int(self.clock())}
        if ci != self.ci:
            self.ci = ci
            self._bump()
        return ci

    # ---- Linear / Jira (API mode) ----
    def refresh_linear(self, s=None):
        s = s or self.settings()
        ln = s["linear"]
        if not ln["enabled"]:
            raise ValueError("switch Linear import on first")
        self.last["linear", self._key()] = self.clock()
        try:
            who, items = linear.issues(ln["api_key"], ln["team"], ln["mine"], self.o["max_items"], self.opener, self.o["timeout_seconds"], self.o["excerpt_chars"])
            self.board().replace("linear", "api", items)
            self._set("linear", ok=True, who=who, how="api", count=self.board().counts()["linear"])
        except linear.LinearError as e:
            self._set("linear", ok=False, error=str(e))
        return self.status["linear"]

    def refresh_jira(self, s=None):
        s = s or self.settings()
        j = s["jira"]
        if not j["enabled"]:
            raise ValueError("switch Jira import on first")
        self.last["jira", self._key()] = self.clock()
        try:
            who = jira.whoami(j["site"], j["email"], j["token"], self.opener, self.o["timeout_seconds"])
            items = jira.issues(j["site"], j["email"], j["token"], j["jql"], self.o["max_items"], self.opener, self.o["timeout_seconds"], self.o["excerpt_chars"])
            self.board().replace("jira", "api", items)
            self._set("jira", ok=True, who=who, how="api", count=self.board().counts()["jira"])
        except jira.JiraError as e:
            self._set("jira", ok=False, error=str(e))
        return self.status["jira"]

    def refresh(self, which):
        return {"github": self.refresh_github, "linear": self.refresh_linear, "jira": self.refresh_jira}[which]()

    # ---- Test (with what is typed in the window, before Save) ----
    def test(self, service, body=None):
        """{ok, who, how, error} - signs in only, imports nothing."""
        s = isettings.merged(isettings.load(self.settings_file, self.o["refresh_minutes"]), body, self.o["refresh_minutes"])
        try:
            if service == "github":
                repo = self.repo(s)
                if repo is None:
                    return {"ok": False, "who": "", "how": "", "error": "this project has no GitHub remote (git remote origin pointing at GitHub)"}
                src = self.source(s, repo)
                return {"ok": True, "who": src.whoami(), "how": src.how, "error": "", "repo": repo["slug"]}
            if service == "linear":
                return {"ok": True, "who": linear.whoami(s["linear"]["api_key"], self.opener, self.o["timeout_seconds"]), "how": "api", "error": ""}
            if service == "jira":
                j = s["jira"]
                return {"ok": True, "who": jira.whoami(j["site"], j["email"], j["token"], self.opener, self.o["timeout_seconds"]), "how": "api", "error": ""}
        except (github.GitHubError, linear.LinearError, jira.JiraError, ValueError, OSError) as e:
            return {"ok": False, "who": "", "how": "", "error": str(e)}
        raise ValueError("service must be github, linear or jira")

    # ---- Import from file ----
    def import_file(self, text, kind="auto"):
        if kind not in ("auto", "jira", "linear"):
            raise ValueError("kind must be auto, jira or linear")
        site = self.settings()["jira"]["site"]
        try:
            kind, items, skipped = csvimport.parse(text, kind, self.o["max_file_bytes"], self.o["max_items"] * 2, site, self.o["excerpt_chars"])
        except csvimport.CsvImportError as e:
            self._set("file", ok=False, error=str(e))
            raise ValueError(str(e)) from None
        self.board().replace(kind, "file", items)       # a new export of the same tool replaces the last one
        self._set("file", ok=True, how=kind, count=len(items), skipped=skipped)
        return {"kind": kind, "count": len(items), "skipped": skipped}

    # ---- the window ----
    def view(self, tasks=()):
        """For the state snapshot: the bounties still to decide on, the CI line (or None) and the status lines. No settings, no secrets."""
        s = self.settings()
        ci = self.ci if self.ci and self.ci.get("project") == self._key() and self.ci_possible(s) else None
        on = {"github": s["github"]["enabled"], "linear": s["linear"]["enabled"], "jira": s["jira"]["enabled"]}
        return {"bounties": self.board().visible(tasks, self.o["max_items"] * 2), "ci": ({k: v for k, v in ci.items() if k != "project"} if ci else None),
                "integrations": {"on": on, "status": {k: dict(v) for k, v in self.status.items()}}}

    def tick(self):
        """One background pass: refresh whatever is switched on and due."""
        try:
            s = self.settings()
            now, key, every = self.clock(), self._key(), s["refresh_minutes"] * 60
            if s["github"]["enabled"] and now - self.last.get(("github", key), -1e12) >= every:
                self.refresh_github(s)
            elif self.ci_possible(s) and now - self.last.get(("ci", key), -1e12) >= self.o["ci_seconds"]:
                self.refresh_ci(s)
            if s["linear"]["enabled"] and now - self.last.get(("linear", key), -1e12) >= every:
                self.refresh_linear(s)
            if s["jira"]["enabled"] and now - self.last.get(("jira", key), -1e12) >= every:
                self.refresh_jira(s)
        except Exception:
            pass          # an integration must never take the window down

    def start(self, stop, every=20.0):
        def loop():
            stop.wait(3)
            while not stop.is_set():
                self.tick()
                stop.wait(every)
        threading.Thread(target=loop, daemon=True, name="integrations").start()
