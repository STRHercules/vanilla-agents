"""Forward-only numbered migrations. Each runs in one transaction and is recorded with a checksum of its text."""
import hashlib

APPEND_ONLY = ["events", "evidence", "claims", "claim_invalidations", "claim_supersessions", "qualifications"]

M1 = """
CREATE TABLE projects(project_id TEXT PRIMARY KEY, name TEXT NOT NULL, root_hint TEXT, created_at INTEGER NOT NULL);
CREATE TABLE tasks(
  task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id), title TEXT NOT NULL, description TEXT NOT NULL,
  base_commit TEXT, current_commit TEXT, env_fingerprint TEXT,
  execution_state TEXT NOT NULL, cancel_state TEXT NOT NULL, verification_state TEXT NOT NULL, release_state TEXT NOT NULL,
  blocked_reason TEXT, target_ref TEXT, policy_hash TEXT, qualification_id TEXT,
  budgets_json TEXT NOT NULL, owned_paths_json TEXT NOT NULL, security_posture TEXT NOT NULL,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, version INTEGER NOT NULL);
CREATE TABLE workers(worker_id TEXT PRIMARY KEY, state TEXT NOT NULL, last_heartbeat INTEGER NOT NULL, agent_provider TEXT, agent_version TEXT);
CREATE TABLE attempts(
  attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), number INTEGER NOT NULL, worker_id TEXT REFERENCES workers(worker_id),
  state TEXT NOT NULL, outcome TEXT, failure_class TEXT, started_at INTEGER NOT NULL, ended_at INTEGER, UNIQUE(task_id, number));
CREATE TABLE criteria(criterion_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), text TEXT NOT NULL, required INTEGER NOT NULL, check_id TEXT NOT NULL);
CREATE TABLE evidence(evidence_id TEXT PRIMARY KEY, size INTEGER NOT NULL, media_type TEXT NOT NULL, stored_path TEXT NOT NULL, created_at INTEGER NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE claims(
  claim_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), attempt_id TEXT, criterion_id TEXT NOT NULL, check_id TEXT NOT NULL, run_id TEXT,
  status TEXT NOT NULL, expected TEXT NOT NULL, observed TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, verifier_id TEXT NOT NULL, verifier_version TEXT NOT NULL,
  source_commit TEXT NOT NULL, env_fingerprint TEXT NOT NULL, repeatability_class TEXT NOT NULL, security_posture TEXT NOT NULL, verified_at INTEGER NOT NULL);
CREATE TABLE claim_invalidations(inv_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(claim_id), reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE claim_supersessions(claim_id TEXT PRIMARY KEY REFERENCES claims(claim_id), superseded_by_claim_id TEXT NOT NULL REFERENCES claims(claim_id), created_at INTEGER NOT NULL);
CREATE TABLE verification_runs(run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt_id TEXT, trusted_manifest_hash TEXT, started_at INTEGER NOT NULL, ended_at INTEGER, summary_json TEXT);
CREATE TABLE qualifications(qualification_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), evidence_set_hash TEXT NOT NULL, claim_ids_json TEXT NOT NULL,
  policy_hash TEXT, source_commit TEXT, created_at INTEGER NOT NULL);
CREATE TABLE leases(
  lease_id TEXT PRIMARY KEY, resource_type TEXT NOT NULL, resource_key TEXT NOT NULL, holder_task_id TEXT NOT NULL REFERENCES tasks(task_id),
  holder_worker_id TEXT REFERENCES workers(worker_id), fencing_token INTEGER NOT NULL, state TEXT NOT NULL, acquired_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE fence_counters(resource_type TEXT NOT NULL, resource_key TEXT NOT NULL, last_token INTEGER NOT NULL, PRIMARY KEY(resource_type, resource_key));
CREATE TABLE approvals(
  approval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), concept TEXT NOT NULL, operation TEXT NOT NULL, scope_json TEXT NOT NULL,
  target_ref TEXT, policy_hash TEXT, qualification_id TEXT, tier INTEGER NOT NULL, requested_at INTEGER NOT NULL, requested_by TEXT NOT NULL,
  decided_at INTEGER, decision TEXT, decided_by TEXT, expires_at INTEGER NOT NULL, consumed_at INTEGER);
CREATE TABLE policies(policy_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version INTEGER NOT NULL, body_json TEXT NOT NULL, body_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE events(
  event_id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, ts INTEGER NOT NULL, principal_type TEXT NOT NULL, principal_id TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL,
  payload_json TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE checkpoints(seq INTEGER PRIMARY KEY, last_event_hash TEXT NOT NULL, event_count INTEGER NOT NULL, written_at INTEGER NOT NULL, file_path TEXT NOT NULL);
CREATE INDEX idx_claims_task ON claims(task_id);
CREATE INDEX idx_events_task ON events(task_id);
CREATE INDEX idx_leases_res ON leases(resource_type, resource_key, state);
"""


def _triggers():
    out = []
    for t in APPEND_ONLY:
        out.append("CREATE TRIGGER %s_no_update BEFORE UPDATE ON %s BEGIN SELECT RAISE(ABORT, '%s is append-only'); END;" % (t, t, t))
        out.append("CREATE TRIGGER %s_no_delete BEFORE DELETE ON %s BEGIN SELECT RAISE(ABORT, '%s is append-only'); END;" % (t, t, t))
    out.append("CREATE TRIGGER approvals_no_delete BEFORE DELETE ON approvals BEGIN SELECT RAISE(ABORT, 'approvals cannot be deleted'); END;")
    out.append(
        "CREATE TRIGGER approvals_guard BEFORE UPDATE ON approvals WHEN "
        "old.approval_id IS NOT new.approval_id OR old.task_id IS NOT new.task_id OR old.concept IS NOT new.concept OR old.operation IS NOT new.operation "
        "OR old.scope_json IS NOT new.scope_json OR old.target_ref IS NOT new.target_ref OR old.policy_hash IS NOT new.policy_hash "
        "OR old.qualification_id IS NOT new.qualification_id OR old.tier IS NOT new.tier OR old.requested_at IS NOT new.requested_at "
        "OR old.requested_by IS NOT new.requested_by OR old.expires_at IS NOT new.expires_at "
        "OR (old.decision IS NOT NULL AND new.decision IS NOT old.decision) OR (old.decided_at IS NOT NULL AND new.decided_at IS NOT old.decided_at) "
        "OR (old.decided_by IS NOT NULL AND new.decided_by IS NOT old.decided_by) OR (old.consumed_at IS NOT NULL AND new.consumed_at IS NOT old.consumed_at) "
        "BEGIN SELECT RAISE(ABORT, 'approval columns are immutable once set'); END;")
    return "\n".join(out)


M2 = """
ALTER TABLE claims ADD COLUMN method TEXT NOT NULL DEFAULT 'deterministic';
ALTER TABLE claims ADD COLUMN bundle_hash TEXT;
ALTER TABLE verification_runs ADD COLUMN patch_hash TEXT;
ALTER TABLE verification_runs ADD COLUMN env_fingerprint TEXT;
ALTER TABLE verification_runs ADD COLUMN outcome TEXT;
ALTER TABLE verification_runs ADD COLUMN bundle_hash TEXT;
"""

MIGRATIONS = [(1, "initial", M1 + "\n" + _triggers()), (2, "verification_runner", M2)]


def checksum(sql):
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()
