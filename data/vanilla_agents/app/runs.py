"""Durable log of finished verification runs (D-240): append-only JSONL OUTSIDE git, one line per run, bounded by rotation.
Holds only what the feed already shows (verdicts, check ids, time, commit, short reason); never command output."""
import json
import os
from pathlib import Path

KEEP = ("run_id", "ts", "commit", "verdict", "checks", "seconds", "title", "reason")


def append(path, rec, max_bytes):
    """Add one run. When the file passes max_bytes it becomes <path>.1 (replacing the older one) and a fresh file starts."""
    p = Path(path)
    line = json.dumps({k: rec.get(k) for k in KEEP}, separators=(",", ":")) + "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        if p.stat().st_size + len(line) > max_bytes:
            os.replace(p, str(p) + ".1")
    except OSError:
        pass
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)


def read(path, limit=50):
    """Newest first, at most `limit`. Skips damaged lines; a missing file is just an empty list."""
    out = []
    for f in (str(path) + ".1", str(path)):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("run_id"):
                        out.append(r)
        except OSError:
            continue
    return out[::-1][:limit]


def last_by_check(runs):
    """{check_id: {verdict, seconds, ts}} from the newest run that contained each check. `runs` is newest first."""
    seen = {}
    for r in runs:
        for c in r.get("checks") or []:
            if isinstance(c, dict) and c.get("check_id") not in seen:
                seen[c["check_id"]] = {"verdict": c.get("status"), "seconds": c.get("seconds", r.get("seconds")), "ts": r.get("ts")}
    return seen
