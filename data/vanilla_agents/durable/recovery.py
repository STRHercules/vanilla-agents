"""Orphan handling shared by restart recovery and the watchdog.

An orphaned task has lost its worker (controller restart, expired lease, silent worker). Everything is moved to a recoverable state step by
step, and every step checks the current state first, so repeating it changes nothing and writes no events (idempotent, D-051).
The controller cannot prove a worker process is dead by itself: `is_terminated(worker_id) -> bool` is supplied by the caller (default: never).
Until it says yes the workers stay termination_requested, leases stay release_pending and the task stays BLOCKED(worker_unreachable).
"""
from ..authority import Actor
from ..contracts import IllegalTransition, Principal
from ..state import EXEC_TERMINAL

CTL = Actor(Principal.CONTROLLER, "controller")
REASON = "worker_unreachable"
DEFAULT_MAX_ATTEMPTS = 3   # a task may be orphaned this many times; the last one blocks it for good (D-060)
IN_FLIGHT = ("LEASING", "RUNNING")   # states a watchdog expects to have a live worker


def _workers(c, task_id):
    return [r[0] for r in c.execute("SELECT worker_id FROM attempts WHERE task_id=? AND worker_id IS NOT NULL UNION "
                                    "SELECT holder_worker_id FROM leases WHERE holder_task_id=? AND holder_worker_id IS NOT NULL ORDER BY 1", (task_id, task_id))]


def orphan_task(svc, task_id, max_attempts=DEFAULT_MAX_ATTEMPTS):
    """Fence off a task whose worker is gone. Returns the number of changes made (0 when already done)."""
    c, n = svc.conn, 0
    t = c.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if t is None or t["cancel_state"] != "NONE":   # cancellation owns its own lifecycle (D-030)
        return 0
    for a in c.execute("SELECT attempt_id, state FROM attempts WHERE task_id=? AND state IN ('PLANNED','ACTIVE') ORDER BY number", (task_id,)).fetchall():
        svc.transition_attempt(CTL, a["attempt_id"], "CANCELLED" if a["state"] == "PLANNED" else "LOST", failure_class="orphaned")
        n += 1
    for wid in _workers(c, task_id):
        w = c.execute("SELECT state FROM workers WHERE worker_id=?", (wid,)).fetchone()["state"]
        if w in ("starting", "alive", "unreachable"):
            svc.set_worker_state(CTL, wid, "termination_requested", task_id=task_id)
            n += 1
    for lse in c.execute("SELECT lease_id FROM leases WHERE holder_task_id=? AND state='held' ORDER BY lease_id", (task_id,)).fetchall():
        svc.request_lease_release(CTL, lse["lease_id"])
        n += 1
    if t["execution_state"] not in EXEC_TERMINAL and t["execution_state"] != "BLOCKED":   # any live state, not only IN_FLIGHT (D-105)
        past = c.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='task_blocked' AND json_extract(payload_json,'$.reason')=?", (task_id, REASON)).fetchone()[0]
        try:
            svc.block_task(CTL, task_id, "retries_exhausted" if max_attempts and past + 1 >= max_attempts else REASON)
            n += 1
        except IllegalTransition:   # state moved under us (D-101): fine if it is now blocked or finished, else surface it
            if svc._task(c, task_id)["execution_state"] not in EXEC_TERMINAL | {"BLOCKED"}:
                raise
    return n


def settle(svc, is_terminated=None):
    """Confirm terminations the caller can prove, release those workers' leases, requeue tasks that are fully clear. Returns changes made."""
    is_terminated = is_terminated or (lambda wid: False)
    c, n = svc.conn, 0
    for w in c.execute("SELECT worker_id FROM workers WHERE state='termination_requested' ORDER BY worker_id").fetchall():
        if is_terminated(w["worker_id"]):
            svc.set_worker_state(CTL, w["worker_id"], "termination_confirmed",
                                 proof={"is_terminated": True, "source": getattr(is_terminated, "__qualname__", type(is_terminated).__name__)})
            n += 1
    for lse in c.execute("SELECT l.lease_id FROM leases l LEFT JOIN workers w ON w.worker_id=l.holder_worker_id WHERE l.state='release_pending' "
                         "AND (w.worker_id IS NULL OR w.state IN ('termination_confirmed','gone')) ORDER BY l.lease_id").fetchall():
        svc.confirm_lease_release(CTL, lse["lease_id"])
        n += 1
    for t in c.execute("SELECT task_id FROM tasks WHERE execution_state='BLOCKED' AND blocked_reason=? AND cancel_state='NONE' ORDER BY created_at, task_id", (REASON,)).fetchall():
        tid = t["task_id"]
        if svc._task_worker_state(c, tid) is None and not c.execute("SELECT 1 FROM leases WHERE holder_task_id=? AND state IN ('held','release_pending')", (tid,)).fetchone():
            svc.transition_execution(CTL, tid, "QUEUED")
            n += 1
    return n


def recover(svc, is_terminated=None, max_attempts=DEFAULT_MAX_ATTEMPTS):
    """Restart recovery: every task with anything in flight is orphaned (a new controller owns none of the old workers), then settled.

    Deterministic (tasks processed in created_at, task_id order) and idempotent. Returns {"orphaned": [...], "changes": n}."""
    c = svc.conn
    rows = c.execute("SELECT task_id FROM tasks WHERE cancel_state='NONE' AND (execution_state IN ('LEASING','RUNNING') "
                     "OR EXISTS(SELECT 1 FROM leases WHERE holder_task_id=tasks.task_id AND state='held') "
                     "OR EXISTS(SELECT 1 FROM attempts WHERE task_id=tasks.task_id AND state IN ('PLANNED','ACTIVE'))) ORDER BY created_at, task_id").fetchall()
    changes, touched = 0, []
    for r in rows:
        k = orphan_task(svc, r["task_id"], max_attempts)
        if k:
            touched.append(r["task_id"])
            changes += k
    return {"orphaned": touched, "changes": changes + settle(svc, is_terminated)}
