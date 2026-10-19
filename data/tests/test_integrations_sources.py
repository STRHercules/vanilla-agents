"""V-044 integration hub, the readers: GitHub remote parsing from .git/config (https / ssh / Enterprise), the gh command through a fake
runner (missing, not signed in, timeout, argument lists), GitHub REST / Linear GraphQL / Jira REST through a fake opener (the secret is
in the header and nowhere else), the CI summary, and Jira / Linear CSV exports. Nothing here touches the network or runs gh."""
import atexit
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error

from vanilla_agents.integrations import csvimport, github, gitremote, httpjson, jira, linear

TOKEN = "ghp_SECRETsecretSECRET1234"


class Runner:
    """A fake gh: answers by the sub-command; records every argument list."""

    def __init__(self, answers=None, exc=None):
        self.calls, self.answers, self.exc = [], answers or {}, exc

    def __call__(self, args, timeout, cwd=None):
        self.calls.append(list(args))
        if self.exc:
            raise self.exc
        key = " ".join(args[1:3])
        a = self.answers.get(key, (0, "[]", ""))
        return a


class Resp:
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")

    def read(self, n=-1):
        return self.data

    def close(self):
        pass


class Opener:
    def __init__(self, answers):
        self.reqs, self.answers = [], list(answers)

    def __call__(self, req, timeout):
        self.reqs.append(req)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return Resp(a)


def which(name):
    return "/usr/bin/gh"


def gitdir(remotes, head="ref: refs/heads/feat-x"):
    d = tempfile.mkdtemp(prefix="va_int_")
    atexit.register(shutil.rmtree, d, True)
    os.makedirs(os.path.join(d, ".git"))
    with open(os.path.join(d, ".git", "config"), "w") as f:
        f.write("[core]\n\tbare = false\n" + "".join('[remote "%s"]\n\turl = %s\n\tfetch = +refs/heads/*:refs/remotes/%s/*\n' % (n, u, n) for n, u in remotes))
    with open(os.path.join(d, ".git", "HEAD"), "w") as f:
        f.write(head + "\n")
    return d


class Remotes(unittest.TestCase):
    def test_forms(self):
        want = {"host": "github.com", "owner": "octo", "repo": "inn"}
        for u in ("https://github.com/octo/inn", "https://github.com/octo/inn.git", "https://github.com/octo/inn/", "http://github.com/octo/inn",
                  "https://x-access-token:%s@github.com/octo/inn.git" % TOKEN, "ssh://git@github.com/octo/inn.git", "ssh://git@ssh.github.com:443/octo/inn.git",
                  "git@github.com:octo/inn.git", "github.com:octo/inn", "git://github.com/octo/inn.git"):
            self.assertEqual(gitremote.parse_remote(u), want, u)
        self.assertNotIn(TOKEN, json.dumps(gitremote.parse_remote("https://u:%s@github.com/octo/inn" % TOKEN)))
        self.assertEqual(gitremote.parse_remote("git@github.acme.io:team/app.git"), {"host": "github.acme.io", "owner": "team", "repo": "app"})
        for bad in ("", "nonsense", "https://github.com/octo", "https://github.com/a/b/c", "/home/me/repo", "https://github.com/../x"):
            self.assertIsNone(gitremote.parse_remote(bad), bad)

    def test_find_prefers_origin_and_skips_other_hosts(self):
        d = gitdir([("origin", "git@gitlab.com:octo/inn.git"), ("upstream", "https://github.com/octo/inn.git")])
        r = gitremote.find(d)
        self.assertEqual((r["slug"], r["remote"], r["web"]), ("octo/inn", "upstream", "https://github.com/octo/inn"))
        self.assertEqual(gitremote.branch(d), "feat-x")
        self.assertIsNone(gitremote.find(gitdir([("origin", "https://bitbucket.org/a/b")])))

    def test_enterprise_host(self):
        d = gitdir([("origin", "https://git.corp.example/team/app.git")])
        self.assertIsNone(gitremote.find(d))
        self.assertEqual(gitremote.find(d, "git.corp.example")["host"], "git.corp.example")
        self.assertEqual(gitremote.find(gitdir([("origin", "https://github.corp.example/team/app")]))["host"], "github.corp.example")

    def test_linked_worktree_reads_the_main_config(self):
        main = gitdir([("origin", "git@github.com:octo/inn.git")])
        wt_git = os.path.join(main, ".git", "worktrees", "wt1")
        os.makedirs(wt_git)
        with open(os.path.join(wt_git, "HEAD"), "w") as f:
            f.write("ref: refs/heads/feat-wt\n")
        with open(os.path.join(wt_git, "commondir"), "w") as f:
            f.write("../..\n")
        wt = tempfile.mkdtemp(prefix="va_wt_")
        self.addCleanup(shutil.rmtree, wt, True)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % wt_git)
        self.assertEqual(gitremote.find(wt)["slug"], "octo/inn")
        self.assertEqual(gitremote.branch(wt), "feat-wt")

    def test_actions_detection(self):
        d = gitdir([("origin", "https://github.com/o/r")])
        self.assertFalse(gitremote.has_actions(d))
        os.makedirs(os.path.join(d, ".github", "workflows"))
        open(os.path.join(d, ".github", "workflows", "ci.yml"), "w").close()
        self.assertTrue(gitremote.has_actions(d))


