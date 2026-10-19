"""Who may do what (pure). Callers request named operations; they never supply target states."""
from .contracts import AuthorityError, Principal

C, V, W, U, S = Principal.CONTROLLER, Principal.VERIFIER, Principal.WORKER, Principal.USER, Principal.SYSTEM

# operation -> principal types allowed
MATRIX = {
    "create_project": {C, U},
    "create_task": {C, U},
    "add_criterion": {C, U},
    "set_task_policy": {C, U},
    "set_task_target": {C, U},
    "update_task_source": {C},
    "transition_execution": {C},
    "set_blocked": {C, S},
    "request_cancel": {C, U},
    "advance_cancel": {C},
    "finish_cancel": {C},
    "register_worker": {C},
    "set_worker_state": {C},
    "worker_heartbeat": {W},            # own worker id only (checked separately)
    "worker_event": {W},                # own attempt only (checked separately)
    "create_attempt": {C},
    "transition_attempt": {C},
    "insert_claim": {V},
    "invalidate_claim": {C, U},
    "set_verification": {V, C},
    "create_lease": {C},
    "release_lease": {C},
    "renew_lease": {C},
    "request_approval": {C, U, W},      # a worker may ASK, never decide
    "decide_approval": {C, U},
    "approve_release": {C},
    "apply_release": {C},
    "revoke_release": {C, U},
    "register_evidence": {C, V},
    "revalidate_evidence": {C, V},
    "ingest_verification": {C},
    "add_policy": {C, U},
    "controller_takeover": {C},
    "checkpoint_failed": {C},
}


class Actor:
    """An authenticated principal created by the controller entry point. Immutable value object."""
    __slots__ = ("type", "id")

    def __init__(self, type_, id_):
        if type_ not in Principal.ALL:
            raise AuthorityError("unknown principal type %r" % (type_,))
        object.__setattr__(self, "type", type_)
        object.__setattr__(self, "id", id_)

    def __setattr__(self, k, v):
        raise AttributeError("Actor is immutable")

    def __repr__(self):
        return "Actor(%s:%s)" % (self.type, self.id)


def require(actor, operation):
    allowed = MATRIX.get(operation)
    if allowed is None:
        raise AuthorityError("unknown operation %r" % operation)
    if actor.type not in allowed:
        raise AuthorityError("%s may not perform %s" % (actor.type, operation), code="authority_denied")


TIER3_OPERATIONS = {"push", "publish", "deploy", "delete_data", "expose_secrets", "change_security", "integrate"}
MAX_APPROVAL_SECONDS = 24 * 3600


def derive_tier(concept, operation, requested):
    """The tier is derived from the operation, never trusted from the requester; a requester may only ask for a HIGHER tier."""
    base = 3 if (concept == "integration" or operation in TIER3_OPERATIONS) else 2
    return max(base, int(requested or 0))


def require_decider(actor, tier):
    """Only controller/user decide approvals; tier 3 only the user."""
    require(actor, "decide_approval")
    if tier >= 3 and actor.type != U:
        raise AuthorityError("tier 3 approvals are decided by the user only")


def check_binding(approval, use, now):
    """Return None if the approval may be used for `use`, else a specific rejection code.

    approval: dict row; use: dict with concept, operation, scope_json, target_ref, policy_hash, qualification_id.
    """
    if approval["decision"] is None:
        return "undecided"
    if approval["decision"] != "approved":
        return "denied"
    if approval["consumed_at"] is not None:
        return "consumed"
    if now >= approval["expires_at"]:
        return "expired"
    if approval["concept"] != use["concept"]:
        return "wrong_concept"
    if approval["operation"] != use["operation"]:
        return "wrong_operation"
    if approval["scope_json"] != use["scope_json"]:
        return "wrong_scope"
    if approval["concept"] == "integration":
        if approval["target_ref"] != use["target_ref"]:
            return "wrong_target"
        if approval["policy_hash"] != use["policy_hash"]:
            return "policy_changed"
        if approval["qualification_id"] != use["qualification_id"]:
            return "qualification_changed"
    return None
