"""Check definitions and verdict rules (pure except the artifact existence test). Exit code 0 is never enough on its own (silent-success rule)."""
import os
import re

from ..contracts import ValidationError

ENV_EXIT_CODES = {3221226505, 3221225477, 3221225725, 3221225786}      # rustc stack overrun, access violation, stack overflow, ctrl-c
ENV_PATTERNS = re.compile(r"(?i)(access is denied|no space left on device|there is not enough space on the disk|os error (5|32|112)\b|"
                          r"the process cannot access the file because it is being used|address already in use|only one usage of each socket)")
RULE_TYPES = ("min_tests_discovered", "artifact_exists", "output_contains", "silent_allowed", "exit_code_in")
REPEATABILITY = ("repeatable", "needs_reset", "non_repeatable")


def validate_check_def(d):
    """A check must say HOW success is proven; definitions without a rule are rejected."""
    for k in ("check_id", "argv", "rule"):
        if k not in d:
            raise ValidationError("check definition is missing %r" % k)
    if not (isinstance(d["argv"], list) and d["argv"] and all(isinstance(a, str) for a in d["argv"])):
        raise ValidationError("argv must be a non-empty list of strings (no shell strings)")
    rule = d["rule"]
    if rule.get("type") not in RULE_TYPES:
        raise ValidationError("unknown rule type %r" % (rule.get("type"),))
    if rule["type"] == "min_tests_discovered" and not (isinstance(rule.get("n"), int) and rule["n"] >= 1 and rule.get("pattern")):
        raise ValidationError("min_tests_discovered needs n >= 1 and a pattern")
    if rule["type"] == "artifact_exists" and not (isinstance(rule.get("paths"), list) and rule["paths"]):
        raise ValidationError("artifact_exists needs a list of paths")
    if rule["type"] == "output_contains" and not rule.get("pattern"):
        raise ValidationError("output_contains needs a pattern")
    if d.get("repeatability", "repeatable") not in REPEATABILITY:
        raise ValidationError("unknown repeatability class")
    t = d.get("timeout", 600)
    if not (isinstance(t, int) and 1 <= t <= 3600):
        raise ValidationError("timeout must be 1..3600 seconds")
    return d


def _first_int(x):
    return int(x if isinstance(x, str) else x[0])


def evaluate(check, exit_code, output, cwd, timed_out=False, started=None):
    """-> (status, observed, rule_results). status in VERIFIED | FAILED | INCONCLUSIVE | BLOCKED."""
    rule = check["rule"]
    ok_codes = rule.get("codes") if rule["type"] == "exit_code_in" else check.get("ok_exit_codes", [0])
    ok_codes = ok_codes or [0]
    if timed_out:
        return "FAILED" if check.get("timeout_is_failure") else "BLOCKED", "timed out after %ss" % check.get("timeout", 600), [{"rule": "timeout", "ok": False}]
    if exit_code is not None and exit_code != 0 and (exit_code in ENV_EXIT_CODES or ENV_PATTERNS.search(output or "")):
        return "BLOCKED", "environment failure (exit %s)" % exit_code, [{"rule": "environment", "ok": False}]
    if exit_code not in ok_codes and rule["type"] == "min_tests_discovered":
        found = re.findall(rule["pattern"], output or "")
        if found and _first_int(found[-1]) == 0:                      # runners such as unittest exit non-zero when they find nothing: that is NOT a code failure
            return "INCONCLUSIVE", "runner exit %s: only 0 test(s) discovered (need %d)" % (exit_code, rule["n"]), [{"rule": "min_tests_discovered", "ok": False, "found": 0, "needed": rule["n"]}]
    if exit_code not in ok_codes:
        return "FAILED", "exit code %s" % exit_code, [{"rule": "exit_code", "ok": False, "detail": "expected one of %s" % ok_codes}]
    results = [{"rule": "exit_code", "ok": True}]
    kind = rule["type"]
    if kind in ("silent_allowed", "exit_code_in"):
        return "VERIFIED", "exit %s (no output required by this check)" % exit_code, results
    if kind == "min_tests_discovered":
        found = re.findall(rule["pattern"], output or "")
        count = sum(int(x if isinstance(x, str) else x[0]) for x in (found if rule.get("sum") else found[-1:])) if found else 0   # "sum": cargo prints one summary per test binary
        skipped = 0
        if rule.get("skipped_pattern"):
            sk = re.findall(rule["skipped_pattern"], output or "")
            skipped = sum(int(x) for x in sk) if rule.get("sum") else (int(sk[-1]) if sk else 0)
        ok = count >= rule["n"] and not (skipped and skipped >= count)
        results.append({"rule": "min_tests_discovered", "ok": ok, "found": count, "skipped": skipped, "needed": rule["n"]})
        if not ok:
            why = "no test summary found" if not found else "only %d test(s) discovered (need %d)" % (count, rule["n"]) if count < rule["n"] else "every discovered test was skipped"
            return "INCONCLUSIVE", "exit 0 but %s" % why, results
        return "VERIFIED", "exit 0; %d test(s) discovered, %d skipped" % (count, skipped), results
    if kind == "output_contains":
        ok = re.search(rule["pattern"], output or "") is not None
        results.append({"rule": "output_contains", "ok": ok})
        return ("VERIFIED", "exit 0; expected output present", results) if ok else ("INCONCLUSIVE", "exit 0 but the expected output was not found", results)
    if kind == "artifact_exists":
        def fresh(p):  # must be made by this run: mtime >= run start (1s slack for coarse filesystems)
            f = os.path.join(cwd, p)
            return os.path.isfile(f) and os.path.getsize(f) > 0 and (started is None or os.path.getmtime(f) >= started - 1)
        missing = [p for p in rule["paths"] if not fresh(p)]
        results.append({"rule": "artifact_exists", "ok": not missing, "missing": missing})
        return ("VERIFIED", "exit 0; artifacts present", results) if not missing else ("INCONCLUSIVE", "exit 0 but artifacts missing or empty: %s" % missing, results)
    raise ValidationError("unhandled rule type")