REPO = {"host": "github.com", "owner": "octo", "repo": "inn"}
ISSUES = json.dumps([{"number": 7, "title": "Fix  the\nlogin", "body": "It breaks <!-- template help --> on Mondays", "labels": [{"name": "bug"}], "url": "https://github.com/octo/inn/issues/7"}])


class Gh(unittest.TestCase):
    def test_missing_not_signed_in_timeout(self):
        with self.assertRaisesRegex(github.GitHubError, "not installed"):
            github.GhSource(REPO, Runner(), which=lambda n: None).whoami()
        r = Runner({"api user": (1, "", "To get started with GitHub CLI, please run:  gh auth login\n")})
        with self.assertRaisesRegex(github.GitHubError, "not signed in"):
            github.GhSource(REPO, r, which=which).whoami()
        with self.assertRaisesRegex(github.GitHubError, "longer than"):
            github.GhSource(REPO, Runner(exc=subprocess.TimeoutExpired("gh", 5)), which=which, timeout=5).issues([], 10)
        with self.assertRaisesRegex(github.GitHubError, "JSON"):
            github.GhSource(REPO, Runner({"issue list": (0, "not json", "")}), which=which).issues([], 10)

    def test_issues_prs_runs_argument_lists(self):
        r = Runner({"api user": (0, '{"login": "octo-owner"}', ""), "issue list": (0, ISSUES, ""),
                    "pr list": (0, json.dumps([{"number": 9, "title": "Speed up", "body": "", "url": "https://github.com/octo/inn/pull/9"}]), ""),
                    "run list": (0, json.dumps([{"workflowName": "build", "status": "completed", "conclusion": "failure", "updatedAt": "2026-10-19T08:00:00Z", "url": "https://github.com/octo/inn/actions/runs/1", "headBranch": "main"}]), "")})
        g = github.GhSource(REPO, r, which=which)
        self.assertEqual(g.whoami(), "octo-owner")
        (it,) = g.issues(["bug", "ui"], 30)          # two labels = two calls, merged by link
        self.assertEqual(it, {"id": "https://github.com/octo/inn/issues/7", "source": "github", "kind": "issue", "key": "#7", "title": "Fix the login",
                              "excerpt": "It breaks on Mondays", "url": "https://github.com/octo/inn/issues/7", "labels": ["bug"]})
        lists = [c for c in r.calls if c[1:3] == ["issue", "list"]]
        self.assertEqual([c[c.index("--label") + 1] for c in lists], ["bug", "ui"])
        self.assertIn("-R", lists[0])
        self.assertEqual(lists[0][lists[0].index("-R") + 1], "octo/inn")
        self.assertEqual(g.prs("review_requested", 5)[0]["kind"], "pr")
        self.assertIn("review-requested:@me", r.calls[-1])
        self.assertEqual(g.prs("off", 5), [])
        runs = g.runs("main")
        self.assertIn("--branch", r.calls[-1])
        self.assertEqual(github.ci_summary(runs, "main")["state"], "failed")
        self.assertTrue(all(isinstance(c, list) and c[0] == "/usr/bin/gh" for c in r.calls))

    def test_enterprise_repo_flag(self):
        r = Runner({"api user": (0, '{"login": "x"}', "")})
        g = github.GhSource({"host": "github.acme.io", "owner": "t", "repo": "a"}, r, which=which)
        g.issues([], 5)
        self.assertIn("github.acme.io/t/a", r.calls[-1])
        g.whoami()
        self.assertEqual(r.calls[-1][-2:], ["--hostname", "github.acme.io"])


def run(name, status, concl="", at=0, branch="main"):
    return {"name": name, "status": status, "conclusion": concl, "at": at, "url": "", "branch": branch}


