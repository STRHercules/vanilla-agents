"""The result bundle the verifier subprocess returns, and its explicit validation. A bundle without `complete: true` (a crashed or truncated run) is rejected."""
import hashlib
import re

from ..audit import canonical
from ..contracts import CLAIM_STATUSES, REPEATABILITY, ValidationError, validate

HEX64 = re.compile(r"^[0-9a-f]{64}$")

CHECK_SCHEMA = {
    "type": "object",
    "required": ["check_id", "status", "expected", "observed", "evidence", "repeatability", "attempts", "rule_results"],
    "additionalProperties": False,
    "properties": {
        "check_id": {"type": "string", "minLength": 1},
        "status": {"type": "string", "enum": list(CLAIM_STATUSES)},
        "expected": {"type": "string"}, "observed": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string", "minLength": 64}},
        "repeatability": {"type": "string", "enum": list(REPEATABILITY)},
        "attempts": {"type": "array", "items": {"type": "object"}},
        "rule_results": {"type": "array", "items": {"type": "object"}},
    },
}
BUNDLE_SCHEMA = {
    "type": "object",
    "required": ["run_id", "task_id", "base_commit", "source_commit", "patch_hash", "manifest_hash", "verifier_id", "verifier_version", "env_fingerprint",
                 "started_at", "ended_at", "outcome", "checks", "diff_findings", "not_verified", "complete"],
    "additionalProperties": False,
    "properties": {
        "run_id": {"type": "string", "minLength": 1}, "task_id": {"type": "string", "minLength": 1}, "base_commit": {"type": "string", "minLength": 1},
        "source_commit": {"type": "string", "minLength": 1}, "patch_hash": {"type": "string"}, "manifest_hash": {"type": "string", "minLength": 64},
        "verifier_id": {"type": "string", "minLength": 1}, "verifier_version": {"type": "string", "minLength": 1}, "env_fingerprint": {"type": "string", "minLength": 1},
        "started_at": {"type": "number"}, "ended_at": {"type": "number"}, "outcome": {"type": "string", "enum": list(CLAIM_STATUSES)},
        "checks": {"type": "array", "items": CHECK_SCHEMA}, "diff_findings": {"type": "array", "items": {"type": "object"}},
        "not_verified": {"type": "array", "items": {"type": "object"}}, "complete": {"type": "boolean"},
    },
}


def validate_bundle(b):
    validate(b, BUNDLE_SCHEMA)
    if b["complete"] is not True:
        raise ValidationError("bundle is not marked complete (the verifier did not finish)", code="bundle_incomplete")
    if not HEX64.match(b["manifest_hash"]):
        raise ValidationError("manifest hash must be 64 hex characters", code="bad_bundle")
    for r in b["checks"]:
        if not all(HEX64.match(h) for h in r["evidence"]):
            raise ValidationError("evidence references must be sha256 hashes", code="bad_bundle")
        if r["status"] == "VERIFIED" and not r["evidence"]:
            raise ValidationError("a VERIFIED check must carry evidence", code="bad_bundle")
    for f in b["diff_findings"]:
        if not isinstance(f.get("blocking"), bool) or not f.get("kind"):
            raise ValidationError("diff finding is malformed", code="bad_bundle")
    return True


def bundle_hash(b):
    return hashlib.sha256(canonical(b).encode("utf-8")).hexdigest()
