"""Single-controller ownership using an OS-enforced byte-range lock (msvcrt on Windows, flock elsewhere).

The OS releases the lock when the owning process dies, so "stale lock" recovery needs no PID guessing: if we can take the lock, any
previous owner is gone. The info record (who, pid, start time, token) is informational and is written after the lock is held.
Application-authority boundary only: another process under the same Windows account can still open or delete the file (D-022).
"""
import errno
import json
import os
import time

from .contracts import ControllerBusy

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows fallback for portability of the test-suite
    msvcrt = None
    import fcntl

INFO_OFFSET = 16  # byte 0 is the locked region; info lives after it so other processes can read it


class ControllerLock:
    def __init__(self, path, controller_id):
        self.path = str(path)
        self.controller_id = controller_id
        self.fd = None
        self.previous = None  # info of the previous owner if the file already carried one (stale/takeover)
        self.token = os.urandom(8).hex()

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if msvcrt:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if e.errno in (errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", errno.EACCES)):
                raise ControllerBusy("another controller owns this database")
            raise   # anything else (antivirus, bad path, permissions) is NOT evidence of a live controller
        self.fd = fd
        os.lseek(fd, INFO_OFFSET, os.SEEK_SET)
        raw = os.read(fd, 4096)
        try:
            self.previous = json.loads(raw.decode("utf-8")) if raw.strip(b"\x00 \r\n") else None
        except (ValueError, UnicodeDecodeError):
            self.previous = {"unreadable": True}
        info = {"controller_id": self.controller_id, "pid": os.getpid(), "started_at": time.time(), "token": self.token}
        data = json.dumps(info).encode("utf-8")
        os.lseek(fd, INFO_OFFSET, os.SEEK_SET)
        os.write(fd, data)
        os.ftruncate(fd, INFO_OFFSET + len(data))
        return self

    def release(self, clear_record=True):
        if self.fd is None:
            return
        try:
            if clear_record:
                os.ftruncate(self.fd, INFO_OFFSET)  # clean shutdown: clear the owner record so only a crash leaves one behind (takeover evidence)
            os.lseek(self.fd, 0, os.SEEK_SET)
            if msvcrt:
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None

    @staticmethod
    def read_info(path):
        """Informational read of the owner record (never proves ownership)."""
        try:
            with open(path, "rb") as f:
                f.seek(INFO_OFFSET)
                raw = f.read(4096)
            return json.loads(raw.decode("utf-8")) if raw.strip(b"\x00 \r\n") else None
        except (OSError, ValueError):
            return None
