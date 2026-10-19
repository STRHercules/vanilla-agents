"""Claims ledger computations. Claim status is COMPUTED from append-only rows; nothing here edits history."""
import hashlib

from .audit import canonical


def current_claims(conn, task_id):
    """Claims with no supersession and no invalidation."""
    return conn.execute(
        "SELECT * FROM claims c WHERE c.task_id=? "
        "AND NOT EXISTS (SELECT 1 FROM claim_supersessions s WHERE s.claim_id=c.claim_id) "
        "AND NOT EXISTS (SELECT 1 FROM claim_invalidations i WHERE i.claim_id=c.claim_id) ORDER BY c.claim_id", (task_id,)).fetchall()


def evaluate(conn, task_id):
    """A task may be VERIFIED only if every required criterion has a current VERIFIED claim. Missing evidence is NOT_VERIFIED, never a pass.

    Returns (ok, details) where details lists per-criterion status and the claim ids / evidence hashes used.
    """
    cur = current_claims(conn, task_id)
    by_crit = {}
    for c in cur:
        by_crit.setdefault(c["criterion_id"], []).append(c)
    details, ok, claim_ids, evidence = [], True, [], []
    if conn.execute("SELECT COUNT(*) FROM criteria WHERE task_id=? AND required=1", (task_id,)).fetchone()[0] == 0:
        return False, {"criteria": [], "claim_ids": [], "evidence": [], "reason": "no_required_criteria"}
    for crit in conn.execute("SELECT criterion_id, required, check_id FROM criteria WHERE task_id=? ORDER BY criterion_id", (task_id,)):
        claims = by_crit.get(crit["criterion_id"], [])
        if not claims:
            status = "NOT_VERIFIED"
        elif all(c["status"] == "VERIFIED" for c in claims):
            status = "VERIFIED"
            claim_ids += [c["claim_id"] for c in claims]
            for c in claims:
                evidence += canonical_list(c["evidence_refs_json"])
        else:
            status = sorted({c["status"] for c in claims if c["status"] != "VERIFIED"})[0]
        if crit["required"] and status != "VERIFIED":
            ok = False
        details.append({"criterion_id": crit["criterion_id"], "required": bool(crit["required"]), "status": status})
    return ok, {"criteria": details, "claim_ids": sorted(claim_ids), "evidence": sorted(set(evidence))}


def canonical_list(json_text):
    import json
    return list(json.loads(json_text))


def evidence_set_hash(claim_ids, evidence_hashes):
    return hashlib.sha256(canonical({"claims": sorted(claim_ids), "evidence": sorted(evidence_hashes)}).encode("utf-8")).hexdigest()
