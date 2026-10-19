"""Orchestrates one verification run and returns a result bundle plus its evidence blobs. Runs in its OWN process; it never writes the database.

Order: refuse on any manifest mismatch -> validate check definitions -> clean checkout (patch validated first, protected files restored from the base)
-> diff-integrity findings -> run each mapped check (silent-success, environment and flaky rules) -> bundle marked complete only at the very end."""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time

from ..audit import canonical
from ..contracts import ValidationError
from . import VERIFIER_ID, VERIFIER_VERSION
from .checkout import CheckoutError, make_checkout
from .diffint import analyze
from .manifest import file_hash, require_manifest
from .patchcheck import PatchRejected
from .redact import redact
from .rules import evaluate, validate_check_def
from .runner import build_env, run_check

MAX_OUTPUT = 65536
ENV_RETRIES = 2
FLAKY_EXTRA_RUNS = 3


def _blob(blobs, data):
    h = hashlib.sha256(data).hexdigest()
    blobs[h] = data
    return h


def _git_version():
    try:
        return subprocess.run(["git", "--version"], capture_output=True, timeout=20).stdout.decode().strip()
    except Exception:
        return "git unknown"


def _attempt(check, work, tmp, degraded=False):
    argv = check.get("degraded_argv") if degraded and check.get("degraded_argv") else check["argv"]
    argv = [a.replace("{python}", sys.executable) for a in argv]
    cwd = os.path.join(work, *check.get("cwd", ".").split("/"))
    t_start = time.time()
    r = run_check(argv, cwd, build_env(check.get("env"), tmp), check.get("timeout", 600), MAX_OUTPUT, tmp)
    status, observed, rules = evaluate(check, r["exit_code"], r["output"], cwd, r["timed_out"], t_start)
    if r.get("start_error"):
        status, observed, rules = "BLOCKED", "the check could not be started", [{"rule": "start", "ok": False}]
    if r.get("cancelled"):
        status, observed, rules = "BLOCKED", "stopped: Pause was pressed", [{"rule": "cancelled", "ok": False}]
    return {"n": 0, "exit_code": r["exit_code"], "status": status, "duration": r["duration"], "timed_out": r["timed_out"], "truncated": r["truncated"], "cancelled": bool(r.get("cancelled"))}, r["output"], observed, rules


def run_one_check(check, work, tmp):
    """Apply the attempt rules: environment retries (bounded), fail-then-pass flaky rule, optional repeat. Returns (status, observed, rules, attempts, last_output)."""
    attempts, outputs = [], []

    def go(degraded=False):
        a, out, obs, rules = _attempt(check, work, tmp, degraded)
        a["n"] = len(attempts) + 1
        attempts.append(a)
        outputs.append(out)
        return a["status"], obs, rules

    status, obs, rules = go()
    if status == "BLOCKED" and not attempts[-1]["timed_out"] and not attempts[-1]["cancelled"]:
        for _ in range(ENV_RETRIES):                                    # environment trouble: bounded, degraded retries, never a pass by luck
            status, obs, rules = go(degraded=True)
            if status != "BLOCKED":
                break
    elif status == "FAILED" and check.get("repeatability", "repeatable") != "non_repeatable" and check.get("retry_failed", True):
        status2, obs2, rules2 = go()
        if status2 == "VERIFIED":
            for _ in range(FLAKY_EXTRA_RUNS):                           # fail-then-pass: three more runs, and the check stays FLAKY
                go()
        if len({a["status"] for a in attempts}) > 1:
            status, obs, rules = "FLAKY", "inconsistent results across attempts: %s" % [a["status"] for a in attempts], rules2
        else:
            status, obs, rules = status2, obs2, rules2
    elif status == "VERIFIED" and check.get("repeat", 1) > 1:
        for _ in range(check["repeat"] - 1):
            go()
        if len({a["status"] for a in attempts}) > 1:
            status, obs = "FLAKY", "inconsistent results across %d repeats: %s" % (len(attempts), [a["status"] for a in attempts])
    return status, obs, rules, attempts, outputs[-1]


def _load_holdouts(trusted, checks):
    """Optional trusted/holdout.json: check definitions the builder never sees. Returned as {"hidden-N": def}, N by sorted real id, so the real id never leaves here (D-290)."""
    p = os.path.join(trusted, "holdout.json")
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        defs = sorted(json.load(f), key=lambda c: c["check_id"])
    out = {"hidden-%d" % i: validate_check_def(dict(c, check_id="hidden-%d" % i)) for i, c in enumerate(defs, 1)}
    if set(out) & set(checks):
        raise ValidationError("a normal check uses a reserved hidden-N id")
    return out


