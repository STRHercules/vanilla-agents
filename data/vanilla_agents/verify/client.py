"""Controller side: start the verifier as a separate process and read its result. A crashed or refused verifier yields NO bundle (nothing is promoted)."""
import base64
import json
import os
import subprocess
import sys
import tempfile

from ..contracts import VanillaError


class VerifierFailed(VanillaError):
    code = "verifier_failed"


def run_verifier(request, timeout=3900, python=None):
    """-> (bundle, blobs{hash: bytes}). Raises VerifierFailed on any non-zero exit, timeout, missing or unreadable output."""
    pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with tempfile.TemporaryDirectory(prefix="va_vr_") as d:
        req_path, out_path = os.path.join(d, "request.json"), os.path.join(d, "result.json")
        with open(req_path, "w", encoding="utf-8") as f:
            json.dump(request, f)
        env = dict(os.environ, PYTHONPATH=pkg_root, PYTHONDONTWRITEBYTECODE="1")
        try:
            p = subprocess.run([python or sys.executable, "-m", "vanilla_agents.verify", req_path, out_path], capture_output=True, timeout=timeout, env=env, cwd=pkg_root)
        except subprocess.TimeoutExpired:
            raise VerifierFailed("verifier did not finish within %ss" % timeout)
        if p.returncode != 0 or not os.path.exists(out_path):
            raise VerifierFailed("verifier exited %s: %s" % (p.returncode, p.stderr.decode("utf-8", "replace")[-300:]))
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            return doc["bundle"], {h: base64.b64decode(b) for h, b in doc["blobs"].items()}
        except (ValueError, KeyError) as e:
            raise VerifierFailed("verifier output was unreadable: %s" % e)
