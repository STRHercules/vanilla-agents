"""Verifier subprocess entry: python -m vanilla_agents.verify <request.json> <out.json>. Writes the result atomically; any failure leaves NO output file."""
import base64
import json
import os
import sys

from ..contracts import VanillaError
from .verifier import run_verification


def main(argv):
    req_path, out_path = argv[1], argv[2]
    with open(req_path, "r", encoding="utf-8") as f:
        req = json.load(f)
    try:
        bundle, blobs = run_verification(req)
    except VanillaError as e:
        print("verifier refused: [%s] %s" % (e.code, e), file=sys.stderr)
        return 3
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"bundle": bundle, "blobs": {h: base64.b64encode(d).decode("ascii") for h, d in blobs.items()}}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
