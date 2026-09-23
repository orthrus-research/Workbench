"""Derived, chunk-addressable JSON pages for Core-owned check snapshots.

The full JSON value is encoded once. Section and record locators address ranges
in that stream; even a single large string spans independently verified pages.
The original archive remains authoritative. This module grants no read authority.
"""

from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import zlib

from .check_storage import canonical, ordinary

LAYOUT = "workbench-check-json-pages-v1"
PAGE_BYTES = 128 * 1024  # Representation choice, not an observation/output limit.


def record_key(key):
    """Opaque, bounded mapping address; preserves even empty or very long keys."""
    return "key:sha256:" + sha256(canonical(key)).hexdigest()


def resolve(value, path):
    for part in path:
        value = value[part]
    return value


def chunks(value):
    """Strict JSON with insertion order, finite numbers and chunked strings."""
    if isinstance(value, str):
        yield b'"'
        for start in range(0, len(value), 8192):
            yield json.encoder.encode_basestring_ascii(value[start:start + 8192])[1:-1].encode()
        yield b'"'
    elif isinstance(value, dict):
        yield b'{'
        for index, (key, item) in enumerate(value.items()):
            if not isinstance(key, str):
                raise ValueError("snapshot JSON object keys must be strings")
            if index:
                yield b','
            yield from chunks(key)
            yield b':'
            yield from chunks(item)
        yield b'}'
    elif isinstance(value, list):
        yield b'['
        for index, item in enumerate(value):
            if index:
                yield b','
            yield from chunks(item)
        yield b']'
    elif value is None or type(value) in (bool, int) or type(value) is float and math.isfinite(value):
        yield json.dumps(value, allow_nan=False, separators=(',', ':')).encode()
    else:
        raise ValueError("snapshot contains a non-JSON value")


class _Writer:
    def __init__(self, db, cancelled):
        self.db, self.cancelled = db, cancelled
        self.position, self.page = 0, 0
        self.buffer = bytearray()
        self.digest = sha256()
        self.hashes = [self.digest]

    def write(self, raw):
        for digest in self.hashes:
            digest.update(raw)
        self.position += len(raw)
        for start in range(0, len(raw), PAGE_BYTES):
            self.buffer.extend(raw[start:start + PAGE_BYTES])
            while len(self.buffer) >= PAGE_BYTES:
                self.flush(PAGE_BYTES)

    def flush(self, length=None):
        if not self.buffer:
            return
        self.cancelled()
        length = len(self.buffer) if length is None else length
        raw = bytes(self.buffer[:length])
        del self.buffer[:length]
        self.db.execute("INSERT INTO pages VALUES(?,?,?,?)",
                        (self.page, len(raw), sha256(raw).hexdigest(), zlib.compress(raw, 6)))
        self.page += 1


def build(path, value, views, cancelled=lambda: None):
    """Build one fresh database; return observed section content descriptors."""
    if path.exists() or path.is_symlink():
        raise ValueError("snapshot index destination exists")
    roots = {}
    for view in views:
        if view['state'] in {'observed', 'incomplete'} and view.get('path') is not None:
            key = tuple(view['path'])
            roots.setdefault(key, []).append(view)
            item = resolve(value, view['path'])
            if view['records'] == 'mapping' and not isinstance(item, dict):
                raise ValueError("snapshot mapping section is not an object")
            if view['records'] == 'sequence' and not isinstance(item, list):
                raise ValueError("snapshot sequence section is not an array")
    prefixes = {key[:index] for key in roots for index in range(len(key) + 1)}
    db = sqlite3.connect(path)
    observed = {}
    try:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE pages(id INTEGER PRIMARY KEY, size INTEGER NOT NULL, digest TEXT NOT NULL, payload BLOB NOT NULL)')
        db.execute('CREATE TABLE document(size INTEGER NOT NULL, digest TEXT NOT NULL)')
        db.execute('CREATE TABLE records(section TEXT NOT NULL, key TEXT NOT NULL, ordinal INTEGER NOT NULL, start INTEGER NOT NULL, size INTEGER NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(section,key)) WITHOUT ROWID')
        db.execute('CREATE UNIQUE INDEX record_order ON records(section,ordinal)')
        writer = _Writer(db, cancelled)

        def visit(item, path, addresses=()):
            sections = roots.get(path, [])
            active = sections or addresses
            start = writer.position
            digest = sha256() if active else None
            if digest is not None:
                writer.hashes.append(digest)
            if path in prefixes and isinstance(item, (dict, list)):
                mapping = isinstance(item, dict)
                writer.write(b'{' if mapping else b'[')
                for ordinal, (key, child) in enumerate(item.items() if mapping else enumerate(item)):
                    if ordinal:
                        writer.write(b',')
                    if mapping:
                        if not isinstance(key, str):
                            raise ValueError("snapshot JSON object keys must be strings")
                        for raw in chunks(key):
                            writer.write(raw)
                        writer.write(b':')
                    records = [(view['id'], record_key(key) if mapping else 'item:' + str(key), ordinal)
                               for view in sections if view['records'] != 'single']
                    visit(child, path + (key,), records)
                writer.write(b'}' if mapping else b']')
            else:
                for raw in chunks(item):
                    writer.write(raw)
            if digest is not None:
                writer.hashes.pop()
                size, checksum = writer.position - start, digest.hexdigest()
                for view in sections:
                    count = 1 if view['records'] == 'single' else len(item)
                    observed[view['id']] = {'count': count, 'content': {
                        'bytes': size, 'sha256': checksum, 'media_type': 'application/json'}}
                    if view['records'] == 'single':
                        addresses = (*addresses, (view['id'], 'value', 0))
                for section, key, ordinal in addresses:
                    db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                               (section, key, ordinal, start, size, checksum))

        visit(value, ())
        writer.flush()
        db.execute('INSERT INTO document VALUES(?,?)', (writer.position, writer.digest.hexdigest()))
        cancelled()
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError("snapshot index integrity differs")
        return observed
    finally:
        db.close()