class Ci(unittest.TestCase):
    def test_summary(self):
        self.assertEqual(github.ci_summary([], "main")["state"], "none")
        ok = [run("build", "completed", "success", 100), run("build", "completed", "failure", 50), run("lint", "completed", "success", 90)]
        s = github.ci_summary(ok, "main")
        self.assertEqual((s["state"], s["name"], s["at"], s["workflows"]), ("passed", "build", 100, 2))       # the newest build run counts
        self.assertEqual(github.ci_summary(ok + [run("test", "in_progress", "", 120)], "main")["state"], "running")
        self.assertEqual(github.ci_summary(ok + [run("test", "in_progress", "", 120), run("lint", "completed", "timed_out", 130)], "main")["name"], "lint")
        self.assertEqual(github.ci_summary([run("build", "completed", "failure", 9, "other")], "main")["state"], "none")
        self.assertEqual(github.run_item({"name": "b", "status": "completed", "conclusion": "success", "updated_at": "2026-10-19T08:00:00Z", "html_url": "javascript:x"})["url"], "")


class Rest(unittest.TestCase):
    def test_token_only_in_the_header(self):
        o = Opener([{"login": "octo-owner"}, [{"number": 3, "title": "Real issue", "html_url": "https://github.com/octo/inn/issues/3"},
                                              {"number": 4, "title": "A PR", "html_url": "https://github.com/octo/inn/pull/4", "pull_request": {}}],
                    {"items": [{"number": 4, "title": "A PR", "html_url": "https://github.com/octo/inn/pull/4"}]}, {"workflow_runs": []}])
        g = github.RestSource(REPO, TOKEN, o)
        self.assertEqual(g.whoami(), "octo-owner")
        self.assertEqual([i["key"] for i in g.issues([], 10)], ["#3"])           # pull requests are not issues
        self.assertEqual(g.prs("assigned", 10)[0]["kind"], "pr")
        g.runs("feat/x")
        for req in o.reqs:
            self.assertTrue(req.full_url.startswith("https://api.github.com/"))
            self.assertEqual(req.get_header("Authorization"), "Bearer " + TOKEN)
            self.assertNotIn(TOKEN, req.full_url)
        self.assertIn("assignee%3Aocto-owner", o.reqs[2].full_url)
        self.assertIn("branch=feat%2Fx", o.reqs[3].full_url)
        self.assertTrue(github.RestSource({"host": "github.acme.io", "owner": "t", "repo": "a"}, TOKEN, o).api.endswith("github.acme.io/api/v3"))

    def test_errors_never_carry_the_token(self):
        for exc in (urllib.error.HTTPError("https://api.github.com/user", 401, "Bad credentials " + TOKEN, {}, io.BytesIO(b"")),
                    urllib.error.URLError("proxy refused Authorization: Bearer " + TOKEN), OSError("boom")):
            with self.assertRaises(github.GitHubError) as cm:
                github.RestSource(REPO, TOKEN, Opener([exc])).whoami()
            self.assertNotIn(TOKEN, str(cm.exception))
        with self.assertRaisesRegex(github.GitHubError, "refused"):
            github.RestSource(REPO, TOKEN, Opener([urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b""))])).whoami()
        with self.assertRaisesRegex(github.GitHubError, "token"):
            github.RestSource(REPO, "", Opener([]))
        with self.assertRaises(httpjson.HttpError):
            httpjson.call("http://api.github.com/user", opener=Opener([{}]))         # https only


