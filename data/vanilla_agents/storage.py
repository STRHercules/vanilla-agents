"""The controller-owned StorageService: the ONLY module that touches SQLite.

Every mutation runs in one BEGIN IMMEDIATE transaction together with its audit event; if the event cannot be written the mutation
rolls back. Authority and state rules are checked before anything changes; a rejected request writes a `rejected` event and changes
nothing else. One writer: the process holding the OS-enforced controller lock (everyone else uses read-only connections).
"""
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from . import audit, authority, ledger, state
from .authority import Actor
from .contracts import (BLOCKED_REASONS, POSTURE_LEVEL0, SCHEMAS, AuthorityError, EvidenceMissing, EvidenceTampered, VanillaError, LeaseBusy,
                        Principal, StaleFence, ValidationError, new_id, validate)
from .evidence import EvidenceStore
from .lock import ControllerLock
from .migrations import MIGRATIONS, checksum

CONTROLLER = Actor(Principal.CONTROLLER, "controller")
KNOWN_POSTURES = {POSTURE_LEVEL0}


def layout(home):
    h = Path(home)
    s = h / "state"
    return {"home": h, "db_dir": s / "db", "db": s / "db" / "vanilla.db", "checkpoints": s / "checkpoints", "backups": s / "backups",
            "evidence": s / "evidence", "lock": s / "controller.lock"}


def daily_backup(home, keep=7, now=None):
    """Once a day, a consistent copy of state/db/vanilla.db into state/backups/vanilla-YYYYMMDD.db (SQLite's online backup, safe while
    the database is in use); the oldest copies beyond `keep` are removed. Returns the new file, or None (no database / already today)."""
    import time as _time
    lay = layout(home)
    if not lay["db"].exists():
        return None
    day = _time.strftime("%Y%m%d", _time.localtime(now))
    dest = lay["backups"] / ("vanilla-%s.db" % day)
    if dest.exists():
        return None
    lay["backups"].mkdir(parents=True, exist_ok=True)
    src = connect_ro(lay["db"])
    try:
        out = sqlite3.connect(str(dest) + ".tmp")
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    os.replace(str(dest) + ".tmp", dest)
    for old in sorted(lay["backups"].glob("vanilla-*.db"))[:-keep]:
        old.unlink()
    return dest


def connect_ro(db_path):
    """Read-only connection (no write methods exist on it; SQLite refuses writes)."""
    conn = sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def logical_dump(conn):
    """Logical content of every table (used to prove read-only commands change nothing)."""
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        out[name] = [tuple(r) for r in conn.execute("SELECT * FROM %s ORDER BY 1" % name)]
    return out


def init_home(home):
    """Create the state layout and an empty, migrated database. Refuses a non-empty target; never overwrites."""
    p = Path(home)
    if p.exists() and any(p.iterdir()):
        raise VanillaError("refusing to initialise: %s exists and is not empty" % p, code="home_not_empty")
    svc = StorageService(home)
    svc.open()
    svc.close()
    return layout(home)


def _canon(o):
    return audit.canonical(o)


