"""Concise CLI. Everything except `init` opens the database READ-ONLY; no command can approve or advance a workflow (Stage 1)."""
import argparse
import json
import sys

from . import audit
from .contracts import VanillaError
from .lock import ControllerLock
from .storage import connect_ro, init_home, layout


_OPEN = []  # read-only connections opened by this invocation; closed by main()


def _ro(home):
    p = layout(home)["db"]
    if not p.exists():
        raise VanillaError("no database at %s (run `vanilla-agents init --home <path>`)" % p, code="no_database")
    conn = connect_ro(p)
    _OPEN.append(conn)
    return conn


def cmd_init(a):
    paths = init_home(a.home)
    print("initialised %s" % paths["home"])
    for k in ("db", "checkpoints", "backups", "evidence"):
        print("  %-12s %s" % (k, paths[k]))
    return 0


def cmd_status(a):
    c = _ro(a.home)
    p = layout(a.home)
    print("schema_version:", c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
    for col in ("execution_state", "verification_state", "release_state"):
        rows = c.execute("SELECT %s, COUNT(*) FROM tasks GROUP BY 1" % col).fetchall()
        print("%-19s %s" % (col, dict((r[0], r[1]) for r in rows) or "-"))
    print("events:", c.execute("SELECT COUNT(*) FROM events").fetchone()[0], " last seq:", c.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])
    print("checkpoints:", audit.classify_checkpoints(c, str(p["checkpoints"]))["status"])
    print("controller owner record (informational, does not prove a live controller):", ControllerLock.read_info(p["lock"]) or "none")
    return 0


def cmd_tasks(a):
    c = _ro(a.home)
    for r in c.execute("SELECT task_id, title, execution_state, cancel_state, verification_state, release_state, security_posture FROM tasks ORDER BY task_id"):
        print(" | ".join(str(x) for x in tuple(r)))
    return 0


def cmd_task(a):
    c = _ro(a.home)
    r = c.execute("SELECT * FROM tasks WHERE task_id=?", (a.task_id,)).fetchone()
    if r is None:
        raise VanillaError("task not found", code="not_found")
    print(json.dumps(dict(r), indent=2, sort_keys=True))
    return 0


def cmd_events(a):
    c = _ro(a.home)
    q, args = "SELECT seq, kind, principal_type, principal_id, task_id, payload_json FROM events", ()
    if a.task:
        q, args = q + " WHERE task_id=?", (a.task,)
    for r in c.execute(q + " ORDER BY seq", args):
        print(" | ".join(str(x) for x in tuple(r)))
    return 0


def cmd_claims(a):
    from . import ledger
    c = _ro(a.home)
    current = {r["claim_id"] for r in ledger.current_claims(c, a.task)}
    for r in c.execute("SELECT * FROM claims WHERE task_id=? ORDER BY claim_id", (a.task,)):
        inv = c.execute("SELECT reason FROM claim_invalidations WHERE claim_id=?", (r["claim_id"],)).fetchone()
        sup = c.execute("SELECT superseded_by_claim_id FROM claim_supersessions WHERE claim_id=?", (r["claim_id"],)).fetchone()
        state = "current" if r["claim_id"] in current else ("invalidated: %s" % inv[0] if inv else "superseded by %s" % sup[0])
        print("%s | %s | %s | %s | posture=%s | %s" % (r["claim_id"], r["criterion_id"], r["check_id"], r["status"], r["security_posture"], state))
    return 0


def cmd_verify_chain(a):
    c = _ro(a.home)
    p = layout(a.home)
    r = audit.verify_chain(c)
    cp = audit.classify_checkpoints(c, str(p["checkpoints"]))
    print("chain:", "OK" if r["ok"] else "BROKEN at seq %s (%s)" % (r["break_seq"], r["reason"]), "| events:", r["count"])
    print("checkpoints:", cp["status"], {k: v for k, v in cp.items() if k != "status"})
    print("note: tamper-evident, not tamper-proof (same-user writable)")
    return 0 if r["ok"] and cp["status"] in ("consistent", "not_due") else 1


def cmd_evidence_scan(a):
    from .evidence import EvidenceStore
    c = _ro(a.home)
    known = {r[0] for r in c.execute("SELECT evidence_id FROM evidence")}
    rep = EvidenceStore.__new__(EvidenceStore)
    rep.root, rep.tmp = str(layout(a.home)["evidence"]), str(layout(a.home)["evidence"] / "tmp")
    import os
    if not os.path.isdir(rep.root):
        print("no evidence folder yet")
        return 0
    out = EvidenceStore.scan(rep, known)
    print(json.dumps(out, indent=2))
    return 0 if not (out["missing"] or out["tampered"]) else 1


def build_parser():
    p = argparse.ArgumentParser(prog="vanilla-agents", description="vanilla-agents Stage 1 (read-only inspection plus init)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, extra in (("init", cmd_init, []), ("status", cmd_status, []), ("tasks", cmd_tasks, []), ("task", cmd_task, ["task_id"]),
                            ("events", cmd_events, []), ("claims", cmd_claims, ["task"]), ("verify-chain", cmd_verify_chain, []),
                            ("evidence-scan", cmd_evidence_scan, [])):
        sp = sub.add_parser(name)
        sp.add_argument("--home", required=True)
        for e in extra:
            sp.add_argument(e)
        if name == "events":
            sp.add_argument("--task")
        sp.set_defaults(fn=fn)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except VanillaError as e:
        print("error [%s]: %s" % (e.code, e), file=sys.stderr)
        return 2
    finally:
        while _OPEN:
            _OPEN.pop().close()


if __name__ == "__main__":
    sys.exit(main())
