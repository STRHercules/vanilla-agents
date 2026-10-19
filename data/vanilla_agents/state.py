"""Pure transition tables and guards (no I/O). Terminal states have no exits."""
from .contracts import IllegalTransition

EXECUTION = {
    "QUEUED": {"LEASING", "BLOCKED"},
    "LEASING": {"RUNNING", "BLOCKED", "QUEUED"},
    "RUNNING": {"FINISHED", "FAILED", "BLOCKED"},
    "BLOCKED": {"QUEUED"},
    "FINISHED": set(), "FAILED": set(), "CANCELLED": set(),
}
# CANCELLED is reachable only through the cancellation lifecycle (see can_finish_cancel), from any non-terminal state.
EXEC_TERMINAL = {"FINISHED", "FAILED", "CANCELLED"}
CANCEL = {"NONE": {"REQUESTED"}, "REQUESTED": {"TERMINATING", "NONE"}, "TERMINATING": {"CONFIRMED"}, "CONFIRMED": set()}
WORKER = {
    "starting": {"alive", "termination_requested"},
    "alive": {"unreachable", "termination_requested"},
    "unreachable": {"alive", "termination_requested"},
    "termination_requested": {"termination_confirmed"},
    "termination_confirmed": {"gone"},
    "gone": set(),
}
TERMINATED_WORKER_STATES = {"termination_confirmed", "gone"}
VERIFICATION = {
    "NOT_RUN": {"RUNNING"},
    "RUNNING": {"VERIFIED", "FAILED", "INCONCLUSIVE", "FLAKY"},
    "VERIFIED": {"STALE"},
    "STALE": {"RUNNING"},
    "FAILED": {"RUNNING"}, "INCONCLUSIVE": {"RUNNING"}, "FLAKY": {"RUNNING"},
}
RELEASE = {"NOT_APPROVED": {"APPROVED"}, "APPROVED": {"APPLIED", "REVOKED"}, "APPLIED": set(), "REVOKED": {"APPROVED"}}
ATTEMPT = {"PLANNED": {"ACTIVE", "CANCELLED"}, "ACTIVE": {"SUCCEEDED", "FAILED", "LOST", "CANCELLED"},
           "SUCCEEDED": set(), "FAILED": set(), "LOST": set(), "CANCELLED": set()}
LEASE = {"held": {"release_pending"}, "release_pending": {"released"}, "released": set()}

TABLES = {"execution": EXECUTION, "cancel": CANCEL, "worker": WORKER, "verification": VERIFICATION, "release": RELEASE,
          "attempt": ATTEMPT, "lease": LEASE}


def can(table, old, new):
    return new in TABLES[table].get(old, ())


def require(table, old, new):
    """Raise IllegalTransition unless `old -> new` is listed in the table."""
    if old not in TABLES[table]:
        raise IllegalTransition("%s: unknown state %r" % (table, old))
    if not can(table, old, new):
        raise IllegalTransition("%s: %s -> %s is not allowed" % (table, old, new))


def require_cancelled(exec_state, cancel_state, worker_state, open_leases):
    """CANCELLED needs: non-terminal task, cancel_state CONFIRMED, worker terminated (or none), no unreconciled leases."""
    if exec_state in EXEC_TERMINAL:
        raise IllegalTransition("execution: %s is terminal" % exec_state)
    if cancel_state != "CONFIRMED":
        raise IllegalTransition("cancel not confirmed (cancel_state=%s)" % cancel_state)
    if worker_state is not None and worker_state not in TERMINATED_WORKER_STATES:
        raise IllegalTransition("worker not confirmed terminated (state=%s)" % worker_state)
    if open_leases:
        raise IllegalTransition("%d lease(s) not reconciled" % open_leases)
