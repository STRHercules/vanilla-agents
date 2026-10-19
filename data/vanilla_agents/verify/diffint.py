"""Diff-integrity analysis (pure): protected paths and weakened tests. Findings with blocking=True stop a task reaching VERIFIED."""
import fnmatch
import re

TEST_PATH = re.compile(r"(^|/)(tests?|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|rs|go|js|ts)$|\.(test|spec)\.(js|ts)$", re.I)
ASSERTION = re.compile(r"\b(assert\w*|expect|should|verify|ensure)\b")
TEST_DEF = re.compile(r"^\s*((async\s+)?def test_\w+|(async\s+)?fn test_\w+|#\[(tokio::)?test\b|#\[cfg\(test\)\]|it\(|test\()")
SKIP_MARK = re.compile(r"(@unittest\.skip|pytest\.mark\.(skip|xfail)|#\[ignore\]|\.skip\(|\bxit\(|@Ignore|t\.Skip\(|\bskipTest\b|\bSkipTest\b|pytest\.skip\b|\bexpectedFailure\b)")
BUILD_CONFIG = re.compile(r"(^|/)(Cargo\.toml|Cargo\.lock|\.cargo/config\.toml|pyproject\.toml|setup\.py|setup\.cfg|package\.json|tox\.ini|Makefile|\.github/.*)$")

TRIVIAL = re.compile(r"^\s*(pass|return( None)?|assert (True|1)|self\.assertTrue\(True\)|\.\.\.)\s*;?\s*$")
SHADOW_STRONG = {"unittest.py", "conftest.py", "pytest.py", "sitecustomize.py", "usercustomize.py"}
STDLIB = {n + ".py" for n in getattr(__import__("sys"), "stdlib_module_names", ())}


def _gutted(removed, added):
    """One hunk: body lines replaced by trivial ones while any assertion line in the hunk is kept verbatim."""
    add = [l for l in added if l.strip()]
    kept = {l.strip() for l in removed if ASSERTION.search(l)}
    other = [l for l in add if l.strip() not in kept]
    body = [l for l in removed if l.strip() and not TEST_DEF.search(l) and not TRIVIAL.search(l) and not ASSERTION.search(l)]
    return bool(body) and bool(other) and all(TRIVIAL.search(l) for l in other)


def matches(path, patterns):
    """Glob match; 'dir/**' protects everything under dir."""
    for pat in patterns:
        if pat.endswith("/**") and (path == pat[:-3] or path.startswith(pat[:-2])):
            return True
        if fnmatch.fnmatch(path, pat):
            return True
    return False


def _count(rx, lines):
    return sum(1 for l in lines if rx.search(l))


def analyze(changes, protected):
    """Return findings: {kind, path, severity, blocking, detail}."""
    out = []

    def add(kind, path, severity, blocking, detail):
        out.append({"kind": kind, "path": path, "severity": severity, "blocking": blocking, "detail": detail})

    for ch in changes:
        p = ch.path
        # a rename out of a test path hides the test from the test-path checks; Rust keeps inline tests in any .rs file
        is_test = bool(TEST_PATH.search(p)) or p.lower().endswith(".rs")
        if ch.old_path and ch.old_path != p and TEST_PATH.search(ch.old_path) and not TEST_PATH.search(p):
            add("tests_removed", p, "high", True, "test file renamed out of a test path (from %s)" % ch.old_path)
        if matches(p, protected):
            add("protected_path_changed", p, "high", False, "protected file %s (the trusted original is used for verification; the change needs separate approval)" % ch.status)
        if BUILD_CONFIG.search(p):
            add("build_config_changed", p, "medium", False, "build or tooling configuration changed")
        base = p.rsplit("/", 1)[-1].lower()
        if ch.status != "deleted" and (ch.status == "added" or ch.old_path != p):
            if base in SHADOW_STRONG:
                add("module_shadowing", p, "high", True, "added %s can shadow a test/runtime module in the work root" % base)
            elif base in STDLIB:
                add("module_shadowing", p, "medium", False, "added %s has the name of a standard-library module" % base)
        if not is_test:
            continue
        if any(_gutted(r, a) for r, a in ch.hunks):
            add("test_body_gutted", p, "high", True, "a test body was reduced to pass/assert True/return while an assertion line was kept")
        ra, aa = _count(ASSERTION, ch.removed), _count(ASSERTION, ch.added)
        if ra > aa:
            add("assertions_removed", p, "high", True, "%d assertion line(s) removed, %d added" % (ra, aa))
        elif ra:
            gone = {l.strip() for l in ch.removed if ASSERTION.search(l)} - {l.strip() for l in ch.added}
            if gone:
                add("assertions_modified", p, "high", True, "%d assertion line(s) changed or replaced" % len(gone))
        rd, ad = _count(TEST_DEF, ch.removed), _count(TEST_DEF, ch.added)
        if rd > ad:
            add("tests_removed", p, "high", True, "%d test definition(s) removed, %d added" % (rd, ad))
        rs, as_ = _count(SKIP_MARK, ch.removed), _count(SKIP_MARK, ch.added)
        if as_ > rs:
            add("skip_added", p, "high", True, "%d skip/ignore marker(s) added" % (as_ - rs))
        if ch.status == "deleted":
            add("test_file_deleted", p, "high", True, "test file deleted")
    return out
