import os
import shutil
import stat
import sys
import tempfile
import unittest

from vanilla_agents.authority import Actor
from vanilla_agents.contracts import Principal
from vanilla_agents.storage import StorageService

CTL = Actor(Principal.CONTROLLER, "controller")
USER = Actor(Principal.USER, "alice")
VER = Actor(Principal.VERIFIER, "verifier-fixture")   # controlled FIXTURE verifier: Stage 1 has no real verifier
SYS = Actor(Principal.SYSTEM, "system")


def worker(wid):
    return Actor(Principal.WORKER, wid)


def rmtree_force(path, *_ignored):
    """Remove a temp tree even when it holds read-only files (git object files are read-only on Windows)."""
    def onexc(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=onexc)
    else:
        shutil.rmtree(path, onerror=onexc)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        self.t += 0.001
        return self.t


class Base(unittest.TestCase):
    """Each test gets a fresh temporary home and an open StorageService (it owns the controller lock)."""
    interval = 100

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="va_t_")
        self.home = os.path.join(self.tmp, "home")
        self.clock = Clock()
        self.svc = StorageService(self.home, clock=self.clock, interval=self.interval).open()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            self.svc.close()
        except Exception:
            pass
        rmtree_force(self.tmp)

    # ---- fixtures
    def project_task(self, title="t1"):
        pid = self.svc.create_project(CTL, "demo")
        tid = self.svc.create_task(CTL, {"project_id": pid, "title": title, "description": "fixture task"})
        return pid, tid

    def verified_task(self, n_criteria=1, commit="c1", env="e1"):
        """A task whose required criteria each have a current VERIFIED fixture claim and a VERIFIED qualification."""
        pid, tid = self.project_task()
        self.svc.update_task_source(CTL, tid, commit, env)
        claims = []
        for i in range(n_criteria):
            crit = self.svc.add_criterion(CTL, tid, "criterion %d" % i, True, "check%d" % i)
            ev = self.svc.put_evidence(VER, ("evidence %d" % i).encode())
            claims.append(self.svc.insert_claim(VER, self.claim(tid, crit, "check%d" % i, [ev], commit, env)))
        self.svc.set_verification(CTL, tid, "RUNNING")
        qid = self.svc.set_verification(VER, tid, "VERIFIED")
        return pid, tid, claims, qid

    @staticmethod
    def claim(tid, crit, check, evidence, commit="c1", env="e1", status="VERIFIED"):
        return {"task_id": tid, "criterion_id": crit, "check_id": check, "status": status, "expected": "pass", "observed": "pass", "evidence_refs": evidence,
                "verifier_id": "verifier-fixture", "verifier_version": "0", "source_commit": commit, "env_fingerprint": env, "repeatability_class": "repeatable"}

    def events(self, kind=None):
        q = "SELECT * FROM events" + (" WHERE kind=?" if kind else "") + " ORDER BY seq"
        return self.svc.conn.execute(q, (kind,) if kind else ()).fetchall()

    def task(self, tid):
        return self.svc.conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
