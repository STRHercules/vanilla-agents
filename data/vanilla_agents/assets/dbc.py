"""WDBC tables (DBFilesClient/*.dbc of the 1.12 client). Every field is 4 bytes; a row is a tuple of uint32 and the caller
decides which columns are strings (offset into the string block) or floats. Column layouts we use are named in COLUMNS."""
import struct


class DBCError(Exception):
    pass


class DBC:
    def __init__(self, data):
        if data[:4] != b"WDBC":
            raise DBCError("not a WDBC table")
        n, fields, size, ssize = struct.unpack_from("<4I", data, 4)
        if size != fields * 4:
            raise DBCError("row size %d is not %d fields x 4" % (size, fields))
        body = 20 + n * size
        self.fields = fields
        self.rows = [struct.unpack_from("<%dI" % fields, data, 20 + i * size) for i in range(n)]
        self._strings = data[body:body + ssize]

    def string(self, off):
        end = self._strings.find(b"\0", off)
        return self._strings[off:end if end >= 0 else None].decode("utf-8", "replace")

    @staticmethod
    def float(v):
        return struct.unpack("<f", struct.pack("<I", v))[0]

    @staticmethod
    def int(v):
        return v - (1 << 32) if v & 0x80000000 else v

    def by_id(self):
        return {r[0]: r for r in self.rows}

    def named(self, layout):
        """Rows as dicts: layout = {name: (column, 'u'|'i'|'f'|'s')}."""
        conv = {"u": lambda v: v, "i": self.int, "f": self.float, "s": self.string}
        return [{k: conv[t](r[c]) for k, (c, t) in layout.items()} for r in self.rows]


# 1.12.1 column layouts (wowdev.wiki, checked against the bundled data)
COLUMNS = {
    "ChrRaces": {"id": (0, "u"), "client_prefix": (6, "s"), "client_file": (17, "s")},
    "CharSections": {"id": (0, "u"), "race": (1, "u"), "sex": (2, "u"), "section": (3, "u"), "variation": (4, "u"), "color": (5, "u"),
                     "tex1": (6, "s"), "tex2": (7, "s"), "tex3": (8, "s"), "flags": (9, "u")},
    "CharHairGeosets": {"id": (0, "u"), "race": (1, "u"), "sex": (2, "u"), "variation": (3, "u"), "geoset": (4, "u"), "bald": (5, "u")},
    "CharacterFacialHairStyles": {"race": (0, "u"), "sex": (1, "u"), "variation": (2, "u"), "geo1": (6, "u"), "geo2": (7, "u"), "geo3": (8, "u")},
    "NameGen": {"id": (0, "u"), "name": (1, "s"), "race": (2, "u"), "sex": (3, "u")},
    "AnimationData": {"id": (0, "u"), "name": (1, "s")},
}


def load(data_dir, table):
    """DBC rows of `table` (e.g. 'CharSections') as dicts using COLUMNS[table], or the raw DBC when no layout is known."""
    raw = data_dir.read("DBFilesClient\\%s.dbc" % table)
    if raw is None:
        raise DBCError("%s.dbc is not in the data folder" % table)
    t = DBC(raw)
    return t.named(COLUMNS[table]) if table in COLUMNS else t