def run_verification(req, clock=time.time):
    """req: run_id, task_id, repo, base_commit, source_commit, patch_path (or None), trusted_dir, work_root, criteria [{criterion_id, check_id, required}].
    Returns (bundle, blobs). Raises VanillaError (manifest_mismatch, validation_failed) when the run must not start."""
    started = clock()
    trusted = req["trusted_dir"]
    mh = require_manifest(trusted, expected_hash=req.get("expected_manifest_hash"))                                      # refuse on ANY mismatch; nothing is repaired
    with open(os.path.join(trusted, "checks.json"), "r", encoding="utf-8") as f:
        checks = {c["check_id"]: validate_check_def(c) for c in json.load(f)}
    with open(os.path.join(trusted, "protected.json"), "r", encoding="utf-8") as f:
        protected = json.load(f)
    holdouts = _load_holdouts(trusted, checks) if req.get("holdouts", True) else {}      # D-304: only the app's fast route (never trusted-code work) sets holdouts False
    checks.update(holdouts)
    criteria = list(req["criteria"]) + [{"criterion_id": h, "check_id": h, "required": True} for h in holdouts]
    patch = b""
    if req.get("patch_path"):
        with open(req["patch_path"], "rb") as pf:
            patch = pf.read()
    patch_hash = hashlib.sha256(patch).hexdigest() if patch else "none"
    root = os.path.join(req["work_root"], req["run_id"])
    blobs, findings, results, not_verified = {}, [], [], []
    env_fp = "|".join([platform.platform(), "py" + platform.python_version(), _git_version(), "verifier " + VERIFIER_VERSION,
                       "checks " + file_hash(os.path.join(trusted, "checks.json"))[:16], "manifest " + mh[:16]])

    def finish(outcome):
        return {"run_id": req["run_id"], "task_id": req["task_id"], "base_commit": req["base_commit"], "source_commit": req["source_commit"], "patch_hash": patch_hash,
                "manifest_hash": mh, "verifier_id": VERIFIER_ID, "verifier_version": VERIFIER_VERSION, "env_fingerprint": env_fp, "started_at": started,
                "ended_at": clock(), "outcome": outcome, "checks": results, "diff_findings": findings, "not_verified": not_verified, "complete": True}, blobs

    try:
        co = make_checkout(req["repo"], req["base_commit"], patch, root, protected)
    except (PatchRejected, CheckoutError) as e:
        findings.append({"kind": e.code, "path": "", "severity": "high", "blocking": True, "detail": str(e)[:300]})
        not_verified.extend({"criterion_id": c["criterion_id"], "check_id": c["check_id"], "reason": "checkout or patch rejected"} for c in criteria)
        return finish("FAILED")
    findings.extend(analyze(co["changes"], protected))
    tmp = os.path.join(root, "tmp")
    os.makedirs(tmp, exist_ok=True)
    seen = set()
    for crit in criteria:
        cid = crit["check_id"]
        if cid in seen:
            continue
        seen.add(cid)
        check = checks.get(cid)
        if check is None:
            not_verified.append({"criterion_id": crit["criterion_id"], "check_id": cid, "reason": "no check definition for this criterion"})
            continue
        status, observed, rules, attempts, output = run_one_check(check, co["work"], tmp)
        if cid in holdouts:   # D-290: a holdout reports its opaque id and verdict only; no argv, output, rule text or observed detail
            sum_h = _blob(blobs, canonical({"check_id": cid, "hidden": True, "status": status}).encode("utf-8"))
            results.append({"check_id": cid, "status": status, "expected": "hidden check passes", "observed": "", "evidence": [sum_h],
                            "repeatability": "repeatable", "attempts": [{"n": a["n"], "status": a["status"]} for a in attempts], "rule_results": []})
            if status != "VERIFIED":
                not_verified.append({"criterion_id": crit["criterion_id"], "check_id": cid, "reason": "hidden check %s" % status})
            continue
        out_h = _blob(blobs, redact(output).encode("utf-8"))
        summary = {"check_id": cid, "argv": [redact(a) for a in check["argv"]], "attempts": attempts, "rule_results": rules, "observed": observed}
        sum_h = _blob(blobs, canonical(summary).encode("utf-8"))
        results.append({"check_id": cid, "status": status, "expected": "check %s passes its evidence rule (%s)" % (cid, check["rule"]["type"]),
                        "observed": observed[:300], "evidence": [sum_h, out_h], "repeatability": check.get("repeatability", "repeatable"),
                        "attempts": attempts, "rule_results": rules})
        if status != "VERIFIED":
            not_verified.append({"criterion_id": crit["criterion_id"], "check_id": cid, "reason": "%s: %s" % (status, observed[:120])})
    required_ids = {c["check_id"] for c in criteria if c.get("required", True)}
    by = {r["check_id"]: r["status"] for r in results}
    if any(f["blocking"] for f in findings):
        outcome = "FAILED"
    elif all(by.get(i) == "VERIFIED" for i in required_ids) and required_ids:
        outcome = "VERIFIED"
    else:
        statuses = {by.get(i, "NOT_VERIFIED") for i in required_ids}
        outcome = next((s for s in ("FAILED", "FLAKY", "BLOCKED", "INCONCLUSIVE") if s in statuses), "NOT_VERIFIED")
    try:
        shutil.rmtree(os.path.join(root, "tmp"), ignore_errors=True)
    except OSError:
        pass
    return finish(outcome)
