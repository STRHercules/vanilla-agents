"""Rebuild events and current state from the durable database (read-only connection) so the UI can start from truth, then follow the feed.
Derived event ids are audit sequence numbers, a different numbering from feed ids; never mix the two cursors."""
import json

from ..storage import connect_ro
from .redact_util import clean
from .schema import Event


def _data(row):
    try:
        d = json.loads(row["payload_json"])
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _map(c, row, titles):
    """One audit row -> zero or one (type, agent, data)."""
    k, p, task = row["kind"], _data(row), row["task_id"]
    if k == "task_created":
        titles[p.get("task_id")] = p.get("title", "")
        return "task_created", None, {"title": p.get("title", "")}
    if k == "execution_changed" and p.get("to") == "QUEUED":
        return "task_ready", None, {"title": titles.get(task, "")}
    if k == "task_blocked":
        return "task_blocked", None, {"title": titles.get(task, ""), "reason": str(p.get("reason", ""))}
    if k == "verification_changed":
        to = p.get("to")
        if to == "RUNNING":
            return "check_started", None, {"check": "verification"}
        if to == "VERIFIED":
            return "task_verified", None, {"title": titles.get(task, "")}
        if to == "FAILED":
            return "task_failed", None, {"title": titles.get(task, "")}
        return "check_finished", None, {"check": "verification", "verdict": str(to)}
    if k == "claim_added":
        r = c.execute("SELECT check_id FROM claims WHERE claim_id=?", (p.get("claim_id"),)).fetchone()
        return "check_finished", None, {"check": r["check_id"] if r else "check", "verdict": str(p.get("status", ""))}
    if k == "worker_state_changed":
        agent = p.get("worker_id")
        if p.get("to") == "alive":
            return "agent_started", agent, {}
        if p.get("to") in ("termination_requested", "termination_confirmed", "gone"):
            return "agent_idle", agent, {}
    if k == "lease_created":
        r = c.execute("SELECT expires_at FROM leases WHERE lease_id=?", (p.get("lease_id"),)).fetchone()
        data = {"resource": str(p.get("resource", ""))}
        if r:
            data["expires_at"] = r["expires_at"] / 1_000_000       # D-410: the lock timer shows the engine's own expiry, never a guess
        return "lease_acquired", None, data
    if k == "lease_released":
        r = c.execute("SELECT resource_type, resource_key FROM leases WHERE lease_id=?", (p.get("lease_id"),)).fetchone()
        return "lease_expired", None, {"resource": "%s:%s" % (r["resource_type"], r["resource_key"]) if r else str(p.get("lease_id", ""))}
    return None


def derive_events(db_path, after_seq=0, limit=5000):
    """Audit events with seq > after_seq mapped onto the UI schema (events with no UI meaning are skipped)."""
    c = connect_ro(db_path)
    try:
        titles = {r["task_id"]: r["title"] for r in c.execute("SELECT task_id, title FROM tasks")}
        out = []
        for row in c.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after_seq, limit)):
            m = _map(c, row, titles)
            if m:
                t, agent, data = m
                out.append(Event(row["seq"], row["ts"] / 1_000_000, t, row["task_id"], agent, {k: clean(v) for k, v in data.items()}))
        return out
    finally:
        c.close()


def current_state(db_path):
    """Present-tense picture: tasks, workers, held leases. Plain dicts, safe to hand to the UI."""
    c = connect_ro(db_path)
    try:
        return {
            "tasks": [dict(task_id=r["task_id"], title=clean(r["title"]), execution=r["execution_state"], verification=r["verification_state"],
                           blocked_reason=clean(r["blocked_reason"])) for r in c.execute("SELECT * FROM tasks ORDER BY created_at, task_id")],
            "workers": [dict(worker_id=r["worker_id"], state=r["state"]) for r in c.execute("SELECT * FROM workers ORDER BY worker_id")],
            "leases": [dict(lease_id=r["lease_id"], resource="%s:%s" % (r["resource_type"], r["resource_key"]), task_id=r["holder_task_id"],
                            expires_at=r["expires_at"] / 1_000_000) for r in c.execute("SELECT * FROM leases WHERE state='held' ORDER BY lease_id")],
        }
    finally:
        c.close()
