"""Read-only MPQ archives (format v0/v1, the vanilla 1.12 client) with the standard library only.
Files are found by path (case and slash direction do not matter). Supported storage: plain, zlib, bzip2 (all a 1.12 data
folder needs; the bundled patch.MPQ/sound.MPQ are 100% zlib). ponytail: PKWARE implode, Huffman/ADPCM and encrypted files
raise MPQError; add them only if an archive actually uses them."""
import bz2
import fnmatch
import os
import struct
import zlib

FLAG_IMPLODE, FLAG_COMPRESS, FLAG_ENCRYPTED, FLAG_SINGLE, FLAG_CRC, FLAG_EXISTS = 0x100, 0x200, 0x10000, 0x1000000, 0x4000000, 0x80000000


class MPQError(Exception):
    pass


def _crypt_table():
    seed, table = 0x00100001, [0] * 0x500
    for i in range(0x100):
        for j in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            hi = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            table[i + j * 0x100] = hi | (seed & 0xFFFF)
    return table


_CRYPT = _crypt_table()


def hash_string(s, kind):
    """kind 0 = table offset, 1 = name A, 2 = name B, 3 = file key."""
    seed1, seed2 = 0x7FED7FED, 0xEEEEEEEE
    for ch in s.upper().replace("/", "\\").encode("latin-1"):
        seed1 = _CRYPT[(kind << 8) + ch] ^ ((seed1 + seed2) & 0xFFFFFFFF)
        seed2 = (ch + seed1 + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
    return seed1


def decrypt(data, key):
    n = len(data) // 4
    words = struct.unpack("<%dI" % n, data[:n * 4])
    seed, out = 0xEEEEEEEE, []
    for w in words:
        seed = (seed + _CRYPT[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
        v = w ^ ((key + seed) & 0xFFFFFFFF)
        key = (((~key << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
        seed = (v + seed + (seed << 5) + 3) & 0xFFFFFFFF
        out.append(v)
    return struct.pack("<%dI" % n, *out)


def _decompress(buf, size):
    if len(buf) >= size:
        return buf[:size]
    kind, body = buf[0], buf[1:]
    if kind == 0x02:
        return zlib.decompress(body)
    if kind == 0x10:
        return bz2.decompress(body)
    raise MPQError("unsupported MPQ compression 0x%02x" % kind)


class Archive:
    def __init__(self, path):
        self.path = path
        self._f = open(path, "rb")
        try:
            self._open()
        except BaseException:
            self._f.close()
            raise

    def _open(self):
        path = self.path
        head = self._f.read(32)
        self.base = 0
        if head[:4] == b"MPQ\x1b":                       # user-data block in front of the real header
            self.base = struct.unpack_from("<I", head, 8)[0]
            self._f.seek(self.base)
            head = self._f.read(32)
        if head[:4] != b"MPQ\x1a":
            raise MPQError("%s is not an MPQ archive" % path)
        _, _, _, ver, shift, hpos, bpos, hcount, bcount = struct.unpack("<4sIIHHIIII", head)
        if ver > 1:
            raise MPQError("%s: MPQ format v%d is not supported (vanilla archives are v0/v1)" % (path, ver))
        self.sector = 512 << shift
        self._hash = self._table(hpos, hcount, "(hash table)")
        self._block = self._table(bpos, bcount, "(block table)")

    def _table(self, pos, count, name):
        self._f.seek(self.base + pos)
        raw = decrypt(self._f.read(count * 16), hash_string(name, 3))
        return [struct.unpack_from("<4I", raw, i * 16) for i in range(count)]

    def close(self):
        self._f.close()

    def _find(self, name):
        n = len(self._hash)
        i = start = hash_string(name, 0) % n
        a, b = hash_string(name, 1), hash_string(name, 2)
        while True:
            ha, hb, _, block = self._hash[i]
            if block == 0xFFFFFFFF:
                return None
            if ha == a and hb == b and block != 0xFFFFFFFE:
                return self._block[block]
            i = (i + 1) % n
            if i == start:
                return None

    def has(self, name):
        e = self._find(name)
        return bool(e and e[3] & FLAG_EXISTS)

    def read(self, name):
        """File bytes, or None when the archive does not hold it."""
        e = self._find(name)
        if not e or not e[3] & FLAG_EXISTS:
            return None
        off, csize, size, flags = e
        if flags & (FLAG_ENCRYPTED | FLAG_IMPLODE):
            raise MPQError("%s: encrypted/imploded files are not supported" % name)
        self._f.seek(self.base + off)
        raw = self._f.read(csize)
        if not flags & FLAG_COMPRESS:
            return raw[:size]
        if flags & FLAG_SINGLE:
            return _decompress(raw, size)
        ns = (size + self.sector - 1) // self.sector
        offs = struct.unpack_from("<%dI" % (ns + 1), raw)
        out = bytearray()
        for i in range(ns):
            want = min(self.sector, size - i * self.sector)
            out += _decompress(raw[offs[i]:offs[i + 1]], want)
        return bytes(out)

    def names(self):
        """Paths from the archive's (listfile); [] when it has none."""
        d = self.read("(listfile)") or b""
        return [ln.strip() for ln in d.decode("latin-1").splitlines() if ln.strip()]


class DataDir:
    """Every *.MPQ of a client Data folder, searched like the game does: patch archives first (patch-3 before patch-2 before patch)."""

    def __init__(self, folder):
        files = [f for f in os.listdir(folder) if f.lower().endswith(".mpq")]
        if not files:
            raise MPQError("no .MPQ files in %s (point data_dir at the client's Data folder)" % folder)
        files.sort(key=lambda f: (not f.lower().startswith("patch"), [-ord(c) for c in f.lower()[:-4]] + [0]))   # patch-3, patch-2, patch, then base
        self.archives = []
        try:
            for f in files:
                self.archives.append(Archive(os.path.join(folder, f)))
        except BaseException:
            self.close()
            raise

    def read(self, name):
        for a in self.archives:
            d = a.read(name)
            if d is not None:
                return d
        return None

    def has(self, name):
        return any(a.has(name) for a in self.archives)

    def names(self, pattern="*"):
        seen, out = set(), []
        pat = pattern.lower().replace("/", "\\")
        for a in self.archives:
            for n in a.names():
                k = n.lower()
                if k not in seen and fnmatch.fnmatchcase(k, pat):
                    seen.add(k)
                    out.append(n)
        return out

    def close(self):
        for a in self.archives:
            a.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
