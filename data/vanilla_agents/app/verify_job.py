"""Verify now (D-220..D-226): one verification at a time in a background thread, reported through the event feed only.
Per-project setup lives in a registry file OUTSIDE git (app.json projects_path). The verifier is injectable (`verifier=`), so tests and --fake-verify never run cargo."""
import json
import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

from ..contracts import VanillaError
from ..durable.preflight import DiskSpaceLow, check_disk
from ..events.feed import Feed
from ..verify import runner
from ..verify.redact import redact
from ..verify.verifier import run_verification
from ..onboard import idea
from ..onboard.setup import slugify
from . import checks_api, route, runs, settings

MANIFEST_HELP = ("The checker's trusted folder no longer matches the fingerprint you recorded (%s). If you changed the checker code on purpose, "
                 "run: python scripts/onboard.py --regen. It rebuilds the trusted folder and records the new fingerprint. "
                 "If you did NOT change it, do not continue: something altered the trusted folder.")


class Refused(Exception):
    """A plain-language reason why Verify cannot start. The message is shown to the owner as is."""


def _git_head(repo, ref="HEAD"):
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise Refused("Git could not be run (%s)." % e)
    if r.returncode or len(r.stdout.strip()) < 40:
        raise Refused("Could not read the current version of %s (is it a git folder with at least one save?)." % repo)
    return r.stdout.strip()


def _rmtree(p):
    def fix(fn, path, _):
        os.chmod(path, stat.S_IWRITE)    # git object files are read-only on Windows
        fn(path)
    shutil.rmtree(p, onerror=fix)


def _fill(text, v):
    for k, x in v.items():
        text = text.replace("{%s}" % k, str(x))
    return text


def _norm(p):
    return os.path.normcase(os.path.realpath(str(p)))


def _name(e):
    """Run/quest name: the entry's own short name (onboarded projects, D-333); older entries keep the folder-above-the-repo name so their history still matches."""
    return e.get("name") or Path(e["repo"]).parent.name or "project"


def verify_task(e):
    """The feed task id this project's verification runs under."""
    return "verify-" + _name(e).lower().replace(" ", "-")


def ideas_root(a):
    """Folder holding every project's IDEA.md (outside git). VANILLA_IDEAS overrides app.json#ideas_root, like VANILLA_PROJECTS does for the registry."""
    return os.path.expanduser(os.environ.get("VANILLA_IDEAS") or a["ideas_root"])


def slug_of(e):
    """The project's folder name under ideas_root: the registry name, or the repo folder name for entries made before onboarding."""
    return slugify(e.get("name") or Path(e["repo"]).name)


def load_registry(path):
    try:
        reg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return reg if isinstance(reg, dict) else {}


