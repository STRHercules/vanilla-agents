"""Contracts, validator, state tables and authority matrix (pure, no database)."""
import itertools
import unittest

from vanilla_agents import authority, state
from vanilla_agents.authority import Actor
from vanilla_agents.contracts import (CLAIM_INPUT_SCHEMA, AuthorityError, IllegalTransition, Principal, UnsupportedSchemaFeature, ValidationError, new_id,
                                 validate)


class ValidatorTests(unittest.TestCase):
    def test_unsupported_keyword_fails_loudly(self):
        for kw in ({"pattern": "x"}, {"minimum": 1}, {"oneOf": []}, {"$ref": "#"}, {"format": "uri"}):
            with self.assertRaises(UnsupportedSchemaFeature):
                validate("x", {"type": "string", **kw})

    def test_nested_unsupported_keyword_is_found(self):
        with self.assertRaises(UnsupportedSchemaFeature):
            validate({"a": "x"}, {"type": "object", "properties": {"a": {"type": "string", "pattern": "x"}}})

    def test_unknown_fields_and_wrong_types_rejected(self):
        schema = {"type": "object", "required": ["a"], "additionalProperties": False, "properties": {"a": {"type": "integer"}}}
        validate({"a": 1}, schema)
        for bad in ({"a": "1"}, {"a": True}, {"a": 1, "b": 2}, {}):
            with self.assertRaises(ValidationError):
                validate(bad, schema)

    def test_claim_schema_requires_all_fields(self):
        good = {k: "x" for k in CLAIM_INPUT_SCHEMA["required"]}
        good.update({"status": "VERIFIED", "evidence_refs": [], "repeatability_class": "repeatable"})
        validate(good, CLAIM_INPUT_SCHEMA)
        for k in CLAIM_INPUT_SCHEMA["required"]:
            bad = dict(good)
            del bad[k]
            with self.assertRaises(ValidationError):
                validate(bad, CLAIM_INPUT_SCHEMA)
        with self.assertRaises(ValidationError):
            validate({**good, "status": "MAYBE"}, CLAIM_INPUT_SCHEMA)

    def test_ids_are_prefixed_unique_and_sortable(self):
        ids = [new_id("task") for _ in range(2000)]
        self.assertEqual(len(set(ids)), 2000)
        self.assertTrue(all(i.startswith("task_") for i in ids))


class StateTableTests(unittest.TestCase):
    def test_every_pair_is_legal_only_if_listed(self):
        for name, table in state.TABLES.items():
            states = list(table)
            for a, b in itertools.product(states, states):
                listed = b in table[a]
                self.assertEqual(state.can(name, a, b), listed, (name, a, b))
                if listed:
                    state.require(name, a, b)
                else:
                    with self.assertRaises(IllegalTransition):
                        state.require(name, a, b)

    def test_all_targets_are_known_states(self):
        for name, table in state.TABLES.items():
            for a, targets in table.items():
                self.assertTrue(targets <= set(table), (name, a))

    def test_terminal_states_have_no_exits(self):
        for s in ("FINISHED", "FAILED", "CANCELLED"):
            self.assertEqual(state.EXECUTION[s], set())
        self.assertEqual(state.RELEASE["APPLIED"], set())
        self.assertEqual(state.RELEASE["REVOKED"], {"APPROVED"})   # D-027: a re-qualified task may be approved again
        for s in ("SUCCEEDED", "FAILED", "LOST", "CANCELLED"):
            self.assertEqual(state.ATTEMPT[s], set())

    def test_cancelled_is_not_a_direct_target_in_the_execution_table(self):
        self.assertTrue(all("CANCELLED" not in t for t in state.EXECUTION.values()))

    def test_cancelled_needs_everything_confirmed(self):
        state.require_cancelled("RUNNING", "CONFIRMED", "termination_confirmed", 0)
        state.require_cancelled("RUNNING", "CONFIRMED", None, 0)
        for args in (("RUNNING", "TERMINATING", "gone", 0), ("RUNNING", "CONFIRMED", "alive", 0), ("RUNNING", "CONFIRMED", "unreachable", 0),
                     ("RUNNING", "CONFIRMED", "gone", 1), ("FINISHED", "CONFIRMED", "gone", 0)):
            with self.assertRaises(IllegalTransition):
                state.require_cancelled(*args)

    def test_unreachable_is_not_terminated(self):
        self.assertNotIn("unreachable", state.TERMINATED_WORKER_STATES)
        self.assertFalse(state.can("worker", "unreachable", "gone"))


class AuthorityMatrixTests(unittest.TestCase):
    def test_matrix_allows_exactly_the_listed_principals(self):
        for op, allowed in authority.MATRIX.items():
            for p in Principal.ALL:
                actor = Actor(p, "x")
                if p in allowed:
                    authority.require(actor, op)
                else:
                    with self.assertRaises(AuthorityError, msg=(op, p)):
                        authority.require(actor, op)

    def test_worker_can_only_heartbeat_note_and_ask(self):
        w = Actor(Principal.WORKER, "w1")
        ok = {op for op, a in authority.MATRIX.items() if Principal.WORKER in a}
        self.assertEqual(ok, {"worker_heartbeat", "worker_event", "request_approval"})

    def test_nothing_but_controller_or_user_decides_approvals(self):
        self.assertEqual(authority.MATRIX["decide_approval"], {Principal.CONTROLLER, Principal.USER})
        with self.assertRaises(AuthorityError):
            authority.require_decider(Actor(Principal.CONTROLLER, "c"), 3)
        authority.require_decider(Actor(Principal.USER, "u"), 3)

    def test_unknown_operation_and_principal(self):
        with self.assertRaises(AuthorityError):
            authority.require(Actor(Principal.USER, "u"), "do_anything")
        with self.assertRaises(AuthorityError):
            Actor("root", "x")

    def test_actor_is_immutable(self):
        a = Actor(Principal.WORKER, "w1")
        with self.assertRaises(AttributeError):
            a.type = Principal.USER

    def test_binding_reason_codes(self):
        base = {"decision": "approved", "consumed_at": None, "expires_at": 100.0, "concept": "integration", "operation": "integrate", "scope_json": "{}",
                "target_ref": "T", "policy_hash": "P", "qualification_id": "Q"}
        use = {"concept": "integration", "operation": "integrate", "scope_json": "{}", "target_ref": "T", "policy_hash": "P", "qualification_id": "Q"}
        self.assertIsNone(authority.check_binding(base, use, 10.0))
        cases = [({"decision": None}, {}, "undecided"), ({"decision": "denied"}, {}, "denied"), ({"consumed_at": 5}, {}, "consumed"),
                 ({}, {"now": 101.0}, "expired"), ({}, {"operation": "x"}, "wrong_operation"), ({}, {"scope_json": "{1}"}, "wrong_scope"),
                 ({}, {"target_ref": "T2"}, "wrong_target"), ({}, {"policy_hash": "P2"}, "policy_changed"), ({}, {"qualification_id": "Q2"}, "qualification_changed"),
                 ({}, {"concept": "action"}, "wrong_concept")]
        for ov_a, ov_u, code in cases:
            u = {**use, **{k: v for k, v in ov_u.items() if k != "now"}}
            self.assertEqual(authority.check_binding({**base, **ov_a}, u, ov_u.get("now", 10.0)), code)


if __name__ == "__main__":
    unittest.main()