class LinearJira(unittest.TestCase):
    def test_linear(self):
        o = Opener([{"data": {"viewer": {"name": "Ada"}, "issues": {"nodes": [{"identifier": "ENG-4", "title": "Cache the menu", "description": "Slow on **Mondays**",
                                                                                 "url": "https://linear.app/inn/issue/ENG-4", "labels": {"nodes": [{"name": "perf"}]}}]}}}])
        who, items = linear.issues("lin_api_SECRET12345678", team="ENG", mine=True, opener=o)
        self.assertEqual((who, items[0]["key"], items[0]["url"]), ("Ada", "ENG-4", "https://linear.app/inn/issue/ENG-4"))
        req = o.reqs[0]
        self.assertEqual((req.full_url, req.get_header("Authorization")), (linear.URL, "lin_api_SECRET12345678"))
        body = json.loads(req.data)
        self.assertEqual(body["variables"]["filter"]["team"], {"key": {"eq": "ENG"}})
        with self.assertRaisesRegex(linear.LinearError, "Authentication"):
            linear.whoami("lin_api_SECRET12345678", Opener([{"errors": [{"message": "Authentication required"}]}]))
        with self.assertRaisesRegex(linear.LinearError, "API key"):
            linear.whoami("", Opener([]))

    def test_jira(self):
        o = Opener([{"issues": [{"key": "INN-12", "fields": {"summary": "Fix the ale tap", "labels": ["bar"],
                                                              "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "It drips."}]}]}}}]}])
        (it,) = jira.issues("https://inn.atlassian.net/", "me@inn.test", "ATATT-secret-123456", opener=o)
        self.assertEqual((it["key"], it["url"], it["excerpt"]), ("INN-12", "https://inn.atlassian.net/browse/INN-12", "It drips."))
        req = o.reqs[0]
        self.assertTrue(req.full_url.startswith("https://inn.atlassian.net/rest/api/3/search/jql?"))
        self.assertTrue(req.get_header("Authorization").startswith("Basic "))
        self.assertNotIn("ATATT", req.full_url)
        for bad in ("http://inn.atlassian.net", "https://inn.atlassian.net/wiki", "https://me:pw@inn.atlassian.net", "inn"):
            with self.assertRaises(jira.JiraError):
                jira.check_site(bad)
        with self.assertRaises(jira.JiraError) as cm:
            jira.whoami("https://inn.atlassian.net", "me@inn.test", "ATATT-secret-123456", Opener([urllib.error.URLError("ATATT-secret-123456")]))
        self.assertNotIn("ATATT-secret", str(cm.exception))


JIRA_CSV = ('﻿Summary,Issue key,Issue id,Issue Type,Status,Priority,Description,Labels,Labels,Status Category\r\n'
            'Fix the ale tap,INN-12,10012,Bug,To Do,High,"It drips.\r\nEvery night, after ""last call"".",bar,urgent,To Do\r\n'
            'Old thing,INN-3,10003,Task,Done,Low,,,,Done\r\n'
            'Paint the sign,INN-14,10014,Task,In Progress,Low,"Needs gold, not yellow",,,In Progress\r\n')
LINEAR_CSV = ('ID,Team,Title,Description,Status,Estimate,Priority,Project ID,Project,Creator,Assignee,Labels,Cycle Number,Created,Updated,Started,Completed,Canceled\n'
              'ENG-4,Engineering,Cache the menu,"Slow on\nMondays",In Progress,2,High,,Menu,Ada,Ada,"perf, backend",3,2026-10-01,2026-10-02,2026-10-02,,\n'
              'ENG-2,Engineering,Shipped already,,Done,1,Low,,,Ada,,,,2026-09-01,2026-09-02,,2026-09-03,\n'
              'ENG-5,Engineering,Dropped,,Canceled,1,Low,,,Ada,,,,2026-09-01,2026-09-02,,,2026-09-03\n')


class Csv(unittest.TestCase):
    def test_jira_export(self):
        kind, items, skipped = csvimport.parse(JIRA_CSV, jira_site="https://inn.atlassian.net")
        self.assertEqual((kind, skipped, [i["key"] for i in items]), ("jira", 1, ["INN-12", "INN-14"]))
        self.assertEqual(items[0]["excerpt"], 'It drips. Every night, after "last call".')
        self.assertEqual(items[0]["labels"], ["bar", "urgent"])
        self.assertEqual(items[0]["url"], "https://inn.atlassian.net/browse/INN-12")
        self.assertEqual(csvimport.parse(JIRA_CSV)[1][0]["id"], "jira:INN-12")          # no site: no link, keyed by the issue key

    def test_linear_export(self):
        kind, items, skipped = csvimport.parse(LINEAR_CSV, "linear")
        self.assertEqual((kind, skipped, len(items)), ("linear", 2, 1))
        self.assertEqual((items[0]["key"], items[0]["excerpt"], items[0]["labels"]), ("ENG-4", "Slow on Mondays", ["perf", "backend"]))
        with_url = LINEAR_CSV.replace("Canceled\n", "Canceled,URL\n", 1).replace("2026-10-02,,\n", "2026-10-02,,,https://linear.app/inn/issue/ENG-4\n", 1)
        self.assertEqual(csvimport.parse(with_url)[1][0]["url"], "https://linear.app/inn/issue/ENG-4")

    def test_semicolons_wrong_kind_oversize_and_empty(self):
        semi = "Summary;Issue key;Status\nFix it;A-1;Open\n"
        self.assertEqual(csvimport.parse(semi)[1][0]["key"], "A-1")
        with self.assertRaisesRegex(csvimport.CsvImportError, "Linear"):
            csvimport.parse(JIRA_CSV, "linear")
        with self.assertRaisesRegex(csvimport.CsvImportError, "too big"):
            csvimport.parse("Summary,Issue key\n" + "x,A-1\n" * 1000, max_bytes=500)
        for bad in ("", "a,b\n1,2\n"):
            with self.assertRaises(csvimport.CsvImportError):
                csvimport.parse(bad)
        self.assertEqual(len(csvimport.parse("Summary,Issue key\n" + "".join("t%d,A-%d\n" % (i, i) for i in range(50)), max_rows=10)[1]), 10)
        bad_link = "Summary,Issue key,URL\nx,A-1,javascript:alert(1)\n"
        self.assertEqual(csvimport.parse(bad_link)[1][0]["url"], "")


if __name__ == "__main__":
    unittest.main()
