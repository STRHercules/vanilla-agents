"""Append-only JSONL event feed. Ids rise by one per event across processes (OS lock), one os.write per event (atomic append),
size-capped with rotation, and every string goes through the existing redaction first. Readers pass a cursor; nothing here polls."""
import json
import os
import time
from pathlib import Path

from .errors import FeedError
from .redact_util import clean as _clean
from .schema import Event, from_dict, validate_parts

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None
    import fcntl

MAX_LINE = 16 * 1024
TAIL_BYTES = 64 * 1024


class Feed:
    def __init__(self, path, max_bytes=1_000_000, keep=3, clock=time.time):
        self.path = Path(path)
        self.max_bytes, self.keep, self.clock = max_bytes, keep, clock

    def _rotated(self, n):
        return self.path.with_name("%s.%d%s" % (self.path.stem, n, self.path.suffix))

    def _files(self):
        """Existing files, oldest first."""
        fs = [self._rotated(n) for n in range(self.keep, 0, -1)] + [self.path]
        return [f for f in fs if f.exists()]

    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
        try:
            if msvcrt:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)   # blocks inside the OS (it retries ~10 s); no loop of ours
            else:
                fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            os.close(fd)
            raise FeedError("could not lock the event feed", code="feed_locked")
        return fd

    @staticmethod
    def _unlock(fd):
        try:
            if msvcrt:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)

    def last_id(self):
        """Newest event id (0 when empty). A UI starts from derive(), then passes this as its read_since cursor."""
        for f in reversed(self._files()):
            size = f.stat().st_size
            with open(f, "rb") as fh:
                fh.seek(max(0, size - TAIL_BYTES))
                for line in reversed(fh.read().splitlines()):
                    try:
                        return int(json.loads(line)["id"])
                    except (ValueError, KeyError, TypeError):
                        continue
        return 0

    def _rotate(self):
        for n in range(self.keep, 0, -1):
            src = self.path if n == 1 else self._rotated(n - 1)
            if src.exists():
                os.replace(src, self._rotated(n))   # replaces (drops) the oldest kept file

    def append(self, event_type, task=None, agent=None, data=None):
        """Validate, redact, assign the next id and append one line. Returns the stored Event."""
        data = {} if data is None else data
        data = {k: _clean(v) for k, v in data.items()} if isinstance(data, dict) else data
        task, agent = _clean(task), _clean(agent)
        validate_parts(event_type, task, agent, data)
        fd = self._lock()
        try:
            ev = Event(self.last_id() + 1, round(self.clock(), 6), event_type, task, agent, data)
            line = (json.dumps(ev.to_dict(), separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
            if len(line) > MAX_LINE:
                raise FeedError("event larger than %d bytes" % MAX_LINE, code="event_too_large")
            if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
                self._rotate()
            prefix = b""
            if self.path.exists() and self.path.stat().st_size:   # a torn last line (crash mid-write) must not glue onto ours
                with open(self.path, "rb") as fh:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        prefix = b"\n"
            wfd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0))
            try:
                os.write(wfd, prefix + line)
            finally:
                os.close(wfd)
            return ev
        finally:
            self._unlock(fd)

    def read_since(self, cursor=0, limit=500):
        """Events with id > cursor, oldest first, at most `limit`. Torn or invalid lines are skipped. If the cursor is older than
        oldest_id() - 1, events were rotated away: resync from derive()."""
        out = []
        for f in self._files():
            try:
                raw = f.read_bytes()
            except OSError:
                continue
            for line in raw.splitlines():
                try:
                    ev = from_dict(json.loads(line))
                except Exception:
                    continue
                if ev.id > cursor:
                    out.append(ev)
        out.sort(key=lambda e: e.id)
        return out[:limit]

    def oldest_id(self):
        evs = self.read_since(0, 1)
        return evs[0].id if evs else 0
