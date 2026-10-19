"""Parse and validate a unified diff BEFORE it touches any folder. Pure: no I/O."""
import re
from dataclasses import dataclass, field

from ..contracts import VanillaError

MAX_PATCH_BYTES = 5 * 1024 * 1024
MAX_FILES = 500
HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


class PatchRejected(VanillaError):
    code = "patch_rejected"


@dataclass
class FileChange:
    path: str
    old_path: str = None
    status: str = "modified"          # added | deleted | modified | renamed
    mode: str = None
    binary: bool = False
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    hunks: list = field(default_factory=list)    # [(removed, added)] per hunk, for per-hunk heuristics


def safe_path(p):
    """True if `p` is a plain relative path that stays inside the checkout."""
    if not p or "\x00" in p or "\\" in p or p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ":" in p:
        return False
    parts = p.split("/")
    if any(x in ("", ".", "..") or x.lower() in (".git", ".gitmodules") for x in parts):      # .gitignore/.github are ordinary files; only the .git folder and .gitmodules are refused
        return False
    # Windows aliases: short names (PROGRA~1), reserved devices (NUL, CON.txt), trailing dot/space (D-047)
    return not any(x != x.rstrip(" .") or re.search(r"~\d", x) or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", x) for x in parts)


def _strip(p):
    p = p.split("\t")[0].strip()
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse_patch(data):
    """Unified diff bytes -> [FileChange]. Raises PatchRejected for anything that is not a plain text diff."""
    if len(data) > MAX_PATCH_BYTES:
        raise PatchRejected("patch is larger than %d bytes" % MAX_PATCH_BYTES)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PatchRejected("patch is not valid UTF-8")
    changes, cur = [], None
    old_left = new_left = 0                         # lines the current hunk still owes; header-looking lines inside it are content
    for line in text.split("\n"):
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                new_left -= 1
                cur.added.append(line[1:])
                cur.hunks[-1][1].append(line[1:])
            elif line.startswith("-"):
                old_left -= 1
                cur.removed.append(line[1:])
                cur.hunks[-1][0].append(line[1:])
            elif not line.startswith("\\"):
                old_left -= 1
                new_left -= 1
            continue
        m = HUNK.match(line)
        if m and cur is not None:
            old_left, new_left = int(m.group(1) or 1), int(m.group(2) or 1)
            cur.hunks.append(([], []))
            continue
        if line.startswith("diff --git "):
            cur = FileChange(path="")
            changes.append(cur)
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur.status, cur.mode = "added", line.split()[-1]
        elif line.startswith("deleted file mode"):
            cur.status, cur.mode = "deleted", line.split()[-1]
        elif line.startswith("new mode") or line.startswith("old mode"):
            cur.mode = line.split()[-1]
        elif line.startswith("rename from "):
            cur.status, cur.old_path = "renamed", line[len("rename from "):].strip()
        elif line.startswith("rename to "):
            cur.path = line[len("rename to "):].strip()
        elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
            cur.binary = True
        elif line.startswith("--- "):
            old = line[4:]
            cur.old_path = None if old.startswith("/dev/null") else _strip(old)
        elif line.startswith("+++ "):
            new = line[4:]
            if new.startswith("/dev/null"):
                cur.status = "deleted"
                cur.path = cur.old_path or cur.path
            else:
                cur.path = _strip(new)
        elif line.startswith("+") and not line.startswith("+++"):
            cur.added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            cur.removed.append(line[1:])
    return changes


def validate_patch(changes):
    """Reject escapes and surprises; returns the list unchanged. Every rejection names its reason."""
    if len(changes) > MAX_FILES:
        raise PatchRejected("patch touches %d files (limit %d)" % (len(changes), MAX_FILES))
    for ch in changes:
        for p in (ch.path, ch.old_path):
            if p and not safe_path(p):
                raise PatchRejected("unsafe path in patch: %r" % p)
        if not ch.path:
            raise PatchRejected("a patch entry has no target path")
        if ch.mode == "120000":
            raise PatchRejected("symbolic links are not allowed: %s" % ch.path)
        if ch.binary:
            raise PatchRejected("binary changes are not accepted in Stage 2: %s" % ch.path)
    return changes
