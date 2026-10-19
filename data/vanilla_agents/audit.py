"""Event hash chain and checkpoints.

TAMPER-EVIDENT, NOT TAMPER-PROOF (D-018): with the database and checkpoint files writable by the same Windows user, an adversarial
process could alter both. A hash proves consistency, not that the original data was trustworthy.
"""
import glob
import hashlib
import json
import os

GENESIS = "0" * 64
CHECKPOINT_INTERVAL = 100


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def event_hash(event_id, seq, ts, ptype, pid, task_id, kind, payload_json, prev_hash):
    """Covers every attribution field (event id, principal, task) so none can be changed without breaking the chain (D-024)."""
    fields = [event_id, seq, ts, ptype, pid, task_id, kind, payload_json, prev_hash]   # JSON array: no field separator can be confused with content
    return hashlib.sha256(json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def verify_chain(conn):
    """Recompute the whole chain. Returns dict(ok, count, last_seq, last_hash, break_seq, reason)."""
    prev, expect, count = GENESIS, 1, 0
    for row in conn.execute("SELECT event_id, seq, ts, principal_type, principal_id, task_id, kind, payload_json, prev_hash, hash FROM events ORDER BY seq"):
        eid, seq, ts, pt, pid, tid, kind, payload, ph, h = tuple(row)
        if seq != expect:
            return {"ok": False, "count": count, "break_seq": expect, "reason": "sequence gap or reorder (found seq %d, expected %d)" % (seq, expect)}
        if ph != prev:
            return {"ok": False, "count": count, "break_seq": seq, "reason": "prev_hash mismatch"}
        if event_hash(eid, seq, ts, pt, pid, tid, kind, payload, ph) != h:
            return {"ok": False, "count": count, "break_seq": seq, "reason": "hash mismatch (event content changed)"}
        prev, expect, count = h, expect + 1, count + 1
    return {"ok": True, "count": count, "last_seq": count, "last_hash": prev, "break_seq": None, "reason": None}


def write_checkpoint(cp_dir, seq, count, last_hash):
    """Write after the database commit; temp file + atomic replace. Raises OSError on failure (caller must not roll back)."""
    os.makedirs(cp_dir, exist_ok=True)
    final = os.path.join(cp_dir, "cp_%012d.json" % seq)
    tmp = final + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"seq": seq, "event_count": count, "last_hash": last_hash}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, final)
    return final


def read_checkpoints(cp_dir):
    out = []
    for p in sorted(glob.glob(os.path.join(cp_dir, "cp_*.json"))):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            out.append((p, d))
        except (OSError, ValueError):
            out.append((p, None))
    return out


def classify_checkpoints(conn, cp_dir, interval=CHECKPOINT_INTERVAL):
    """Explicit classification of the checkpoint situation.

    consistent: every checkpoint matches its own historical event, and the newest is not overdue.
    not_due: fewer events than one interval and no checkpoint (nothing expected yet).
    missing: events beyond one interval but no checkpoint files.
    stale: newest checkpoint is older than the latest due boundary (a checkpoint was skipped or lost).
    ahead: a checkpoint refers to an event the database does not have (for example a restored older database).
    inconsistent: a checkpoint file is unreadable or disagrees with the event it names.
    """
    maxseq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
    cps = read_checkpoints(cp_dir) if os.path.isdir(cp_dir) else []
    if not cps:
        return {"status": "not_due" if maxseq < interval else "missing", "max_seq": maxseq, "newest_checkpoint": None}
    newest = 0
    status = "consistent"
    for path, d in cps:
        if d is None or not {"seq", "event_count", "last_hash"} <= set(d):
            return {"status": "inconsistent", "max_seq": maxseq, "detail": "unreadable checkpoint %s" % os.path.basename(path)}
        newest = max(newest, d["seq"])
        if d["seq"] > maxseq:
            status = "ahead"
            continue
        row = conn.execute("SELECT hash FROM events WHERE seq=?", (d["seq"],)).fetchone()
        if row is None or row[0] != d["last_hash"] or d["event_count"] != d["seq"]:
            return {"status": "inconsistent", "max_seq": maxseq, "detail": "checkpoint at seq %d disagrees with the event" % d["seq"]}
    if status == "ahead":
        return {"status": "ahead", "max_seq": maxseq, "newest_checkpoint": newest}
    if maxseq - newest >= interval:
        status = "stale"
    return {"status": status, "max_seq": maxseq, "newest_checkpoint": newest}