class VerifyJob:
    def __init__(self, cfg, feed_path, settings_file, verifier=run_verification, usage=shutil.disk_usage, git_head=_git_head, clock=time.time, changes=route.git_changes):
        a = cfg["app"]
        self.a, self.sounds, self.feed, self.settings_file = a, cfg["sounds"], Feed(feed_path), settings_file
        self.verifier, self.usage, self.git_head, self.clock, self.changes = verifier, usage, git_head, clock, changes
        self.ui = cfg["strings"]["ui"]
        self.registry_path = os.path.expanduser(os.environ.get("VANILLA_PROJECTS") or a["projects_path"])
        self.runs_path = os.path.expanduser(os.environ.get("VANILLA_RUNS") or a["runs_path"])
        self.lock, self.thread = threading.Lock(), None
        self.running, self.check, self.started, self.expected = False, "", 0.0, 0.0
        self.target = None       # V-045: {quest, agent} while the checker looks at one competing proposal

    def paused(self):
        return bool(settings.load(self.settings_file, self.sounds).get("paused"))

    def set_paused(self, value):
        settings.update(self.settings_file, {"paused": bool(value)}, self.sounds)
        if value:
            runner.CANCEL.set()        # Pause also stops a check that is already running (its process tree is killed)

    def status(self):
        with self.lock:
            return {"paused": self.paused(), "running": self.running, "check": self.check,
                    "elapsed": round(self.clock() - self.started, 1) if self.running else 0, "expected": self.expected,
                    "target": dict(self.target) if self.running and self.target else None}

    def _entry(self, project):
        reg = load_registry(self.registry_path)
        hit = next((v for k, v in reg.items() if project and isinstance(v, dict) and _norm(k) == _norm(project)), None)    # D-335: strictly this project's own entry, never "the only one"
        if hit is None:
            raise Refused("This project is not set up for checking yet. Run: python scripts/onboard.py <project folder>   (or use Add project in the Esc menu; it writes %s)." % self.registry_path)
        return hit

    def entry(self, project):
        """The project's registry entry, or None when it is not set up (read-only; used by the spellbook)."""
        try:
            return self._entry(project)
        except Refused:
            return None

    def start(self, project, only=None, proposal=None):
        """Begin a run, or raise Refused with a plain message. Returns the run id. `only` = one check id (a spell click): just that check, no holdouts.
        V-045 `proposal` = {quest, agent, branch, title}: check the last commit of that competing branch (a local branch of the project's own
        repository; worktrees share it) instead of the base. Reported as one fix_checked line; the project's own quest and run log are untouched."""
        with self.lock:
            if self.running:
                raise Refused("A check is already running. Please wait until it finishes.")
            if self.paused():
                raise Refused("Paused. The checker is on hold; press Resume first.")
            e = self._entry(project)
            if not e.get("criteria"):
                raise Refused("No automatic checks were found for this project (agent-only mode), so there is nothing to verify yet.")
            work_root = self.a["verify_work_root"]
            try:
                check_disk(work_root, self.a["verify_min_free_bytes"], usage=self.usage)
            except DiskSpaceLow:
                raise Refused("Not enough free space on the disk for a check (it needs about %.0f GB free). Free some space and try again." % (self.a["verify_min_free_bytes"] / 2 ** 30))
            if proposal is not None:
                from .contests import valid_ref
                if not valid_ref(proposal.get("branch")) or proposal["branch"] == "HEAD":
                    raise Refused("That branch name is not allowed.")
                base = self.git_head(e["repo"], "refs/heads/" + proposal["branch"])
            else:
                base = self.git_head(e["repo"], e.get("default_base_ref", "HEAD"))
            pick = self._route(e, base) if proposal is None else {"route": "full", "reasons": [], "criteria": list(e["criteria"]), "holdouts": True, "why": ""}
            if only is not None:
                if only not in e["criteria"]:
                    raise Refused("That check is not one of this project's checks.")
                pick["criteria"], pick["holdouts"] = [only], False
            check = (pick["criteria"] or e["criteria"])[0]
            runner.CANCEL.clear()
            self.running, self.check, self.started = True, check, self.clock()
            self.target = {"quest": proposal["quest"], "agent": proposal["agent"]} if proposal else None
            self.expected = float(self.a["verify_check_seconds"].get(check, self.a["verify_default_seconds"]))
            run_id = "ui-%d" % int(self.clock())
            self.thread = threading.Thread(target=self._run, args=(e, base, run_id, _name(e), pick, proposal), daemon=True)
            self.thread.start()
            return run_id

    def _route(self, e, base):
        """D-300..D-309: fast or full, from the files changed since the last good check of this project. Any doubt means full."""
        name = _name(e)
        last = next((r.get("commit") for r in runs.read(self.runs_path, 200) if r.get("verdict") == "VERIFIED" and r.get("title") == "Verify " + name), None)
        prot = []
        try:
            prot = [p for p in json.loads((Path(e["trusted_dir"]) / "protected.json").read_text(encoding="utf-8")) if isinstance(p, str)]
        except (OSError, ValueError, TypeError):
            pass
        oos = idea.out_of_scope(ideas_root(self.a), slug_of(e))       # D-346: the Idea's out-of-scope paths force the full route
        pick = route.classify(self.changes(e["repo"], last, base) if last else None, route.merge_rules(self.a["route"], e.get("route")), prot, oos)
        fast = [c for c in e.get("fast_criteria", []) if c in e["criteria"]]
        pick["criteria"] = fast if pick["route"] == "fast" and fast else list(e["criteria"])
        pick["why"] = " ".join(_fill(self.ui["route.why_" + k], v) for k, v in pick["reasons"])
        return pick

    def _sent_back(self, bundle, why):
        """D-320..D-329: [(check name, evidence)] for every check that failed. Evidence is the verifier's own one-line finding (redacted again, capped);
        a holdout (hidden-N) gets a fixed sentence and never its observed text, name or output."""
        out = []
        for r in bundle["checks"]:
            if r["status"] not in ("FAILED", "FLAKY"):
                continue
            hidden = r["check_id"].startswith("hidden-")
            out.append((checks_api.label(self.ui, r["check_id"]), self.ui["sentback.hidden"] if hidden else redact(str(r.get("observed") or r["status"]))[:300]))
        if not out:      # failed before any check ran (patch refused, protected file touched): the finding is the evidence
            blocking = [f for f in bundle.get("diff_findings", []) if f.get("blocking")]
            out.append((self.ui["sentback.change"], redact(str(blocking[0].get("detail")) if blocking else why)[:300]))
        return out

    def _run(self, e, base, run_id, name, pick, proposal=None):
        task, title = verify_task(e), "Verify " + name
        work = os.path.join(self.a["verify_work_root"], run_id)
        emit = (lambda t, **d: None) if proposal else (lambda t, **d: self.feed.append(t, task=task, data=d))
        outcome, checks, why = "BLOCKED", [], ""
        try:
            emit("task_ready", title=title)
            emit("route_chosen", route=pick["route"], why=pick["why"])
            for c in pick["criteria"]:
                emit("check_started", check=c)
            req = {"run_id": run_id, "task_id": task, "repo": e["repo"], "base_commit": base, "source_commit": base, "patch_path": None,
                   "trusted_dir": e["trusted_dir"], "work_root": self.a["verify_work_root"], "expected_manifest_hash": e["expected_manifest_hash"],
                   "criteria": [{"criterion_id": "c%d" % i, "check_id": c, "required": True} for i, c in enumerate(pick["criteria"], 1)], "holdouts": pick["holdouts"]}
            bundle, _ = self.verifier(req)
            for r in bundle["checks"]:
                emit("check_finished", check=r["check_id"], verdict=r["status"])
            outcome, checks = bundle["outcome"], [{"check_id": r["check_id"], "status": r["status"], "seconds": round(sum(a.get("duration", 0) for a in r.get("attempts", [])), 1)} for r in bundle["checks"]]
            why = "; ".join(n["reason"] for n in bundle.get("not_verified", []))[:300] or outcome
            if outcome == "VERIFIED":
                emit("task_verified", title=title)
            elif outcome in ("FAILED", "FLAKY"):
                for check, evidence in self._sent_back(bundle, why):
                    emit("sent_back", check=check, evidence=evidence)
                emit("task_failed", title=title, reason=why)
            else:
                emit("task_blocked", title=title, reason=why)
        except VanillaError as ex:
            msg = MANIFEST_HELP % "manifest" if getattr(ex, "code", "") == "manifest_mismatch" else str(ex)
            why = msg[:300]
            emit("task_blocked", title=title, reason=msg[:600])
        except Exception as ex:   # a crash must end the run visibly, never leave Verify stuck on "running"
            why = "The check stopped unexpectedly: %s" % str(ex)[:300]
            emit("task_blocked", title=title, reason=why)
        finally:
            if proposal:       # V-045: one line for the competing fix; the run log keeps only the project's own checks (zones, auto-detect read it)
                try:
                    self.feed.append("fix_checked", task=proposal["quest"], agent=proposal["agent"],
                                     data={"title": str(proposal.get("title") or proposal["quest"])[:200], "verdict": outcome, "branch": proposal["branch"],
                                           **({"reason": redact(str(why))[:300]} if why and why != outcome else {})})
                except Exception:
                    pass
            try:   # the durable run log must never break a run (D-240)
                if not proposal:
                    runs.append(self.runs_path, {"run_id": run_id, "ts": self.clock(), "commit": base, "verdict": outcome, "checks": checks,
                                                 "seconds": round(self.clock() - self.started, 1), "title": title, "reason": why}, self.a["runs_max_bytes"])
            except OSError:
                pass
            try:
                _rmtree(work)
            except OSError:   # FileNotFoundError (nothing was made) included; a leftover is reported by LIMITATIONS, never blocks the next run
                pass
            with self.lock:
                self.running, self.target = False, None
