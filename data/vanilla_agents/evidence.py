"""Staged, content-addressed evidence store (files cannot join a SQLite transaction, so publication is staged).

put():   1 write tmp + fsync  2 hash and re-read  3 publish atomically (never overwrite)  -> caller then commits the DB row + event.
read():  always re-hashes; missing or changed content raises EvidenceMissing / EvidenceTampered (never repaired silently).
scan():  reports leftover temp files, published files with no database row (orphans), missing files and tampered files.
"""
import hashlib
import os
import time

from .contracts import EvidenceConflict, EvidenceError, EvidenceMissing, EvidenceTampered


class EvidenceStore:
    def __init__(self, root):
        self.root = str(root)
        self.tmp = os.path.join(self.root, "tmp")
        os.makedirs(self.tmp, exist_ok=True)
        self.hook = None  # tests inject failures: hook(stage) may raise

    def path_for(self, h):
        if not isinstance(h, str) or len(h) != 64 or any(ch not in "0123456789abcdef" for ch in h):
            raise EvidenceError("evidence id must be 64 lowercase hex characters")
        return os.path.join(self.root, h[:2], h)

    def _stage(self, name):
        if self.hook:
            self.hook(name)

    def put(self, data):
        tmp = os.path.join(self.tmp, "t_%d_%s" % (time.time_ns(), os.urandom(4).hex()))
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        self._stage("after_tmp")
        with open(tmp, "rb") as f:
            back = f.read()
        h = hashlib.sha256(back).hexdigest()
        if h != hashlib.sha256(data).hexdigest():
            os.remove(tmp)
            raise EvidenceTampered("staged file did not read back identically")
        self._stage("after_hash")
        target = self.path_for(h)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            self._require_same(target, h)
            os.remove(tmp)
        else:
            try:
                os.rename(tmp, target)  # fails on Windows if the target appeared meanwhile: never overwrites
            except FileExistsError:
                self._require_same(target, h)
                os.remove(tmp)
        self._stage("after_publish")
        return h, len(data), os.path.relpath(target, self.root).replace("\\", "/")

    def _require_same(self, target, h):
        with open(target, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != h:
                raise EvidenceConflict("existing artifact %s does not match its name; refusing to reuse or overwrite" % h)

    def read(self, h):
        p = self.path_for(h)
        if not os.path.exists(p):
            raise EvidenceMissing("evidence %s is missing" % h)
        with open(p, "rb") as f:
            data = f.read()
        if hashlib.sha256(data).hexdigest() != h:
            raise EvidenceTampered("evidence %s content changed" % h)
        return data

    def scan(self, known):
        """known: set of hashes registered in the database."""
        tmp_files = sorted(os.listdir(self.tmp)) if os.path.isdir(self.tmp) else []
        on_disk = set()
        for d in os.listdir(self.root):
            sub = os.path.join(self.root, d)
            if d != "tmp" and os.path.isdir(sub):
                on_disk.update(os.listdir(sub))
        missing, tampered = [], []
        for h in sorted(known):
            try:
                self.read(h)
            except EvidenceMissing:
                missing.append(h)
            except EvidenceTampered:
                tampered.append(h)
        return {"tmp_files": tmp_files, "orphans": sorted(on_disk - set(known)), "missing": missing, "tampered": tampered}

    def cleanup_tmp(self, min_age_seconds=0):
        """Remove leftover temp files (interrupted publications). Published orphans are only reported, never deleted (Stage 1)."""
        removed = []
        now = time.time()
        for name in os.listdir(self.tmp):
            p = os.path.join(self.tmp, name)
            if now - os.path.getmtime(p) >= min_age_seconds:
                os.remove(p)
                removed.append(name)
        return removed