class StorageService:
    def __init__(self, home, clock=time.time, interval=audit.CHECKPOINT_INTERVAL, posture=POSTURE_LEVEL0, controller_id=None, migrations=None):
        self.paths = layout(home)
        self.clock = clock
        self.interval = interval
        if posture not in KNOWN_POSTURES:
            raise ValidationError("unknown security posture %r" % (posture,))
        self.posture = posture
        self.migrations = migrations if migrations is not None else MIGRATIONS
        self.controller_id = controller_id or "ctl_" + os.urandom(4).hex()
        self.conn = None
        self.lock = None
        self.evidence = None
        self._cp_failed = False
        self._fail_event_write = False          # test hook: simulate an event-write failure
        self._write_cp = audit.write_checkpoint  # test hook: replaceable checkpoint writer

    # ------------------------------------------------------------ lifecycle
    def open(self):
        for k in ("db_dir", "checkpoints", "backups", "evidence"):
            self.paths[k].mkdir(parents=True, exist_ok=True)
        self.lock = ControllerLock(self.paths["lock"], self.controller_id).acquire()
        try:
            self.conn = sqlite3.connect(str(self.paths["db"]), isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self._migrate()
            self.evidence = EvidenceStore(self.paths["evidence"])
            if self.lock.previous is not None:
                self._run(CONTROLLER, "controller_takeover", None, "controller_takeover",
                          lambda c: (None, {"previous": self.lock.previous, "new": self.controller_id}))
        except BaseException:
            self.close(final_checkpoint=False, clear_record=self.lock is None or self.lock.previous is None or self._takeover_done())
            raise
        return self

    def _takeover_done(self):
        try:
            return self.conn is not None and self.conn.execute("SELECT 1 FROM events WHERE kind='controller_takeover' LIMIT 1").fetchone() is not None
        except sqlite3.Error:
            return False

    def close(self, final_checkpoint=True, clear_record=True):
        if self.conn is not None:
            if final_checkpoint:
                row = self.conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                done = self.conn.execute("SELECT MAX(seq) FROM checkpoints").fetchone()[0]
                if row and done != row["seq"]:
                    self._checkpoint(row["seq"], row["hash"])
            self.conn.close()
            self.conn = None
        if self.lock is not None:
            self.lock.release(clear_record=clear_record)
            self.lock = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    def _migrate(self):
        c = self.conn
        c.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at INTEGER NOT NULL)")
        known = {v: (n, checksum(sql)) for v, n, sql in self.migrations}
        applied = {r["version"]: r["checksum"] for r in c.execute("SELECT version, checksum FROM schema_version")}
        newest = max(known) if known else 0
        if applied and max(applied) > newest:
            raise VanillaError("database schema version %d is newer than this software (%d)" % (max(applied), newest), code="database_newer")
        for v, cs in applied.items():
            if v not in known or known[v][1] != cs:
                raise VanillaError("applied migration %d does not match its recorded checksum" % v, code="migration_checksum_mismatch")
        for v, name, sql in self.migrations:
            if v in applied:
                continue
            script = "BEGIN;\n%s\nINSERT INTO schema_version VALUES(%d,'%s','%s',%d);\nCOMMIT;" % (sql, v, name, checksum(sql), self._ts())
            try:
                c.executescript(script)
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise

    def _ts(self):
        return int(self.clock() * 1_000_000)

    # ------------------------------------------------------------ transaction plumbing
    def _append_event(self, actor, kind, task_id, payload):
        c = self.conn
        if self._fail_event_write:
            raise RuntimeError("simulated event write failure")
        last = c.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev = (last["seq"] + 1, last["hash"]) if last else (1, audit.GENESIS)
        pj, ts = _canon(payload or {}), self._ts()
        eid = new_id("evt")
        h = audit.event_hash(eid, seq, ts, actor.type, actor.id, task_id, kind, pj, prev)
        c.execute("INSERT INTO events(event_id, seq, ts, principal_type, principal_id, task_id, kind, payload_json, prev_hash, hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (eid, seq, ts, actor.type, actor.id, task_id, kind, pj, prev, h))

    def _run(self, actor, op, task_id, kind, fn):
        """authority check -> BEGIN IMMEDIATE -> fn(conn) -> event -> COMMIT. fn returns (result, payload)."""
        pending_cp_failure = self._cp_failed
        try:
            authority.require(actor, op)
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if pending_cp_failure:
                    self._append_event(CONTROLLER, "checkpoint_failed", None, {"note": "an earlier checkpoint write failed"})
                result, payload = fn(self.conn)
                self._append_event(actor, kind, task_id, payload)
                self.conn.execute("COMMIT")
            except BaseException:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise
        except VanillaError as e:
            self._record_rejection(actor, op, task_id, e)
            raise
        if pending_cp_failure:
            self._cp_failed = False
        self._after_commit()
        return result

    def _record_rejection(self, actor, op, task_id, err):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self._append_event(actor, "rejected", task_id, {"operation": op, "code": err.code, "message": str(err)[:300]})
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            return
        self._after_commit()

    def _after_commit(self):
        row = self.conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        if row and row["seq"] % self.interval == 0:
            self._checkpoint(row["seq"], row["hash"])

    def _checkpoint(self, seq, last_hash):
        """Runs AFTER a commit; a failure never rolls anything back, it is recorded as an event on the next transaction."""
        try:
            path = self._write_cp(str(self.paths["checkpoints"]), seq, seq, last_hash)
            self.conn.execute("INSERT OR REPLACE INTO checkpoints(seq, last_event_hash, event_count, written_at, file_path) VALUES(?,?,?,?,?)",
                              (seq, last_hash, seq, self._ts(), os.path.basename(path)))
        except (OSError, sqlite3.Error):
            self._cp_failed = True

    # ------------------------------------------------------------ small helpers
    def _task(self, c, task_id):
        r = c.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if r is None:
            raise VanillaError("task %s not found" % task_id, code="not_found")
        return r

    def _touch(self, c, task_id, **cols):
        sets = ", ".join("%s=?" % k for k in cols)
        c.execute("UPDATE tasks SET %s, updated_at=?, version=version+1 WHERE task_id=?" % sets, (*cols.values(), self._ts(), task_id))

    def _task_worker_state(self, c, task_id):
        """None if every worker ever attached to the task (any attempt, any lease) is confirmed terminated; else the state of one that is not.

        An attempt marked LOST/FAILED says nothing about its worker: only the worker's own state can prove termination (D-014, D-021)."""
        rows = c.execute("SELECT w.state FROM workers w WHERE w.worker_id IN "
                         "(SELECT worker_id FROM attempts WHERE task_id=? AND worker_id IS NOT NULL UNION SELECT holder_worker_id FROM leases WHERE holder_task_id=? AND holder_worker_id IS NOT NULL)",
                         (task_id, task_id)).fetchall()
        live = [r["state"] for r in rows if r["state"] not in state.TERMINATED_WORKER_STATES]
        return live[0] if live else None

    def _stale_task(self, c, task_id, reason):
        """A qualification input changed: VERIFIED -> STALE, qualification dropped, any APPROVED release revoked."""
        t = self._task(c, task_id)
        info = {"task_id": task_id, "reason": reason, "went_stale": False, "release_revoked": False}
        if t["verification_state"] == "VERIFIED":
            state.require("verification", "VERIFIED", "STALE")
            self._touch(c, task_id, verification_state="STALE", qualification_id=None)
            info["went_stale"] = True
        if t["release_state"] == "APPROVED":
            state.require("release", "APPROVED", "REVOKED")
            self._touch(c, task_id, release_state="REVOKED")
            info["release_revoked"] = True
        return info

    # ------------------------------------------------------------ projects, tasks, criteria, policy
    def create_project(self, actor, name):
        def fn(c):
            pid = new_id("proj")
            c.execute("INSERT INTO projects VALUES(?,?,?,?)", (pid, name, None, self._ts()))
            return pid, {"project_id": pid, "name": name}
        return self._run(actor, "create_project", None, "project_created", fn)

    def create_task(self, actor, data):
        def fn(c):
            validate(data, SCHEMAS["task_input"])
            if c.execute("SELECT 1 FROM projects WHERE project_id=?", (data["project_id"],)).fetchone() is None:
                raise ValidationError("unknown project", code="not_found")
            tid, now = new_id("task"), self._ts()
            c.execute("INSERT INTO tasks(task_id, project_id, title, description, base_commit, execution_state, cancel_state, verification_state, release_state, "
                      "budgets_json, owned_paths_json, security_posture, created_at, updated_at, version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                      (tid, data["project_id"], data["title"], data["description"], data.get("base_commit"), "QUEUED", "NONE", "NOT_RUN", "NOT_APPROVED",
                       _canon(data.get("budgets", {})), _canon(data.get("owned_paths", [])), self.posture, now, now))
            return tid, {"task_id": tid, "title": data["title"]}
        return self._run(actor, "create_task", None, "task_created", fn)

    def add_criterion(self, actor, task_id, text, required=True, check_id="check"):
        def fn(c):
            self._task(c, task_id)
            cid = new_id("crit")
            c.execute("INSERT INTO criteria VALUES(?,?,?,?,?)", (cid, task_id, text, 1 if required else 0, check_id))
            info = self._stale_task(c, task_id, "criterion_added")   # a qualification never covers a criterion added after it (D-025)
            return cid, {"criterion_id": cid, "required": bool(required), "check_id": check_id, "effects": info}
        return self._run(actor, "add_criterion", task_id, "criterion_added", fn)

    def add_policy(self, actor, project_id, body):
        def fn(c):
            if c.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone() is None:
                raise ValidationError("unknown project", code="not_found")
            v = (c.execute("SELECT COALESCE(MAX(version),0) FROM policies WHERE project_id=?", (project_id,)).fetchone()[0]) + 1
            bj = _canon(body)
            h = hashlib.sha256(bj.encode()).hexdigest()
            c.execute("INSERT INTO policies VALUES(?,?,?,?,?,?)", (new_id("pol"), project_id, v, bj, h, self._ts()))
            return h, {"project_id": project_id, "version": v, "body_hash": h}
        return self._run(actor, "add_policy", None, "policy_added", fn)

    def set_task_policy(self, actor, task_id, policy_hash):
        def fn(c):
            t = self._task(c, task_id)
            if c.execute("SELECT 1 FROM policies WHERE body_hash=? AND project_id=?", (policy_hash, t["project_id"])).fetchone() is None:
                raise ValidationError("policy hash is not a policy of this task's project", code="unknown_policy")
            changed = t["policy_hash"] != policy_hash
            self._touch(c, task_id, policy_hash=policy_hash)
            info = self._stale_task(c, task_id, "policy_changed") if changed else {}
            return info, {"policy_hash": policy_hash, "effects": info}
        return self._run(actor, "set_task_policy", task_id, "task_policy_set", fn)

    def set_task_target(self, actor, task_id, target_ref):
        def fn(c):
            t = self._task(c, task_id)
            self._touch(c, task_id, target_ref=target_ref)
            info = self._stale_task(c, task_id, "target_changed") if t["target_ref"] != target_ref else {}
            return info.get("release_revoked", False), {"target_ref": target_ref, "effects": info}
        return self._run(actor, "set_task_target", task_id, "task_target_set", fn)

    def update_task_source(self, actor, task_id, current_commit, env_fingerprint):
        """Conservative staleness: claims whose source_commit or env_fingerprint differ are invalidated."""
        def fn(c):
            self._task(c, task_id)
            self._touch(c, task_id, current_commit=current_commit, env_fingerprint=env_fingerprint)
            n = 0
            for cl in ledger.current_claims(c, task_id):
                if cl["source_commit"] != current_commit or cl["env_fingerprint"] != env_fingerprint:
                    c.execute("INSERT INTO claim_invalidations VALUES(?,?,?,?,?)", (new_id("inv"), cl["claim_id"], "source_or_environment_changed", actor.id, self._ts()))
                    n += 1
            info = self._stale_task(c, task_id, "source_or_environment_changed") if n else {}
            return n, {"commit": current_commit, "invalidated_claims": n, "effects": info}
        return self._run(actor, "update_task_source", task_id, "task_source_updated", fn)

    # ------------------------------------------------------------ execution and cancellation
    def transition_execution(self, actor, task_id, new_state, fence=None):
        def fn(c):
            self._check_fence(c, fence, task_id)
            t = self._task(c, task_id)
            if new_state == "CANCELLED":
                raise state.IllegalTransition("CANCELLED is reached only through finish_cancel")
            if new_state == "BLOCKED":
                raise state.IllegalTransition("use block_task to block with a reason")
            if t["cancel_state"] != "NONE":
                raise state.IllegalTransition("cancellation is %s; the task may not move on" % t["cancel_state"], code="cancel_in_progress")
            state.require("execution", t["execution_state"], new_state)
            self._touch(c, task_id, execution_state=new_state, **({"blocked_reason": None} if t["execution_state"] == "BLOCKED" else {}))
            return new_state, {"from": t["execution_state"], "to": new_state}
        return self._run(actor, "transition_execution", task_id, "execution_changed", fn)

    def block_task(self, actor, task_id, reason, fence=None):
        def fn(c):
            self._check_fence(c, fence, task_id)
            if reason not in BLOCKED_REASONS:
                raise ValidationError("unknown blocked reason %r" % reason)
            t = self._task(c, task_id)
            state.require("execution", t["execution_state"], "BLOCKED")
            self._touch(c, task_id, execution_state="BLOCKED", blocked_reason=reason)
            return "BLOCKED", {"from": t["execution_state"], "reason": reason}
        return self._run(actor, "set_blocked", task_id, "task_blocked", fn)

    def _cancel_step(self, c, task_id, to):
        t = self._task(c, task_id)
        if t["execution_state"] in state.EXEC_TERMINAL:
            raise state.IllegalTransition("task is terminal (%s)" % t["execution_state"])
        state.require("cancel", t["cancel_state"], to)
        if to == "CONFIRMED":
            ws = self._task_worker_state(c, task_id)
            if ws is not None and ws not in state.TERMINATED_WORKER_STATES:
                raise state.IllegalTransition("worker not confirmed terminated (state=%s)" % ws)
        self._touch(c, task_id, cancel_state=to)
        return to, {"from": t["cancel_state"], "to": to}

    def request_cancel(self, actor, task_id):
        return self._run(actor, "request_cancel", task_id, "cancel_requested", lambda c: self._cancel_step(c, task_id, "REQUESTED"))

    def advance_cancel(self, actor, task_id, to):
        return self._run(actor, "advance_cancel", task_id, "cancel_advanced", lambda c: self._cancel_step(c, task_id, to))

    def finish_cancel(self, actor, task_id):
        def fn(c):
            t = self._task(c, task_id)
            open_leases = c.execute("SELECT COUNT(*) FROM leases WHERE holder_task_id=? AND state!='released'", (task_id,)).fetchone()[0]
            state.require_cancelled(t["execution_state"], t["cancel_state"], self._task_worker_state(c, task_id), open_leases)
            self._touch(c, task_id, execution_state="CANCELLED", blocked_reason=None)
            return "CANCELLED", {"from": t["execution_state"]}
        return self._run(actor, "finish_cancel", task_id, "task_cancelled", fn)

    # ------------------------------------------------------------ workers and attempts
    def register_worker(self, actor, provider=None, version=None, worker_id=None):
        def fn(c):
            wid = worker_id or new_id("wrk")
            c.execute("INSERT INTO workers VALUES(?,?,?,?,?)", (wid, "starting", self._ts(), provider, version))
            return wid, {"worker_id": wid, "provider": provider, "version": version}
        return self._run(actor, "register_worker", None, "worker_registered", fn)

    def _single_task(self, worker_id):
        """The one task a worker is attached to (attempts or leases), else None; used to tag audit events (D-104)."""
        rows = self.conn.execute("SELECT task_id FROM attempts WHERE worker_id=? UNION SELECT holder_task_id FROM leases WHERE holder_worker_id=?", (worker_id, worker_id)).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def set_worker_state(self, actor, worker_id, new_state, task_id=None, proof=None):
        def fn(c):
            w = c.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
            if w is None:
                raise VanillaError("worker not found", code="not_found")
            state.require("worker", w["state"], new_state)
            c.execute("UPDATE workers SET state=? WHERE worker_id=?", (new_state, worker_id))
            pl = {"worker_id": worker_id, "from": w["state"], "to": new_state}
            if proof is not None:
                pl["proof"] = proof
            return new_state, pl
        return self._run(actor, "set_worker_state", task_id or self._single_task(worker_id), "worker_state_changed", fn)

    def worker_heartbeat(self, actor, worker_id, lease_ttl=None):
        """Heartbeat; with lease_ttl it also extends this worker's held, unexpired leases to now+ttl (D-100)."""
        def fn(c):
            if actor.id != worker_id:
                raise AuthorityError("a worker may only heartbeat for itself")
            w = c.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
            if w is None:
                raise VanillaError("worker not found", code="not_found")
            if w["state"] in ("starting", "unreachable"):
                state.require("worker", w["state"], "alive")
                c.execute("UPDATE workers SET state='alive', last_heartbeat=? WHERE worker_id=?", (self._ts(), worker_id))
            elif w["state"] == "alive":
                c.execute("UPDATE workers SET last_heartbeat=? WHERE worker_id=?", (self._ts(), worker_id))
            else:
                raise state.IllegalTransition("worker is %s; heartbeat refused" % w["state"])
            pl = {"worker_id": worker_id}
            if lease_ttl is not None:
                pl["renewed"] = self._renew_held(c, worker_id, lease_ttl)
            return None, pl
        return self._run(actor, "worker_heartbeat", None, "worker_heartbeat", fn)

    def _renew_held(self, c, worker_id, ttl):
        now = self._ts()
        ids = [r[0] for r in c.execute("SELECT lease_id FROM leases WHERE holder_worker_id=? AND state='held' AND expires_at>? ORDER BY lease_id", (worker_id, now))]
        for lid in ids:
            c.execute("UPDATE leases SET expires_at=MAX(expires_at, ?) WHERE lease_id=?", (now + int(ttl * 1_000_000), lid))
        return ids

    def _require_open_task(self, c, task_id):
        t = self._task(c, task_id)
        if t["cancel_state"] != "NONE" or t["execution_state"] in state.EXEC_TERMINAL:
            raise state.IllegalTransition("task is cancelling or finished; no new attempts or leases", code="task_not_open")
        return t

    def create_attempt(self, actor, task_id, worker_id=None, fence=None):
        def fn(c):
            self._check_fence(c, fence, task_id, worker_id)
            self._require_open_task(c, task_id)
            n = c.execute("SELECT COALESCE(MAX(number),0)+1 FROM attempts WHERE task_id=?", (task_id,)).fetchone()[0]
            aid = new_id("att")
            c.execute("INSERT INTO attempts(attempt_id, task_id, number, worker_id, state, started_at) VALUES(?,?,?,?,?,?)", (aid, task_id, n, worker_id, "PLANNED", self._ts()))
            return aid, {"attempt_id": aid, "number": n, "worker_id": worker_id}
        return self._run(actor, "create_attempt", task_id, "attempt_created", fn)

    def transition_attempt(self, actor, attempt_id, new_state, outcome=None, failure_class=None, fence=None):
        def fn(c):
            a = c.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if a is None:
                raise VanillaError("attempt not found", code="not_found")
            self._check_fence(c, fence, a["task_id"], a["worker_id"])
            state.require("attempt", a["state"], new_state)
            ended = self._ts() if not state.ATTEMPT[new_state] else None
            c.execute("UPDATE attempts SET state=?, outcome=?, failure_class=?, ended_at=? WHERE attempt_id=?", (new_state, outcome, failure_class, ended, attempt_id))
            return new_state, {"attempt_id": attempt_id, "from": a["state"], "to": new_state, "failure_class": failure_class}
        return self._run(actor, "transition_attempt", None, "attempt_changed", fn)

    def worker_event(self, actor, attempt_id, text):
        def fn(c):
            a = c.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if a is None or a["worker_id"] != actor.id:
                raise AuthorityError("a worker may only write events about its own attempt")
            return None, {"attempt_id": attempt_id, "text": str(text)[:500]}
        return self._run(actor, "worker_event", None, "worker_note", fn)

    # ------------------------------------------------------------ evidence
    def register_evidence(self, actor, h, size, media_type, stored_path):
        def fn(c):
            if not (isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)):
                raise ValidationError("evidence id must be 64 lowercase hex characters", code="bad_evidence_id")
            if stored_path != "%s/%s" % (h[:2], h):
                raise ValidationError("stored path does not match the content address", code="bad_evidence_path")
            data = self.evidence.read(h)          # the file must exist and match its name
            if len(data) != size:
                raise ValidationError("size does not match the stored evidence", code="bad_evidence_size")
            if c.execute("SELECT 1 FROM evidence WHERE evidence_id=?", (h,)).fetchone():
                return False, {"evidence_id": h, "duplicate": True}
            c.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?)", (h, size, media_type, stored_path, self._ts(), actor.id))
            return True, {"evidence_id": h, "size": size}
        return self._run(actor, "register_evidence", None, "evidence_added", fn)

    def put_evidence(self, actor, data, media_type="application/octet-stream"):
        """Staged publish (tmp -> hash -> atomic publish) then ONE transaction for the row + event."""
        try:
            authority.require(actor, "register_evidence")
        except VanillaError as e:
            self._record_rejection(actor, "register_evidence", None, e)
            raise
        h, size, rel = self.evidence.put(data)
        self.register_evidence(actor, h, size, media_type, rel)
        return h

    def evidence_scan(self):
        known = {r["evidence_id"] for r in self.conn.execute("SELECT evidence_id FROM evidence")}
        return self.evidence.scan(known)

    # ------------------------------------------------------------ claims, qualification, verification
    def insert_claim(self, actor, data):
        def fn(c):
            return self._claim_tx(c, data)
        return self._run(actor, "insert_claim", data.get("task_id"), "claim_added", fn)

    def _claim_tx(self, c, data, method="deterministic", bundle_hash=None):
        """Every claim rule in one place (used by insert_claim and by bundle ingestion); runs inside the caller's transaction."""
        validate(data, SCHEMAS["claim_input"])
        t = self._task(c, data["task_id"])
        crit = c.execute("SELECT check_id FROM criteria WHERE criterion_id=? AND task_id=?", (data["criterion_id"], data["task_id"])).fetchone()
        if crit is None:
            raise ValidationError("unknown criterion for this task", code="not_found")
        if crit["check_id"] != data["check_id"]:
            raise ValidationError("claim check_id does not match the criterion's check", code="check_mismatch")
        if data.get("attempt_id") and c.execute("SELECT 1 FROM attempts WHERE attempt_id=? AND task_id=?", (data["attempt_id"], data["task_id"])).fetchone() is None:
            raise ValidationError("attempt does not belong to this task", code="attempt_mismatch")
        if t["current_commit"] is not None and (data["source_commit"] != t["current_commit"] or data["env_fingerprint"] != t["env_fingerprint"]):
            raise ValidationError("claim was produced for a different source or environment than the task's current one", code="claim_source_mismatch")
        if data["status"] == "VERIFIED" and not data["evidence_refs"]:
            raise ValidationError("a VERIFIED claim must reference evidence", code="verified_claim_needs_evidence")
        for h in data["evidence_refs"]:
            if c.execute("SELECT 1 FROM evidence WHERE evidence_id=?", (h,)).fetchone() is None:
                raise ValidationError("unknown evidence reference %s" % h, code="unknown_evidence")
        old = ledger.current_claims(c, data["task_id"])
        cid = new_id("clm")
        c.execute("INSERT INTO claims(claim_id, task_id, attempt_id, criterion_id, check_id, run_id, status, expected, observed, evidence_refs_json, verifier_id, "
                  "verifier_version, source_commit, env_fingerprint, repeatability_class, security_posture, verified_at, method, bundle_hash) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (cid, data["task_id"], data.get("attempt_id"), data["criterion_id"], data["check_id"], data.get("run_id"), data["status"], data["expected"],
                   data["observed"], _canon(data["evidence_refs"]), data["verifier_id"], data["verifier_version"], data["source_commit"], data["env_fingerprint"],
                   data["repeatability_class"], self.posture, self._ts(), method, bundle_hash))
        superseded = 0
        for o in old:
            if o["criterion_id"] == data["criterion_id"] and o["check_id"] == data["check_id"]:
                c.execute("INSERT INTO claim_supersessions VALUES(?,?,?)", (o["claim_id"], cid, self._ts()))
                superseded += 1
        # the qualification was built on the claims just replaced: it no longer describes the current evidence (D-025)
        info = self._stale_task(c, data["task_id"], "claim_superseded") if superseded else {}
        return cid, {"claim_id": cid, "status": data["status"], "criterion_id": data["criterion_id"], "effects": info}

    def invalidate_claim(self, actor, claim_id, reason):
        def fn(c):
            cl = c.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
            if cl is None:
                raise VanillaError("claim not found", code="not_found")
            was_current = any(x["claim_id"] == claim_id for x in ledger.current_claims(c, cl["task_id"]))
            c.execute("INSERT INTO claim_invalidations VALUES(?,?,?,?,?)", (new_id("inv"), claim_id, reason, actor.id, self._ts()))
            info = self._stale_task(c, cl["task_id"], "claim_invalidated") if was_current else {}
            return info, {"claim_id": claim_id, "reason": reason, "effects": info}
        return self._run(actor, "invalidate_claim", None, "claim_invalidated", fn)

    def set_verification(self, actor, task_id, new_state):
        def fn(c):
            t = self._task(c, task_id)
            state.require("verification", t["verification_state"], new_state)
            if new_state == "STALE":                       # every exit from VERIFIED drops the qualification and revokes an approved release (D-025)
                info = self._stale_task(c, task_id, "marked_stale")
                return None, {"from": t["verification_state"], "to": "STALE", "effects": info}
            extra = {}
            if new_state == "VERIFIED":
                if actor.type != Principal.VERIFIER:
                    raise AuthorityError("only the verifier may qualify a task as VERIFIED")
                extra["qualification_id"] = self._qualify(c, t)
            self._touch(c, task_id, verification_state=new_state, **extra)
            return extra.get("qualification_id"), {"from": t["verification_state"], "to": new_state, **extra}
        return self._run(actor, "set_verification", task_id, "verification_changed", fn)

    def _qualify(self, c, t):
        """Create a qualification for a task whose required criteria all have current VERIFIED claims, source recorded and evidence intact."""
        task_id = t["task_id"]
        ok, det = ledger.evaluate(c, task_id)
        if not ok:
            raise state.IllegalTransition("required criteria lack current VERIFIED claims", code="required_claims_not_verified")
        if t["current_commit"] is None or t["env_fingerprint"] is None:
            raise state.IllegalTransition("the task's source commit and environment must be recorded before qualification", code="task_source_not_set")
        bad = self._bad_evidence(det["evidence"])
        if bad:
            raise state.IllegalTransition("referenced evidence is missing or altered: %s (run revalidate_evidence)" % sorted(bad), code="evidence_invalid")
        qid = new_id("qual")
        c.execute("INSERT INTO qualifications VALUES(?,?,?,?,?,?,?)",
                  (qid, task_id, ledger.evidence_set_hash(det["claim_ids"], det["evidence"]), _canon(det["claim_ids"]), t["policy_hash"], t["current_commit"], self._ts()))
        return qid

    def ingest_verification(self, actor, bundle, blobs, expected_manifest_hash):
        """Validate a result bundle from the verifier subprocess and store it: evidence files, the run record and ALL claims in one transaction.

        A bundle that is incomplete, mismatched or from an unexpected manifest writes nothing (a crashed verifier promotes nothing).
        blobs: {sha256 hex: bytes}. Returns {"run_id", "claim_ids", "verification_state", "qualification_id"}."""
        from .verify.bundle import validate_bundle, bundle_hash
        try:
            authority.require(actor, "ingest_verification")
            validate_bundle(bundle)
            if bundle["manifest_hash"] != expected_manifest_hash:
                raise ValidationError("bundle was produced under a different trusted manifest", code="manifest_mismatch")
            for h, data in blobs.items():
                if hashlib.sha256(data).hexdigest() != h:
                    raise ValidationError("evidence blob does not match its hash", code="blob_mismatch")
            needed = {h for r in bundle["checks"] for h in r["evidence"]}
            if not needed <= set(blobs):
                raise ValidationError("bundle references evidence it did not deliver", code="missing_blob")
        except VanillaError as e:
            self._record_rejection(actor, "ingest_verification", bundle.get("task_id") if isinstance(bundle, dict) else None, e)
            raise
        bh = bundle_hash(bundle)
        for h, data in blobs.items():
            self.evidence.put(data)                      # staged, content-addressed, never overwrites

        def fn(c):
            t = self._task(c, bundle["task_id"])
            if t["verification_state"] != "RUNNING":
                raise state.IllegalTransition("verification must be RUNNING to ingest a bundle (is %s)" % t["verification_state"], code="not_running")
            if c.execute("SELECT 1 FROM verification_runs WHERE run_id=?", (bundle["run_id"],)).fetchone():
                raise ValidationError("this run was already ingested", code="run_exists")
            for h, data in blobs.items():
                if c.execute("SELECT 1 FROM evidence WHERE evidence_id=?", (h,)).fetchone() is None:
                    c.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?)", (h, len(data), "application/octet-stream", "%s/%s" % (h[:2], h), self._ts(), actor.id))
            claim_ids = []
            for r in bundle["checks"]:
                crit = c.execute("SELECT criterion_id FROM criteria WHERE task_id=? AND check_id=?", (bundle["task_id"], r["check_id"])).fetchall()
                for row in crit:
                    claim_ids.append(self._claim_tx(c, {
                        "task_id": bundle["task_id"], "criterion_id": row["criterion_id"], "check_id": r["check_id"], "run_id": bundle["run_id"], "status": r["status"],
                        "expected": r["expected"], "observed": r["observed"], "evidence_refs": r["evidence"], "verifier_id": bundle["verifier_id"],
                        "verifier_version": bundle["verifier_version"], "source_commit": bundle["source_commit"], "env_fingerprint": bundle["env_fingerprint"],
                        "repeatability_class": r["repeatability"]}, method="deterministic", bundle_hash=bh)[0])
            c.execute("INSERT INTO verification_runs(run_id, task_id, attempt_id, trusted_manifest_hash, started_at, ended_at, summary_json, patch_hash, env_fingerprint, outcome, bundle_hash) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (bundle["run_id"], bundle["task_id"], None, bundle["manifest_hash"], int(bundle["started_at"] * 1_000_000), int(bundle["ended_at"] * 1_000_000),
                       _canon({"diff_findings": bundle["diff_findings"], "not_verified": bundle["not_verified"]}), bundle["patch_hash"], bundle["env_fingerprint"], bundle["outcome"], bh))
            ok, det = ledger.evaluate(c, bundle["task_id"])
            qid, new_state = None, None
            blocking = any(f.get("blocking") for f in bundle["diff_findings"])
            if ok and not blocking:
                qid = self._qualify(c, self._task(c, bundle["task_id"]))
                new_state = "VERIFIED"
            else:
                statuses = {d["status"] for d in det["criteria"] if d["required"]}
                new_state = "FAILED" if (blocking or "FAILED" in statuses) else "FLAKY" if "FLAKY" in statuses else "INCONCLUSIVE"
            state.require("verification", "RUNNING", new_state)
            self._touch(c, bundle["task_id"], verification_state=new_state, **({"qualification_id": qid} if qid else {}))
            return {"run_id": bundle["run_id"], "claim_ids": claim_ids, "verification_state": new_state, "qualification_id": qid}, {
                "run_id": bundle["run_id"], "bundle_hash": bh, "verifier": bundle["verifier_id"], "claims": len(claim_ids), "verification_state": new_state,
                "manifest_hash": bundle["manifest_hash"]}
        return self._run(actor, "ingest_verification", bundle["task_id"], "verification_ingested", fn)

    def _bad_evidence(self, hashes):
        """Re-hash every referenced artifact; returns {hash: 'missing'|'tampered'}."""
        bad = {}
        for h in hashes:
            try:
                self.evidence.read(h)
            except EvidenceMissing:
                bad[h] = "missing"
            except EvidenceTampered:
                bad[h] = "tampered"
        return bad

    def revalidate_evidence(self, actor, task_id):
        """Re-hash the evidence of the task's current claims; claims with missing or altered evidence are invalidated (never repaired)."""
        def fn(c):
            self._task(c, task_id)
            invalidated, info = [], {}
            for cl in ledger.current_claims(c, task_id):
                bad = self._bad_evidence(ledger.canonical_list(cl["evidence_refs_json"]))
                if bad:
                    c.execute("INSERT INTO claim_invalidations VALUES(?,?,?,?,?)",
                              (new_id("inv"), cl["claim_id"], "evidence_" + "_".join(sorted(set(bad.values()))), actor.id, self._ts()))
                    invalidated.append(cl["claim_id"])
            if invalidated:
                info = self._stale_task(c, task_id, "evidence_invalid")
            return invalidated, {"invalidated_claims": invalidated, "effects": info}
        return self._run(actor, "revalidate_evidence", task_id, "evidence_revalidated", fn)

    # ------------------------------------------------------------ leases and fencing
    def create_lease(self, actor, task_id, worker_id, resource_type, resource_key, ttl=3600):
        def fn(c):
            self._require_open_task(c, task_id)
            if c.execute("SELECT 1 FROM leases WHERE resource_type=? AND resource_key=? AND state IN ('held','release_pending')", (resource_type, resource_key)).fetchone():
                raise LeaseBusy("%s:%s is already leased" % (resource_type, resource_key))
            c.execute("INSERT INTO fence_counters VALUES(?,?,1) ON CONFLICT(resource_type, resource_key) DO UPDATE SET last_token=last_token+1", (resource_type, resource_key))
            tok = c.execute("SELECT last_token FROM fence_counters WHERE resource_type=? AND resource_key=?", (resource_type, resource_key)).fetchone()[0]
            lid, now = new_id("lse"), self._ts()
            c.execute("INSERT INTO leases VALUES(?,?,?,?,?,?,?,?,?)", (lid, resource_type, resource_key, task_id, worker_id, tok, "held", now, now + int(ttl * 1_000_000)))
            return {"lease_id": lid, "fencing_token": tok}, {"lease_id": lid, "resource": "%s:%s" % (resource_type, resource_key), "fencing_token": tok}
        return self._run(actor, "create_lease", task_id, "lease_created", fn)

    def require_fence(self, resource_type, resource_key, token):
        """vanilla-agents-controlled operations must present their token; an older token is rejected. (External processes cannot be fenced this way.)"""
        self._check_fence(self.conn, (resource_type, resource_key, token))
        return True

    def _check_fence(self, c, fence, task_id=None, worker_id=None):
        """fence=None means unfenced (callers that hold no lease). Otherwise (resource_type, resource_key, token) must be the current, unexpired,
        held lease and, when task_id is given, held by that task (D-048). The token must be a real int (D-102) and, when worker_id is
        given and the lease records a holder worker, that worker must be the holder (D-103)."""
        if fence is None:
            return True
        try:
            resource_type, resource_key, token = fence
        except (TypeError, ValueError):
            raise StaleFence("fence must be (resource_type, resource_key, token)")
        if type(token) is not int:
            raise StaleFence("fencing token must be an int, got %s" % type(token).__name__)
        r = c.execute("SELECT last_token FROM fence_counters WHERE resource_type=? AND resource_key=?", (resource_type, resource_key)).fetchone()
        if r is None or token < r["last_token"]:
            raise StaleFence("fencing token %s is older than the current token" % token)
        held = c.execute("SELECT lease_id, holder_task_id, holder_worker_id, expires_at FROM leases WHERE resource_type=? AND resource_key=? AND fencing_token=? AND state='held'",
                         (resource_type, resource_key, token)).fetchone()
        if held is None:
            raise StaleFence("no held lease carries token %s" % token)
        if held["expires_at"] <= self._ts():
            raise StaleFence("lease for token %s has expired" % token)
        if task_id is not None and held["holder_task_id"] != task_id:
            raise StaleFence("lease for token %s belongs to another task" % token)
        if worker_id is not None and held["holder_worker_id"] is not None and held["holder_worker_id"] != worker_id:
            raise StaleFence("lease for token %s is held by another worker" % token)
        return held

    def renew_lease(self, actor, fence, ttl, worker_id=None):
        """Fenced renewal (D-100): extends expires_at to now+ttl only for the current, held, unexpired lease named by the fence.
        Controller-only; workers renew through worker_heartbeat(lease_ttl=). Stale, released or expired tokens are rejected (and audited) without change."""
        def fn(c):
            held = self._check_fence(c, fence, None, worker_id)
            if held is True:
                raise StaleFence("renew_lease needs a fence")
            if type(ttl) not in (int, float) or ttl <= 0:
                raise ValidationError("ttl must be a positive number")
            c.execute("UPDATE leases SET expires_at=MAX(expires_at, ?) WHERE lease_id=?", (self._ts() + int(ttl * 1_000_000), held["lease_id"]))
            return None, {"lease_id": held["lease_id"], "fencing_token": fence[2], "ttl": ttl}
        return self._run(actor, "renew_lease", self._lease_task(fence), "lease_renewed", fn)

    def _lease_task(self, fence):
        try:
            r = self.conn.execute("SELECT holder_task_id FROM leases WHERE resource_type=? AND resource_key=? AND fencing_token=?", tuple(fence)).fetchone()
        except Exception:
            return None
        return r[0] if r else None

    def _lease_task_by_id(self, lease_id):
        r = self.conn.execute("SELECT holder_task_id FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
        return r[0] if r else None

    def request_lease_release(self, actor, lease_id):
        def fn(c):
            lse = c.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
            if lse is None:
                raise VanillaError("lease not found", code="not_found")
            state.require("lease", lse["state"], "release_pending")
            c.execute("UPDATE leases SET state='release_pending' WHERE lease_id=?", (lease_id,))
            return None, {"lease_id": lease_id}
        return self._run(actor, "release_lease", self._lease_task_by_id(lease_id), "lease_release_requested", fn)

    def confirm_lease_release(self, actor, lease_id):
        """release_pending -> released only after the holder worker is confirmed terminated (or there is none)."""
        def fn(c):
            lse = c.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
            if lse is None:
                raise VanillaError("lease not found", code="not_found")
            state.require("lease", lse["state"], "released")
            if lse["holder_worker_id"]:
                w = c.execute("SELECT state FROM workers WHERE worker_id=?", (lse["holder_worker_id"],)).fetchone()
                if w and w["state"] not in state.TERMINATED_WORKER_STATES:
                    raise state.IllegalTransition("holder worker is %s, not confirmed terminated" % w["state"], code="holder_not_terminated")
            c.execute("UPDATE leases SET state='released' WHERE lease_id=?", (lease_id,))
            return None, {"lease_id": lease_id}
        return self._run(actor, "release_lease", self._lease_task_by_id(lease_id), "lease_released", fn)

    # ------------------------------------------------------------ approvals and release
    def request_approval(self, actor, task_id, concept, operation, scope, tier=2, expires_at=None, target_ref=None, policy_hash=None, qualification_id=None):
        def fn(c):
            if concept not in ("action", "integration"):
                raise ValidationError("unknown approval concept")
            self._task(c, task_id)          # raises when the task does not exist
            if actor.type == Principal.WORKER and c.execute("SELECT 1 FROM attempts WHERE task_id=? AND worker_id=?", (task_id, actor.id)).fetchone() is None:
                raise AuthorityError("a worker may only request approvals for tasks it has an attempt on")
            if concept == "integration" and not (target_ref and policy_hash and qualification_id):
                raise ValidationError("integration approvals must name target, policy hash and qualification")
            eff_tier = authority.derive_tier(concept, operation, tier)
            now_s = self.clock()
            eff_expires = min(expires_at if expires_at is not None else now_s + 3600, now_s + authority.MAX_APPROVAL_SECONDS)
            aid = new_id("apr")
            c.execute("INSERT INTO approvals(approval_id, task_id, concept, operation, scope_json, target_ref, policy_hash, qualification_id, tier, requested_at, requested_by, expires_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (aid, task_id, concept, operation, _canon(scope), target_ref, policy_hash, qualification_id, eff_tier, self._ts(), actor.id,
                       int(eff_expires * 1_000_000)))
            return aid, {"approval_id": aid, "concept": concept, "operation": operation, "tier": tier}
        return self._run(actor, "request_approval", task_id, "approval_requested", fn)

    def decide_approval(self, actor, approval_id, decision):
        def fn(c):
            a = c.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if a is None:
                raise VanillaError("approval not found", code="not_found")
            authority.require_decider(actor, a["tier"])
            if decision not in ("approved", "denied"):
                raise ValidationError("decision must be approved or denied")
            if a["decision"] is not None:
                raise state.IllegalTransition("approval already decided", code="already_decided")
            if decision == "approved" and a["concept"] == "integration":
                t = self._task(c, a["task_id"])
                if (a["target_ref"], a["policy_hash"], a["qualification_id"]) != (t["target_ref"], t["policy_hash"], t["qualification_id"]):
                    raise ValidationError("approval no longer matches the task's current target, policy or qualification", code="binding_mismatch")
            c.execute("UPDATE approvals SET decision=?, decided_at=?, decided_by=? WHERE approval_id=?", (decision, self._ts(), actor.id, approval_id))
            return None, {"approval_id": approval_id, "decision": decision, "decided_by": actor.id}
        return self._run(actor, "decide_approval", None, "approval_decided", fn)

    def _binding_row(self, a):
        d = dict(a)
        d["expires_at"] = a["expires_at"] / 1_000_000
        return d

    def _release_use(self, task):
        return {"concept": "integration", "operation": "integrate", "scope_json": _canon({"task_id": task["task_id"]}), "target_ref": task["target_ref"],
                "policy_hash": task["policy_hash"], "qualification_id": task["qualification_id"]}

    def _check_release(self, c, task_id, approval_id):
        t = self._task(c, task_id)
        a = c.execute("SELECT * FROM approvals WHERE approval_id=? AND task_id=?", (approval_id, task_id)).fetchone()
        if a is None:
            raise VanillaError("approval not found for this task", code="not_found")
        if t["verification_state"] != "VERIFIED" or t["qualification_id"] is None:
            raise state.IllegalTransition("release needs verification VERIFIED with a current qualification (is %s)" % t["verification_state"], code="not_verified")
        reason = authority.check_binding(self._binding_row(a), self._release_use(t), self.clock())
        if reason:
            raise AuthorityError("approval cannot be used: %s" % reason, code=reason)
        q = c.execute("SELECT claim_ids_json FROM qualifications WHERE qualification_id=?", (t["qualification_id"],)).fetchone()
        refs = set()
        for cid in json.loads(q["claim_ids_json"]):
            refs.update(json.loads(c.execute("SELECT evidence_refs_json FROM claims WHERE claim_id=?", (cid,)).fetchone()[0]))
        bad = self._bad_evidence(refs)
        if bad:
            raise state.IllegalTransition("qualification evidence is missing or altered: %s" % sorted(bad), code="evidence_invalid")
        return t, a

    def approve_release(self, actor, task_id, approval_id):
        def fn(c):
            t, a = self._check_release(c, task_id, approval_id)
            if t["verification_state"] != "VERIFIED":
                raise state.IllegalTransition("release needs verification VERIFIED (is %s)" % t["verification_state"], code="not_verified")
            state.require("release", t["release_state"], "APPROVED")
            self._touch(c, task_id, release_state="APPROVED")
            return None, {"approval_id": approval_id, "target_ref": t["target_ref"], "qualification_id": t["qualification_id"]}
        return self._run(actor, "approve_release", task_id, "release_approved", fn)

    def apply_release(self, actor, task_id, approval_id):
        def fn(c):
            t, a = self._check_release(c, task_id, approval_id)
            state.require("release", t["release_state"], "APPLIED")
            c.execute("UPDATE approvals SET consumed_at=? WHERE approval_id=?", (self._ts(), approval_id))
            self._touch(c, task_id, release_state="APPLIED")
            return None, {"approval_id": approval_id, "target_ref": t["target_ref"]}
        return self._run(actor, "apply_release", task_id, "release_applied", fn)

    def revoke_release(self, actor, task_id):
        def fn(c):
            t = self._task(c, task_id)
            state.require("release", t["release_state"], "REVOKED")
            self._touch(c, task_id, release_state="REVOKED")
            return None, {"from": t["release_state"]}
        return self._run(actor, "revoke_release", task_id, "release_revoked", fn)

    def use_action_approval(self, actor, task_id, approval_id, operation, scope):
        """Consume a single-use ACTION approval (bound to operation, scope and expiry)."""
        def fn(c):
            a = c.execute("SELECT * FROM approvals WHERE approval_id=? AND task_id=?", (approval_id, task_id)).fetchone()
            if a is None:
                raise VanillaError("approval not found for this task", code="not_found")
            reason = authority.check_binding(self._binding_row(a), {"concept": "action", "operation": operation, "scope_json": _canon(scope),
                                                                    "target_ref": None, "policy_hash": None, "qualification_id": None}, self.clock())
            if reason:
                raise AuthorityError("approval cannot be used: %s" % reason, code=reason)
            c.execute("UPDATE approvals SET consumed_at=? WHERE approval_id=?", (self._ts(), approval_id))
            return None, {"approval_id": approval_id, "operation": operation}
        return self._run(actor, "apply_release", task_id, "action_approval_used", fn)

    # ------------------------------------------------------------ verification of the store itself
    def verify_chain(self):
        r = audit.verify_chain(self.conn)
        r["checkpoints"] = audit.classify_checkpoints(self.conn, str(self.paths["checkpoints"]), self.interval)
        return r
