"""Audit chain, checkpoints, evidence staging, migrations, single-controller lock, crash recovery, CLI and safety."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from vanilla_agents import audit, cli
from vanilla_agents.contracts import ControllerBusy, EvidenceConflict, EvidenceMissing, EvidenceTampered, VanillaError
from vanilla_agents.migrations import MIGRATIONS
from vanilla_agents.storage import StorageService, connect_ro, layout, logical_dump
from tests.helpers import CTL, VER, Base, Clock, rmtree_force

ROOT = str(Path(__file__).resolve().parent.parent)


def run_py(code, *args, **kw):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.Popen([sys.executable, "-c", code, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, **kw)


def tamper_connection(svc):
    """An 'attacker' connection that first removes the append-only triggers (same-user access is assumed in Stage 1)."""
    svc.conn.close()
    c = sqlite3.connect(str(svc.paths["db"]), isolation_level=None)
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        c.execute("DROP TRIGGER %s" % name)
    svc.conn = c
    c.row_factory = sqlite3.Row
    return c


class ChainTests(Base):
    def fill(self, n):
        for i in range(n):
            self.svc.create_project(CTL, "p%d" % i)

    def test_valid_chain_passes_and_is_gap_free(self):
        self.fill(10)
        r = audit.verify_chain(self.svc.conn)
        self.assertTrue(r["ok"])
        self.assertEqual([e["seq"] for e in self.events()], list(range(1, len(self.events()) + 1)))

    def test_modified_event_detected_at_its_seq(self):
        self.fill(8)
        c = tamper_connection(self.svc)
        c.execute("UPDATE events SET payload_json='{\"x\":1}' WHERE seq=5")
        r = audit.verify_chain(c)
        self.assertEqual((r["ok"], r["break_seq"]), (False, 5))

    def test_deleted_event_detected(self):
        self.fill(8)
        c = tamper_connection(self.svc)
        c.execute("DELETE FROM events WHERE seq=4")
        r = audit.verify_chain(c)
        self.assertEqual((r["ok"], r["break_seq"]), (False, 4))

    def test_reordered_events_detected(self):
        self.fill(8)
        c = tamper_connection(self.svc)
        c.execute("UPDATE events SET seq=-1 WHERE seq=3")
        c.execute("UPDATE events SET seq=3 WHERE seq=6")
        c.execute("UPDATE events SET seq=6 WHERE seq=-1")
        self.assertFalse(audit.verify_chain(c)["ok"])

    def test_every_field_is_covered_by_the_hash(self):
        self.fill(3)
        c = tamper_connection(self.svc)
        for col, val in (("ts", 1), ("principal_type", "user"), ("principal_id", "evil"), ("kind", "other"), ("prev_hash", "0" * 63 + "1")):
            c.execute("BEGIN")
            c.execute("UPDATE events SET %s=? WHERE seq=2" % col, (val,))
            self.assertFalse(audit.verify_chain(c)["ok"], col)
            c.execute("ROLLBACK")
        self.assertTrue(audit.verify_chain(c)["ok"])


class CheckpointTests(Base):
    interval = 10

    def fill(self, n):
        for i in range(n):
            self.svc.create_project(CTL, "p%d" % i)

    def cp(self):
        return audit.classify_checkpoints(self.svc.conn, str(self.svc.paths["checkpoints"]), self.interval)["status"]

    def cp_files(self):
        return sorted(Path(self.svc.paths["checkpoints"]).glob("cp_*.json"))

    def test_not_due_then_consistent(self):
        self.fill(5)
        self.assertEqual(self.cp(), "not_due")
        self.fill(7)
        self.assertEqual(self.cp(), "consistent")
        self.assertTrue(self.cp_files())

    def test_checkpoint_written_after_commit_at_interval_and_on_shutdown(self):
        self.fill(10)
        self.assertEqual([f.name for f in self.cp_files()], ["cp_000000000010.json"])
        self.fill(3)
        self.svc.close()
        self.assertEqual(sorted(f.name for f in self.cp_files()), ["cp_000000000010.json", "cp_000000000013.json"])

    def test_older_checkpoint_is_legitimate_when_database_has_more_events(self):
        self.fill(14)   # checkpoint at 10, database at 14
        self.assertEqual(self.cp(), "consistent")
        r = audit.verify_chain(self.svc.conn)
        self.assertTrue(r["ok"])

    def test_missing_checkpoint(self):
        self.fill(15)
        for f in self.cp_files():
            f.unlink()
        self.assertEqual(self.cp(), "missing")

    def test_stale_checkpoint(self):
        self.fill(35)
        for f in self.cp_files()[1:]:
            f.unlink()          # only the checkpoint at 10 remains; boundaries 20 and 30 were lost
        self.assertEqual(self.cp(), "stale")

    def test_inconsistent_checkpoint(self):
        self.fill(12)
        f = self.cp_files()[0]
        d = json.loads(f.read_text())
        d["last_hash"] = "f" * 64
        f.write_text(json.dumps(d))
        self.assertEqual(self.cp(), "inconsistent")
        f.write_text("not json")
        self.assertEqual(self.cp(), "inconsistent")

    def test_checkpoint_ahead_of_a_restored_older_database(self):
        self.fill(5)
        self.svc.close()
        backup = str(self.svc.paths["db"]) + ".bak"
        shutil.copy(self.svc.paths["db"], backup)           # old database: 6 events
        self.svc = StorageService(self.home, clock=self.clock, interval=self.interval).open()
        self.fill(15)                                        # checkpoints now at 10, 20 and on shutdown
        self.svc.close()
        shutil.copy(backup, self.svc.paths["db"])            # "restore" the old database
        self.svc = StorageService(self.home, clock=self.clock, interval=self.interval)
        ro = connect_ro(self.svc.paths["db"])
        self.assertEqual(audit.classify_checkpoints(ro, str(self.svc.paths["checkpoints"]), self.interval)["status"], "ahead")
        self.assertTrue(audit.verify_chain(ro)["ok"])        # the chain itself is fine: only the checkpoint reveals the rollback
        ro.close()
        self.svc.conn = None
        self.svc.lock = None

    def test_truncated_tail_is_caught_by_the_checkpoint(self):
        self.fill(14)
        self.svc.close()
        self.svc = StorageService(self.home, clock=self.clock, interval=self.interval)
        c = sqlite3.connect(str(self.svc.paths["db"]), isolation_level=None)
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            c.execute("DROP TRIGGER %s" % name)
        c.execute("DELETE FROM events WHERE seq>11")         # checkpoint at 10 and the shutdown checkpoint at 15 exist
        self.assertTrue(audit.verify_chain(c)["ok"])
        self.assertEqual(audit.classify_checkpoints(c, str(self.svc.paths["checkpoints"]), self.interval)["status"], "ahead")
        c.close()
        self.svc.conn = self.svc.lock = None

    def test_failed_checkpoint_write_never_rolls_back_a_commit(self):
        def boom(*a, **k):
            raise OSError("disk full")
        self.svc._write_cp = boom
        self.fill(10)                                        # the 10th committed event is the checkpoint boundary
        self.assertEqual(len(self.events()), 10)             # all committed despite the failed write
        self.assertEqual(self.svc.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 10)
        self.assertTrue(self.svc._cp_failed)
        self.assertEqual(self.cp_files(), [])
        self.svc._write_cp = audit.write_checkpoint
        self.svc.create_project(CTL, "after")                # the next transaction records the earlier failure
        self.assertEqual(len(self.events("checkpoint_failed")), 1)
        self.assertEqual(len(self.events()), 12)
        self.assertFalse(self.svc._cp_failed)
        self.assertTrue(audit.verify_chain(self.svc.conn)["ok"])


class EvidenceTests(Base):
    def test_put_is_content_addressed_and_idempotent(self):
        h1 = self.svc.put_evidence(VER, b"hello")
        h2 = self.svc.put_evidence(VER, b"hello")
        self.assertEqual(h1, h2)
        self.assertEqual(h1, hashlib.sha256(b"hello").hexdigest())
        self.assertEqual(self.svc.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)
        self.assertEqual(self.svc.evidence.read(h1), b"hello")

    def test_tampered_or_missing_evidence_is_detected_never_repaired(self):
        h = self.svc.put_evidence(VER, b"original")
        path = self.svc.evidence.path_for(h)
        Path(path).write_bytes(b"changed")
        with self.assertRaises(EvidenceTampered):
            self.svc.evidence.read(h)
        self.assertEqual(self.svc.evidence_scan()["tampered"], [h])
        self.assertEqual(Path(path).read_bytes(), b"changed")   # not silently repaired
        os.remove(path)
        with self.assertRaises(EvidenceMissing):
            self.svc.evidence.read(h)
        self.assertEqual(self.svc.evidence_scan()["missing"], [h])

    def test_existing_corrupt_artifact_is_never_overwritten_or_reused(self):
        h = hashlib.sha256(b"payload").hexdigest()
        target = self.svc.evidence.path_for(h)
        os.makedirs(os.path.dirname(target))
        Path(target).write_bytes(b"corrupt")
        with self.assertRaises(EvidenceConflict):
            self.svc.evidence.put(b"payload")
        self.assertEqual(Path(target).read_bytes(), b"corrupt")

    def test_interruption_after_tmp_leaves_recoverable_temp_and_no_db_row(self):
        def boom(stage):
            if stage == "after_tmp":
                raise KeyboardInterrupt()
        self.svc.evidence.hook = boom
        with self.assertRaises(KeyboardInterrupt):
            self.svc.put_evidence(VER, b"interrupted")
        self.svc.evidence.hook = None
        scan = self.svc.evidence_scan()
        self.assertEqual(len(scan["tmp_files"]), 1)
        self.assertEqual(self.svc.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
        self.assertEqual(len(self.svc.evidence.cleanup_tmp()), 1)
        self.assertEqual(self.svc.evidence_scan()["tmp_files"], [])

    def test_interruption_after_publish_before_db_row_is_reported_as_orphan_not_deleted(self):
        def boom(stage):
            if stage == "after_publish":
                raise KeyboardInterrupt()
        self.svc.evidence.hook = boom
        with self.assertRaises(KeyboardInterrupt):
            self.svc.put_evidence(VER, b"published but unregistered")
        self.svc.evidence.hook = None
        h = hashlib.sha256(b"published but unregistered").hexdigest()
        self.assertEqual(self.svc.evidence_scan()["orphans"], [h])
        self.assertTrue(os.path.exists(self.svc.evidence.path_for(h)))
        self.assertEqual(self.svc.put_evidence(VER, b"published but unregistered"), h)   # retry registers it
        self.assertEqual(self.svc.evidence_scan()["orphans"], [])

    def test_database_row_and_event_commit_together(self):
        self.svc._fail_event_write = True
        with self.assertRaises(RuntimeError):
            self.svc.put_evidence(VER, b"x")
        self.svc._fail_event_write = False
        self.assertEqual(self.svc.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
        self.assertEqual(len(self.svc.evidence_scan()["orphans"]), 1)

    def test_workers_cannot_register_evidence(self):
        from tests.helpers import worker
        from vanilla_agents.contracts import AuthorityError
        with self.assertRaises(AuthorityError):
            self.svc.put_evidence(worker("w"), b"x")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="va_m_")
        self.home = os.path.join(self.tmp, "h")
        self.addCleanup(rmtree_force, self.tmp)

    def q(self, sql):
        c = sqlite3.connect(str(layout(self.home)["db"]))
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def versions(self):
        c = sqlite3.connect(str(layout(self.home)["db"]))
        try:
            return [r[0] for r in c.execute("SELECT version FROM schema_version ORDER BY version")]
        finally:
            c.close()

    def test_fresh_install_reaches_latest_and_second_open_is_a_noop(self):
        StorageService(self.home).open().close()
        self.assertEqual(self.versions(), [1, 2])
        before = self.q("SELECT applied_at FROM schema_version")
        StorageService(self.home).open().close()
        self.assertEqual(self.versions(), [1, 2])
        self.assertEqual(self.q("SELECT applied_at FROM schema_version"), before)

    def test_changed_migration_checksum_aborts_startup(self):
        StorageService(self.home).open().close()
        changed = [(1, "initial", MIGRATIONS[0][2] + "\n-- edited after being applied"), MIGRATIONS[1]]
        with self.assertRaises(VanillaError) as cm:
            StorageService(self.home, migrations=changed).open()
        self.assertEqual(cm.exception.code, "migration_checksum_mismatch")

    def test_failing_migration_rolls_back_cleanly(self):
        StorageService(self.home).open().close()
        bad = MIGRATIONS + [(3, "broken", "CREATE TABLE half_done(a INTEGER);\nTHIS IS NOT SQL;")]
        with self.assertRaises(sqlite3.Error):
            StorageService(self.home, migrations=bad).open()
        self.assertEqual(self.q("SELECT COUNT(*) FROM sqlite_master WHERE name='half_done'")[0][0], 0)
        self.assertEqual(self.versions(), [1, 2])
        StorageService(self.home).open().close()   # lock was released and the db is still usable

    def test_database_from_a_newer_version_is_refused(self):
        newer = MIGRATIONS + [(3, "later", "CREATE TABLE later(a INTEGER);")]
        StorageService(self.home, migrations=newer).open().close()
        with self.assertRaises(VanillaError) as cm:
            StorageService(self.home).open()
        self.assertEqual(cm.exception.code, "database_newer")


class ControllerLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="va_l_")
        self.addCleanup(rmtree_force, self.tmp)

    def test_second_writer_is_refused_while_the_first_lives(self):
        home = os.path.join(self.tmp, "h")
        with StorageService(home) as first:
            with self.assertRaises(ControllerBusy):
                StorageService(home).open()
            first.create_project(CTL, "still works")
        StorageService(home).open().close()   # free again after a clean close

    def test_concurrent_startups_produce_exactly_one_owner(self):
        lock_path = os.path.join(self.tmp, "c.lock")
        start_at = time.time() + 2.0
        code = ("import sys,time;from vanilla_agents.lock import ControllerLock;from vanilla_agents.contracts import ControllerBusy\n"
                "t=float(sys.argv[2]);time.sleep(max(0,t-time.time()))\n"
                "l=ControllerLock(sys.argv[1],'c'+sys.argv[3])\n"
                "try:\n l.acquire();print('OWNER',flush=True);time.sleep(3)\nexcept ControllerBusy:\n print('BUSY',flush=True)\n")
        procs = [run_py(code, lock_path, str(start_at), str(i)) for i in range(6)]
        outs = [p.communicate(timeout=30)[0].strip() for p in procs]
        self.assertEqual(outs.count("OWNER"), 1, outs)
        self.assertEqual(outs.count("BUSY"), 5, outs)

    def test_stale_lock_after_a_crash_is_recovered_with_a_takeover_event(self):
        home = os.path.join(self.tmp, "h")
        code = ("import sys,time;from vanilla_agents.storage import StorageService\ns=StorageService(sys.argv[1]).open();print('READY',flush=True);time.sleep(60)\n")
        p = run_py(code, home)
        self.assertEqual(p.stdout.readline().strip(), "READY")
        with self.assertRaises(ControllerBusy):               # a LIVE lock is never stolen
            StorageService(home).open()
        p.kill()
        p.wait()
        p.stdout.close()
        p.stderr.close()
        svc = StorageService(home).open()                      # the OS released the lock: recovery
        try:
            ev = svc.conn.execute("SELECT * FROM events WHERE kind='controller_takeover'").fetchall()
            self.assertEqual(len(ev), 1)
            self.assertIn("previous", ev[0]["payload_json"])
            self.assertTrue(audit.verify_chain(svc.conn)["ok"])
        finally:
            svc.close()
        svc2 = StorageService(home).open()                     # clean close leaves no record: no second takeover
        try:
            self.assertEqual(svc2.conn.execute("SELECT COUNT(*) FROM events WHERE kind='controller_takeover'").fetchone()[0], 1)
        finally:
            svc2.close()

    def test_kill_mid_transaction_leaves_no_partial_state(self):
        home = os.path.join(self.tmp, "h")
        code = ("import sys,time;from vanilla_agents.storage import StorageService\ns=StorageService(sys.argv[1]).open()\n"
                "s.conn.execute('BEGIN IMMEDIATE');s.conn.execute(\"INSERT INTO projects VALUES('proj_partial','x',NULL,1)\")\n"
                "print('READY',flush=True);time.sleep(60)\n")
        p = run_py(code, home)
        self.assertEqual(p.stdout.readline().strip(), "READY")
        p.kill()
        p.wait()
        p.stdout.close()
        p.stderr.close()
        svc = StorageService(home).open()
        try:
            self.assertEqual(svc.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)
            self.assertTrue(audit.verify_chain(svc.conn)["ok"])
        finally:
            svc.close()

    def test_inspection_connections_cannot_write(self):
        home = os.path.join(self.tmp, "h")
        with StorageService(home) as svc:
            svc.create_project(CTL, "p")
            ro = connect_ro(svc.paths["db"])
            for sql in ("INSERT INTO projects VALUES('x','x',NULL,1)", "DELETE FROM projects", "UPDATE tasks SET title='x'"):
                with self.assertRaises(sqlite3.Error):
                    ro.execute(sql)
            ro.close()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="va_c_")
        self.home = os.path.join(self.tmp, "h")
        self.addCleanup(rmtree_force, self.tmp)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_init_creates_layout_and_refuses_non_empty_directories(self):
        rc, out, _ = self.run_cli("init", "--home", self.home)
        self.assertEqual(rc, 0)
        self.assertTrue(layout(self.home)["db"].exists())
        marker = Path(self.home) / "state" / "db" / "vanilla.db"
        size = marker.stat().st_size
        rc, _, err = self.run_cli("init", "--home", self.home)
        self.assertEqual(rc, 2)
        self.assertIn("home_not_empty", err)
        self.assertEqual(marker.stat().st_size, size)         # nothing overwritten
        other = os.path.join(self.tmp, "occupied")
        os.makedirs(other)
        Path(other, "keep.txt").write_text("mine")
        rc, _, err = self.run_cli("init", "--home", other)
        self.assertEqual(rc, 2)
        self.assertEqual(Path(other, "keep.txt").read_text(), "mine")
        self.assertEqual(os.listdir(other), ["keep.txt"])

    def test_read_only_commands_change_nothing_logically(self):
        with StorageService(self.home, clock=Clock()) as svc:
            pid = svc.create_project(CTL, "p")
            tid = svc.create_task(CTL, {"project_id": pid, "title": "t", "description": "d"})
            crit = svc.add_criterion(CTL, tid, "c", True, "k")
            ev = svc.put_evidence(VER, b"e")
            svc.insert_claim(VER, Base.claim(tid, crit, "k", [ev]))
            ro = connect_ro(svc.paths["db"])
            before, seq = logical_dump(ro), ro.execute("SELECT MAX(seq) FROM events").fetchone()[0]
            for args in (("status",), ("tasks",), ("task", tid), ("events",), ("events", "--task", tid), ("claims", tid), ("verify-chain",), ("evidence-scan",)):
                rc, out, err = self.run_cli(args[0], "--home", self.home, *args[1:])
                self.assertEqual(rc, 0, (args, out, err))
            self.assertEqual(logical_dump(ro), before)
            self.assertEqual(ro.execute("SELECT MAX(seq) FROM events").fetchone()[0], seq)
            ro.close()

    def test_verify_chain_exit_codes(self):
        with StorageService(self.home, clock=Clock()) as svc:
            svc.create_project(CTL, "p")
        self.assertEqual(self.run_cli("verify-chain", "--home", self.home)[0], 0)
        c = sqlite3.connect(str(layout(self.home)["db"]), isolation_level=None)
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            c.execute("DROP TRIGGER %s" % name)
        c.execute("UPDATE events SET payload_json='{}' WHERE seq=1")
        c.close()
        rc, out, _ = self.run_cli("verify-chain", "--home", self.home)
        self.assertEqual(rc, 1)
        self.assertIn("BROKEN at seq 1", out)

    def test_missing_database_is_a_clean_error(self):
        rc, _, err = self.run_cli("status", "--home", os.path.join(self.tmp, "nothing"))
        self.assertEqual(rc, 2)
        self.assertIn("no_database", err)


class SafetyTests(unittest.TestCase):
    def tree_hash(self, root):
        h = hashlib.sha256()
        for p in sorted(Path(root).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(str(p.relative_to(root)).encode())
                h.update(p.read_bytes())
        return h.hexdigest()

    def test_workflow_touches_only_its_temporary_home(self):
        tmp = tempfile.mkdtemp(prefix="va_s_")
        self.addCleanup(rmtree_force, tmp)
        src_before = self.tree_hash(Path(ROOT) / "vanilla_agents")
        cwd_before = sorted(os.listdir(os.getcwd()))
        with StorageService(os.path.join(tmp, "h"), clock=Clock()) as svc:
            pid = svc.create_project(CTL, "p")
            tid = svc.create_task(CTL, {"project_id": pid, "title": "t", "description": "d"})
            svc.put_evidence(VER, b"e")
        self.assertEqual(self.tree_hash(Path(ROOT) / "vanilla_agents"), src_before)
        self.assertEqual(sorted(os.listdir(os.getcwd())), cwd_before)
        self.assertTrue(all(str(p).startswith(tmp) for p in Path(tmp).rglob("*")))


if __name__ == "__main__":
    unittest.main()
