"""Disk preflight: refuse to start when the target drive is too full. The threshold is a parameter (C: is nearly full on the dev machine)."""
import os
import shutil

from ..contracts import VanillaError, ValidationError


class DiskSpaceLow(VanillaError):
    code = "disk_space_low"


def check_disk(path, min_free_bytes, usage=shutil.disk_usage):
    """Return free bytes on the drive holding `path`; raise DiskSpaceLow if below min_free_bytes. `usage` is injectable for tests."""
    if isinstance(min_free_bytes, bool) or not isinstance(min_free_bytes, int) or min_free_bytes < 0:
        raise ValidationError("min_free_bytes must be a non-negative integer")
    probe = os.path.abspath(str(path))
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:   # a not-yet-created target is measured on its nearest existing parent
        probe = os.path.dirname(probe)
    free = usage(probe).free
    if free < min_free_bytes:
        raise DiskSpaceLow("only %d bytes free on the drive of %s, need %d" % (free, probe, min_free_bytes))
    return free
