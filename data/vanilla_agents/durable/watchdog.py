"""Watchdog: finds expired leases and stuck runs using an injectable clock and moves them to a recoverable state (D-050). Never sleeps."""
from . import recovery


class Watchdog:
    def __init__(self, svc, stuck_after, clock=None, is_terminated=None, max_attempts=recovery.DEFAULT_MAX_ATTEMPTS, lease_ttl=None):
        """stuck_after: seconds without a worker heartbeat (RUNNING) or without any task update (LEASING) before a run counts as stuck."""
        self.svc, self.stuck_after, self.clock, self.is_terminated = svc, stuck_after, clock or svc.clock, is_terminated
        self.max_attempts, self.lease_ttl = max_attempts, lease_ttl

    def _now(self):
        return int(self.clock() * 1_000_000)

    def findings(self):
        """[(task_id, kind)] sorted; kind is lease_expired, worker_silent or leasing_stuck. Read-only."""
        c, now, limit = self.svc.conn, self._now(), int(self.stuck_after * 1_000_000)
        found = {}
        for r in c.execute("SELECT holder_task_id FROM leases WHERE state='held' AND expires_at<=?", (now,)):
            found[r[0]] = "lease_expired"
        for r in c.execute("SELECT t.task_id FROM tasks t WHERE t.execution_state='RUNNING' AND t.cancel_state='NONE' AND EXISTS("
                           "SELECT 1 FROM attempts a LEFT JOIN workers w ON w.worker_id=a.worker_id WHERE a.task_id=t.task_id AND a.state='ACTIVE' "
                           "AND COALESCE(w.last_heartbeat, a.started_at) <= ?)", (now - limit,)):
            found.setdefault(r[0], "worker_silent")
        for r in c.execute("SELECT task_id FROM tasks WHERE execution_state='LEASING' AND cancel_state='NONE' AND updated_at<=?", (now - limit,)):
            found.setdefault(r[0], "leasing_stuck")
        for r in c.execute("SELECT t.task_id, t.execution_state FROM tasks t WHERE t.cancel_state='NONE' AND t.execution_state IN ('LEASING','RUNNING') "
                           "AND NOT EXISTS(SELECT 1 FROM leases l WHERE l.holder_task_id=t.task_id AND l.state='held')"):
            found.setdefault(r[0], "no_held_lease")   # half-finished orphan or lost lease (D-101)
        for r in c.execute("SELECT t.task_id FROM tasks t WHERE t.cancel_state='NONE' AND t.execution_state='RUNNING' "
                           "AND NOT EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.task_id AND a.state='ACTIVE')"):
            found.setdefault(r[0], "no_live_attempt")
        for r in c.execute("SELECT t.task_id FROM tasks t WHERE t.cancel_state='NONE' AND t.execution_state IN ('QUEUED','BLOCKED') "
                           "AND EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.task_id AND a.state='ACTIVE')"):
            found.setdefault(r[0], "stray_attempt")
        return sorted(found.items())

    def _renew(self):
        """With lease_ttl set, extend leases of workers whose heartbeat is fresh (D-100). Expired or released leases are never revived."""
        now, limit = self._now(), int(self.stuck_after * 1_000_000)
        for r in self.svc.conn.execute("SELECT l.resource_type, l.resource_key, l.fencing_token, l.holder_worker_id FROM leases l JOIN workers w ON w.worker_id=l.holder_worker_id "
                                       "WHERE l.state='held' AND l.expires_at>? AND w.state='alive' AND w.last_heartbeat>? ORDER BY l.lease_id", (now, now - limit)).fetchall():
            self.svc.renew_lease(recovery.CTL, (r[0], r[1], r[2]), self.lease_ttl, worker_id=r[3])

    def scan(self):
        """Act on the findings, then settle. Idempotent. Returns {"findings": [...], "changes": n}."""
        if self.lease_ttl:
            self._renew()
        found = self.findings()
        n = sum(recovery.orphan_task(self.svc, tid, self.max_attempts) for tid, _ in found)
        return {"findings": found, "changes": n + recovery.settle(self.svc, self.is_terminated)}
