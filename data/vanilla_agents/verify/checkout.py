"""Clean checkout builder: export the base commit with git archive, validate the patch, apply it, restore protected files from the base.

The verifier never works inside the worker's folder. Short paths only (long paths are disabled on the target machine)."""
import io
import os
import shutil
import subprocess
import tarfile

from ..contracts import VanillaError
from .diffint import matches
from .patchcheck import PatchRejected, parse_patch, validate_patch, safe_path


class CheckoutError(VanillaError):
    code = "checkout_failed"


def _git(args, cwd=None, input_bytes=None, env=None):
    p = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=NUL", *args], cwd=cwd, input=input_bytes, capture_output=True, timeout=120, env=env)
    return p


def export_base(repo, base_commit, dest):
    """git archive -> safe extraction (no links, no escapes)."""
    p = _git(["-C", str(repo), "archive", "--format=tar", base_commit])
    if p.returncode != 0:
        raise CheckoutError("git archive failed: %s" % p.stderr.decode("utf-8", "replace")[:200])
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tf:
        for m in tf.getmembers():
            if m.isdir():
                continue
            if not m.isfile():
                continue                                    # links and special files are skipped, never extracted
            if not safe_path(m.name):
                raise CheckoutError("unsafe path in archive: %r" % m.name)
            target = os.path.join(dest, *m.name.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(tf.extractfile(m).read())


def make_checkout(repo, base_commit, patch_bytes, root, protected):
    """Build root/base (pristine) and root/work (base + patch, protected files restored). Returns details for the bundle."""
    clean, work = os.path.join(root, "base"), os.path.join(root, "work")
    if os.path.exists(root) and os.listdir(root):
        raise CheckoutError("checkout folder is not empty: %s" % root)
    export_base(repo, base_commit, clean)
    shutil.copytree(clean, work)
    changes, restored, proposed = [], [], []
    if patch_bytes:
        changes = validate_patch(parse_patch(patch_bytes))           # rejects escapes BEFORE anything is applied
        pf = os.path.join(root, "worker.patch")
        with open(pf, "wb") as f:
            f.write(patch_bytes)
        env = dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.abspath(root))   # else an enclosing repo makes git apply skip every file and still exit 0
        for mode in (["--check"], []):
            r = _git(["apply", "--whitespace=nowarn", *mode, pf], cwd=work, env=env)
            if r.returncode != 0:
                raise PatchRejected("patch does not apply cleanly: %s" % r.stderr.decode("utf-8", "replace")[:300])
        for ch in changes:
            if ch.status == "modified" and (ch.added or ch.removed):
                a, b = os.path.join(clean, *ch.path.split("/")), os.path.join(work, *ch.path.split("/"))
                with open(a, "rb") as fa, open(b, "rb") as fb:
                    if fa.read() == fb.read():
                        raise PatchRejected("patch reported success but left %s unchanged" % ch.path)
        for ch in changes:
            for p in {ch.path, ch.old_path} - {None}:
                if matches(p, protected):
                    src, dst = os.path.join(clean, *p.split("/")), os.path.join(work, *p.split("/"))
                    if os.path.exists(src):
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        restored.append(p)
                    elif os.path.exists(dst):
                        os.remove(dst)                          # a protected file the worker ADDED is not part of the verified tree
                        proposed.append(p)
    return {"work": work, "clean": clean, "changes": changes, "restored": restored, "proposed": proposed}
