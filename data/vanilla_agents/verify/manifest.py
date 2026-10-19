"""Hash-pinned trusted manifest. The verifier refuses to run if any pinned file is missing or changed (D-018: detection, not prevention)."""
import hashlib
import json
import os

from ..audit import canonical
from ..contracts import VanillaError

MANIFEST_NAME = "MANIFEST.json"
CODE_FILES = ("__init__.py", "manifest.py", "patchcheck.py", "diffint.py", "redact.py", "rules.py", "bundle.py", "checkout.py", "runner.py", "verifier.py", "__main__.py")
CORE_FILES = ("__init__.py", "audit.py", "contracts.py")      # vanilla_agents modules the verifier imports (D-044)
REQUIRED_TRUSTED = ("checks.json", "protected.json")


class ManifestMismatch(VanillaError):
    code = "manifest_mismatch"


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def code_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _resolve(key, trusted_dir, code_root):
    kind, _, rel = key.partition(":")
    if kind == "trusted":
        return os.path.join(trusted_dir, rel)
    if kind == "code":
        return os.path.join(code_root, rel)
    if kind == "core":
        return os.path.join(os.path.dirname(code_root), rel)
    raise ManifestMismatch("unknown manifest entry %r" % key)


def build_manifest(trusted_dir, trusted_files, code_root=None):
    """Pin the named trusted files plus the verifier's own code."""
    code_root = code_root or code_dir()
    files = {}
    for rel in sorted(set(trusted_files) | set(REQUIRED_TRUSTED)):
        files["trusted:" + rel] = file_hash(os.path.join(trusted_dir, rel))
    for rel in CODE_FILES:
        files["code:" + rel] = file_hash(os.path.join(code_root, rel))
    for rel in CORE_FILES:
        files["core:" + rel] = file_hash(os.path.join(os.path.dirname(code_root), rel))
    return {"version": 1, "files": files}


def write_manifest(trusted_dir, trusted_files, code_root=None):
    m = build_manifest(trusted_dir, trusted_files, code_root)
    with open(os.path.join(trusted_dir, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(m, f, sort_keys=True, indent=1)
    return m


def manifest_hash(manifest):
    return hashlib.sha256(canonical(manifest).encode("utf-8")).hexdigest()


def load_manifest(trusted_dir):
    p = os.path.join(trusted_dir, MANIFEST_NAME)
    if not os.path.exists(p):
        raise ManifestMismatch("no trusted manifest at %s" % p)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def problems(manifest, trusted_dir, code_root=None):
    """List of (key, 'missing'|'changed') for every pinned file that does not match."""
    code_root = code_root or code_dir()
    bad = []
    pinned = manifest.get("files", {})
    for key in ["trusted:" + r for r in REQUIRED_TRUSTED] + ["code:" + r for r in CODE_FILES] + ["core:" + r for r in CORE_FILES]:
        if key not in pinned:
            bad.append((key, "not pinned"))
    for key, want in sorted(manifest.get("files", {}).items()):
        path = _resolve(key, trusted_dir, code_root)
        if not os.path.isfile(path):
            bad.append((key, "missing"))
        elif file_hash(path) != want:
            bad.append((key, "changed"))
    return bad


def require_manifest(trusted_dir, code_root=None, expected_hash=None):
    """Return the manifest hash, or raise ManifestMismatch listing what is wrong. Never repairs anything.
    expected_hash: a hash supplied from OUTSIDE the trusted folder (D-044); without it a rewritten manifest+files pair is not detected."""
    m = load_manifest(trusted_dir)
    bad = problems(m, trusted_dir, code_root)
    if bad:
        raise ManifestMismatch("trusted manifest check failed: %s" % ", ".join("%s %s" % (k, why) for k, why in bad))
    mh = manifest_hash(m)
    if expected_hash is not None and mh != expected_hash:
        raise ManifestMismatch("manifest hash %s does not match the expected %s" % (mh[:16], str(expected_hash)[:16]))
    return mh
