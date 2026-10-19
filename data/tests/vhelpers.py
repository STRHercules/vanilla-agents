"""Fixture builders for Stage 2: tiny git repositories, trusted folders with pinned manifests, and verifier requests. Everything lives in temp folders."""
import json
import os
import subprocess
import tempfile

from vanilla_agents.verify.manifest import write_manifest

GIT = ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.com", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false"]

GOOD_TEST = '''import unittest


class T(unittest.TestCase):
    def test_add(self):
        from app import add
        self.assertEqual(add(1, 2), 3)

    def test_zero(self):
        from app import add
        self.assertEqual(add(0, 0), 0)
'''
APP = "def add(a, b):\n    return a + b\n"
BROKEN_APP = "def add(a, b):\n    return a - b\n"

UNIT_CHECK = {"check_id": "unit", "argv": ["{python}", "-m", "unittest", "discover", "-s", "tests", "-t", "."], "cwd": ".", "timeout": 60, "env": [],
              "repeatability": "repeatable", "rule": {"type": "min_tests_discovered", "pattern": r"Ran (\d+) tests?", "skipped_pattern": r"skipped=(\d+)", "n": 1}}


def git(repo, *args, input_bytes=None):
    p = subprocess.run([*GIT, "-C", repo, *args], capture_output=True, input=input_bytes)
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, p.stderr.decode("utf-8", "replace")))
    return p.stdout


def make_repo(root, files):
    os.makedirs(root, exist_ok=True)
    subprocess.run([*GIT, "init", "-q", "-b", "main", root], check=True, capture_output=True)
    for rel, content in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="\n", encoding="utf-8") as f:
            f.write(content)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return git(root, "rev-parse", "HEAD").decode().strip()


def make_patch(repo, changes):
    """Apply `changes` {rel: content or None to delete} to the working tree and return the diff against HEAD as bytes; the working tree is then reset."""
    for rel, content in changes.items():
        p = os.path.join(repo, *rel.split("/"))
        if content is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)
    git(repo, "add", "-A")
    patch = git(repo, "diff", "--cached", "--binary", "HEAD")
    git(repo, "reset", "-q", "--hard", "HEAD")
    git(repo, "clean", "-fdq")
    return patch


def make_trusted(root, checks, protected=("tests/**", "scripts/**")):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "checks.json"), "w", encoding="utf-8") as f:
        json.dump(checks, f)
    with open(os.path.join(root, "protected.json"), "w", encoding="utf-8") as f:
        json.dump(list(protected), f)
    write_manifest(root, ["checks.json", "protected.json"])
    return root


def request(tmp, repo, base, trusted, criteria, patch=None, run_id="run_t1", source="src1"):
    patch_path = None
    if patch is not None:
        patch_path = os.path.join(tmp, "worker.patch")
        with open(patch_path, "wb") as f:
            f.write(patch)
    return {"run_id": run_id, "task_id": "task_x", "repo": repo, "base_commit": base, "source_commit": source, "patch_path": patch_path,
            "trusted_dir": trusted, "work_root": os.path.join(tmp, "verify"), "criteria": criteria}


def new_tmp():
    return tempfile.mkdtemp(prefix="va_v_")
