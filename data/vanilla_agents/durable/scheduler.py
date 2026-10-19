"""Scheduler: picks the next ready task from stored state, deterministically (oldest created first, task_id breaks ties)."""
from ..contracts import LeaseBusy
from .recovery import CTL


def next_ready(svc):
    """task_id of the next QUEUED task that is not being cancelled, or None."""
    r = svc.conn.execute("SELECT task_id FROM tasks WHERE execution_state='QUEUED' AND cancel_state='NONE' ORDER BY created_at, task_id LIMIT 1").fetchone()
    return r["task_id"] if r else None


def ready_tasks(svc):
    """All QUEUED, non-cancelling task_ids in dispatch order (created_at, task_id)."""
    return [r["task_id"] for r in svc.conn.execute("SELECT task_id FROM tasks WHERE execution_state='QUEUED' AND cancel_state='NONE' ORDER BY created_at, task_id")]


def claim_next(svc, worker_id, resource_type, resource_key, ttl=3600, resource_of=None):
    """Lease the resource for the oldest ready task whose resource is free and move it to LEASING (fenced). None when nothing is ready.

    Rule (D-061): walk ready tasks in (created_at, task_id) order; a task whose resource is busy is skipped, not reordered (it stays first
    in line next call). resource_of(task_id) -> (type, key) gives each task its own resource; default is the fixed one passed in.
    If every ready task is busy, LeaseBusy is raised and nothing changes."""
    busy = None
    for tid in ready_tasks(svc):
        rtype, rkey = resource_of(tid) if resource_of else (resource_type, resource_key)
        try:
            lease = svc.create_lease(CTL, tid, worker_id, rtype, rkey, ttl)
        except LeaseBusy as e:
            busy = e
            continue
        fence = (rtype, rkey, lease["fencing_token"])
        try:
            svc.transition_execution(CTL, tid, "LEASING", fence=fence)
        except BaseException:
            svc.request_lease_release(CTL, lease["lease_id"])
            raise
        return {"task_id": tid, "lease_id": lease["lease_id"], "fence": fence}
    if busy:
        raise busy
    return None


def start_run(svc, claim, worker_id):
    """LEASING -> RUNNING with a fresh ACTIVE attempt, every step fenced. Returns the attempt id."""
    tid, fence = claim["task_id"], claim["fence"]
    att = svc.create_attempt(CTL, tid, worker_id, fence=fence)
    svc.transition_attempt(CTL, att, "ACTIVE", fence=fence)
    svc.transition_execution(CTL, tid, "RUNNING", fence=fence)
    return att
