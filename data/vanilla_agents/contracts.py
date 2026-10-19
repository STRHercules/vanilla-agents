"""Identifiers, enums, errors and the explicit validator (no I/O).

The validator implements ONLY the keywords listed in SUPPORTED. It is not a JSON Schema implementation (decision D-016):
any other keyword raises UnsupportedSchemaFeature instead of being ignored.
"""
import os
import time

SCHEMA_VERSION = 1
POSTURE_LEVEL0 = "level_0_integrity_checks_only"


class VanillaError(Exception):
    """Base class for all vanilla-agents errors; `code` is a stable machine-readable reason."""
    code = "vanilla_error"

    def __init__(self, message="", code=None):
        super().__init__(message or self.code)
        if code:
            self.code = code


class IllegalTransition(VanillaError):
    code = "illegal_transition"


class AuthorityError(VanillaError):
    code = "authority_denied"


class ValidationError(VanillaError):
    code = "validation_failed"


class UnsupportedSchemaFeature(VanillaError):
    code = "unsupported_schema_feature"


class ControllerBusy(VanillaError):
    code = "controller_busy"


class LeaseBusy(VanillaError):
    code = "lease_busy"


class StaleFence(VanillaError):
    code = "stale_fence"


class EvidenceError(VanillaError):
    code = "evidence_error"


class EvidenceMissing(EvidenceError):
    code = "evidence_missing"


class EvidenceTampered(EvidenceError):
    code = "evidence_tampered"


class EvidenceConflict(EvidenceError):
    code = "evidence_conflict"


# ---- enums (plain string constants so rows stay human-readable)
class Principal:
    CONTROLLER, VERIFIER, WORKER, USER, SYSTEM = "controller", "verifier", "worker", "user", "system"
    ALL = (CONTROLLER, VERIFIER, WORKER, USER, SYSTEM)


class Exec:
    QUEUED, LEASING, RUNNING, FINISHED, FAILED, BLOCKED, CANCELLED = "QUEUED", "LEASING", "RUNNING", "FINISHED", "FAILED", "BLOCKED", "CANCELLED"


class Cancel:
    NONE, REQUESTED, TERMINATING, CONFIRMED = "NONE", "REQUESTED", "TERMINATING", "CONFIRMED"


class Verif:
    NOT_RUN, RUNNING, VERIFIED, FAILED, INCONCLUSIVE, FLAKY, STALE = "NOT_RUN", "RUNNING", "VERIFIED", "FAILED", "INCONCLUSIVE", "FLAKY", "STALE"


class Release:
    NOT_APPROVED, APPROVED, APPLIED, REVOKED = "NOT_APPROVED", "APPROVED", "APPLIED", "REVOKED"


BLOCKED_REASONS = ("usage_limit", "resource_busy", "disk", "environment", "approval_needed", "budget", "worker_unreachable", "retries_exhausted")
CLAIM_STATUSES = ("VERIFIED", "FAILED", "INCONCLUSIVE", "FLAKY", "NOT_VERIFIED", "BLOCKED")
REPEATABILITY = ("repeatable", "needs_reset", "non_repeatable")
APPROVAL_CONCEPTS = ("action", "integration")
EVIDENCE_RULES = ("min_tests_discovered", "artifact_exists", "output_contains", "silent_allowed", "exit_code_in")


def new_id(prefix):
    """Time-sortable id with a type prefix and a random suffix; never reused."""
    return "%s_%012x%s" % (prefix, time.time_ns() // 1000, os.urandom(4).hex())


# ---- explicit validator
SUPPORTED = {"type", "required", "properties", "additionalProperties", "enum", "minLength", "items", "description", "title"}
_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def validate(value, schema, path="$"):
    """Validate `value` against `schema`; raises ValidationError, or UnsupportedSchemaFeature for any keyword outside SUPPORTED."""
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise UnsupportedSchemaFeature("unsupported schema keyword(s) %s at %s" % (sorted(unknown), path))
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        for x in types:
            if x not in _TYPES:
                raise UnsupportedSchemaFeature("unsupported type %r at %s" % (x, path))
        if not any(_TYPES[x](value) for x in types):
            raise ValidationError("%s: expected %s, got %s" % (path, t, type(value).__name__))
    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError("%s: %r not in enum" % (path, value))
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        raise ValidationError("%s: shorter than %d" % (path, schema["minLength"]))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in value:
                raise ValidationError("%s: missing required field %r" % (path, r))
        if schema.get("additionalProperties", True) is False:
            extra = set(value) - set(props)
            if extra:
                raise ValidationError("%s: unknown field(s) %s" % (path, sorted(extra)))
        for k, sub in props.items():
            if k in value:
                validate(value[k], sub, "%s.%s" % (path, k))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], "%s[%d]" % (path, i))
    return True


# ---- published schema documents (documentation of the contracts; enforced with validate())
CLAIM_INPUT_SCHEMA = {
    "type": "object",
    "required": ["task_id", "criterion_id", "check_id", "status", "expected", "observed", "evidence_refs", "verifier_id", "verifier_version",
                 "source_commit", "env_fingerprint", "repeatability_class"],
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "attempt_id": {"type": ["string", "null"]},
        "criterion_id": {"type": "string", "minLength": 1},
        "check_id": {"type": "string", "minLength": 1},
        "run_id": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": list(CLAIM_STATUSES)},
        "expected": {"type": "string"},
        "observed": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "verifier_id": {"type": "string", "minLength": 1},
        "verifier_version": {"type": "string", "minLength": 1},
        "source_commit": {"type": "string", "minLength": 1},
        "env_fingerprint": {"type": "string", "minLength": 1},
        "repeatability_class": {"type": "string", "enum": list(REPEATABILITY)},
    },
}

TASK_INPUT_SCHEMA = {
    "type": "object",
    "required": ["project_id", "title", "description"],
    "additionalProperties": False,
    "properties": {
        "project_id": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "base_commit": {"type": ["string", "null"]},
        "budgets": {"type": "object"},
        "owned_paths": {"type": "array", "items": {"type": "string"}},
    },
}

SCHEMAS = {"claim_input": CLAIM_INPUT_SCHEMA, "task_input": TASK_INPUT_SCHEMA}