class Reader:
    """A caller-verified immutable database. Validate pages again as consumed."""
    def __init__(self, path, cancelled=lambda: None):
        self.path = ordinary(path)
        self.stamp = self._stamp()
        self.cancelled = cancelled
        self.db = sqlite3.connect(Path(path).as_uri() + '?mode=ro&immutable=1', uri=True)
        self.db.execute('PRAGMA query_only=ON')
        self.cached = None

    def _stamp(self):
        info = ordinary(self.path).stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def check(self):
        self.cancelled()
        if self._stamp() != self.stamp:
            raise ValueError("snapshot index changed during read")

    def close(self):
        self.db.close()

    def records(self, section, offset, count):
        self.check()
        return self.db.execute('SELECT key,ordinal,start,size,digest FROM records WHERE section=? AND ordinal>=? ORDER BY ordinal LIMIT ?',
                               (section, offset, count)).fetchall()

    def record(self, section, key):
        self.check()
        row = self.db.execute('SELECT key,ordinal,start,size,digest FROM records WHERE section=? AND key=?',
                              (section, key)).fetchone()
        if row is None:
            raise ValueError("snapshot record is unavailable")
        return row

    def ranges(self, start, size):
        if type(start) is not int or type(size) is not int or start < 0 or size < 0:
            raise ValueError("invalid snapshot byte range")
        end = start + size
        while start < end:
            self.check()
            page = start // PAGE_BYTES
            if self.cached is None or self.cached[0] != page:
                row = self.db.execute('SELECT size,digest,payload FROM pages WHERE id=?', (page,)).fetchone()
                if row is None or not 0 < row[0] <= PAGE_BYTES:
                    raise ValueError("snapshot page is missing or invalid")
                decoder = zlib.decompressobj()
                raw = decoder.decompress(row[2], row[0] + 1)
                if (len(raw) != row[0] or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                        or sha256(raw).hexdigest() != row[1]):
                    raise ValueError("snapshot page content differs")
                self.cached = page, raw
            raw = self.cached[1]
            offset = start % PAGE_BYTES
            block = raw[offset:offset + min(end - start, PAGE_BYTES - offset)]
            if not block:
                raise ValueError("snapshot byte range exceeds retained content")
            yield block
            start += len(block)
        self.check()

    def value(self, row):
        raw = b''.join(self.ranges(row[2], row[3]))
        if sha256(raw).hexdigest() != row[4]:
            raise ValueError("snapshot record content differs")
        return json.loads(raw)

    def verify_document(self):
        """Independently read back all compressed bytes before publication."""
        rows = self.db.execute('SELECT size,digest FROM document').fetchall()
        if len(rows) != 1 or rows[0][0] <= 0:
            raise ValueError('snapshot document identity is missing')
        size, expected = rows[0]
        count, last = self.db.execute('SELECT count(*),max(id) FROM pages').fetchone()
        if count != (size + PAGE_BYTES - 1) // PAGE_BYTES or last != count - 1:
            raise ValueError('snapshot page coverage differs')
        digest = sha256()
        for block in self.ranges(0, size):
            digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError('snapshot complete document content differs')
        return {'sha256': expected, 'bytes': size}
