"""Project-type detection (D-330): look at a repo's files and propose check definitions in the normal checks.json format.
Pure and read-only: no command is run. Unknown type = no checks (onboarding still works; only the agents work, nothing is auto-verified).
Every proposed check passes validate_check_def and says HOW success is proven (a test count, or output that must appear)."""
import json
import os
import re

from ..verify.rules import validate_check_def

_CARGO_ENV = ["USERPROFILE", "HOMEDRIVE", "HOMEPATH", "CARGO_HOME", "RUSTUP_HOME", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "LOCALAPPDATA", "APPDATA"]
_TOOL_ENV = ["USERPROFILE", "HOMEDRIVE", "HOMEPATH", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"]
_NPM_DEFAULT_TEST = re.compile(r"no test specified")


def _has(repo, name):
    return os.path.exists(os.path.join(repo, name))


def _read(repo, name):
    try:
        with open(os.path.join(repo, name), encoding="utf-8", errors="replace") as f:
            return f.read(200000)
    except OSError:
        return ""


def _check(cid, argv, rule, timeout, env):
    return {"check_id": cid, "argv": argv, "cwd": ".", "timeout": timeout, "env": env, "repeatability": "repeatable", "rule": rule}


def _rust(repo):
    rule = {"type": "min_tests_discovered", "pattern": r"test result: ok\. ([1-9]\d*) passed", "skipped_pattern": r"(\d+) ignored", "sum": True, "n": 1}
    return {"type": "rust", "checks": [_check("cargo_test", ["cargo", "test", "--release"], rule, 1800, _CARGO_ENV)],
            "protected": ["tests/**", "scripts/**", "Cargo.lock", "rust-toolchain*", ".cargo/**"],
            "full_paths": ["Cargo.toml", "Cargo.lock", "rust-toolchain*", ".cargo/**", "build.rs"]}


def _npm(repo):
    try:
        test = (json.loads(_read(repo, "package.json")).get("scripts") or {}).get("test", "")
    except (ValueError, AttributeError):
        test = ""
    checks = []
    if isinstance(test, str) and test.strip() and not _NPM_DEFAULT_TEST.search(test):    # the stub 'no test specified' proves nothing
        rule = {"type": "output_contains", "pattern": r"(?i)(\b[1-9]\d* (passing|passed)\b|# pass [1-9]\d*)"}   # mocha, jest/vitest, node --test
        checks.append(_check("npm_test", ["npm.cmd" if os.name == "nt" else "npm", "test"], rule, 900, _TOOL_ENV))
    return {"type": "npm", "checks": checks, "protected": ["test/**", "tests/**", "__tests__/**", "scripts/**", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
            "full_paths": ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"]}


def _python(repo):
    cfg = _read(repo, "pyproject.toml") + _read(repo, "setup.cfg") + _read(repo, "tox.ini")
    pytest = _has(repo, "pytest.ini") or _has(repo, "conftest.py") or "pytest" in cfg
    tests = any(f.startswith("test") and f.endswith(".py") for d in ("tests", "test", ".") for f in _listdir(os.path.join(repo, d)))
    checks = []
    if pytest:
        checks.append(_check("pytest", ["{python}", "-m", "pytest", "-q"], {"type": "min_tests_discovered", "pattern": r"(\d+) passed", "n": 1}, 900, _TOOL_ENV))
    elif tests:
        checks.append(_check("unittest", ["{python}", "-m", "unittest", "discover", "-v"], {"type": "min_tests_discovered", "pattern": r"Ran (\d+) tests?", "n": 1}, 900, _TOOL_ENV))
    return {"type": "python", "checks": checks, "protected": ["tests/**", "test/**", "conftest.py", "scripts/**", "pytest.ini", "tox.ini", "requirements*.txt", "poetry.lock"],
            "full_paths": ["pyproject.toml", "setup.py", "setup.cfg", "requirements*.txt", "poetry.lock", "pytest.ini", "tox.ini"]}


def _go(repo):
    rule = {"type": "output_contains", "pattern": r"(?m)^ok\s"}
    return {"type": "go", "checks": [_check("go_test", ["go", "test", "./..."], rule, 900, _TOOL_ENV)],
            "protected": ["**/*_test.go", "scripts/**", "go.sum"], "full_paths": ["go.mod", "go.sum"]}


def _make(repo):
    # ponytail: a Makefile gives no test count, so the proof is only "the output says pass/ok"; weakest rung, used only when no other type matched
    rule = {"type": "output_contains", "pattern": r"(?i)\b(pass(ed|ing)?|ok)\b"}
    return {"type": "make", "checks": [_check("make_test", ["make", "test"], rule, 900, _TOOL_ENV)],
            "protected": ["tests/**", "test/**", "scripts/**", "Makefile"], "full_paths": ["Makefile"]}


def _listdir(p):
    try:
        return os.listdir(p)
    except OSError:
        return []


def detect(repo):
    """-> {'type': 'rust+npm'|...|'unknown', 'checks': [defs], 'protected': [globs], 'full_paths': [globs]}. Marker files decide; a type with no provable test is listed but adds no check."""
    repo = str(repo)
    found = [f(repo) for marker, f in (("Cargo.toml", _rust), ("package.json", _npm), (None, _python), ("go.mod", _go)) if (_has(repo, marker) if marker else _pyish(repo))]
    if not any(t["checks"] for t in found) and re.search(r"(?m)^test\s*:", _read(repo, "Makefile")):
        found.append(_make(repo))
    out = {"type": "+".join(t["type"] for t in found) or "unknown", "checks": [], "protected": [], "full_paths": []}
    for t in found:
        out["checks"] += [validate_check_def(c) for c in t["checks"]]
        for k in ("protected", "full_paths"):
            out[k] += [g for g in t[k] if g not in out[k]]
    return out


def _pyish(repo):
    return any(_has(repo, n) for n in ("pyproject.toml", "pytest.ini", "setup.py", "setup.cfg", "tox.ini", "conftest.py", "requirements.txt")) or any(
        f.startswith("test") and f.endswith(".py") for d in ("tests", "test") for f in _listdir(os.path.join(repo, d)))
