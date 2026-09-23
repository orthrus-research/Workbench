"""Immutable, paged diagnostic delivery inside an existing Core check attempt.

Domain owners supply meaning and source bindings. Publication is the last write;
readers never build a recipe index or resume the executing operation.
"""

from hashlib import sha256
import re

from . import check_storage as storage
from .host_filesystem import fsync_directory

DIRECTORY = 'diagnostic-delivery'
FORMAT = 'workbench-check-diagnostics-v1'
PAGE_RECORDS = 128  # Presentation granularity, never an observation limit.


def _sealed(path, kind):
    value = storage.read_json(path, byte_limit=None)
    if storage.seal(kind, {k: v for k, v in value.items() if k != 'id'}) != value:
        raise ValueError('diagnostic delivery identity changed')
    return value


def publish(attempt, *, binding, summary, streams):
    directory = attempt / DIRECTORY
    directory.mkdir(mode=0o700)
    fsync_directory(attempt)
    sections = {}
    for name, values in sorted(streams.items()):
        if not re.fullmatch('[a-z][a-z-]*', name) or not isinstance(values, list):
            raise ValueError('invalid diagnostic stream')
        pages = []
        for offset in range(0, len(values), PAGE_RECORDS):
            page = {'offset': offset, 'records': values[offset:offset + PAGE_RECORDS]}
            raw = storage.canonical(page) + b'\n'
            path = f'{name}-{offset}.json'
            storage.write_bytes(directory / path, raw, byte_limit=None)
            pages.append({'path': path, 'bytes': len(raw), 'sha256': sha256(raw).hexdigest(),
                          'offset': offset, 'count': len(page['records'])})
        sections[name] = {'count': len(values), 'pages': pages}
    record = storage.seal('check-diagnostics', {'format': FORMAT, 'binding': binding,
                                               'summary': summary, 'streams': sections})
    storage.write_json(directory / 'publication.json', record, byte_limit=None)
    return record


def publication(attempt, *, binding, revision=None):
    record = _sealed(attempt / DIRECTORY / 'publication.json', 'check-diagnostics')
    if (record.get('format') != FORMAT or record.get('binding') != binding
            or revision is not None and revision != record['id']):
        raise ValueError('diagnostic delivery belongs to another input or revision')
    for name, section in record['streams'].items():
        if not re.fullmatch('[a-z][a-z-]*', name):
            raise ValueError('invalid diagnostic section')
        offset = 0
        for page in section['pages']:
            if (page['path'] != f'{name}-{offset}.json' or page['offset'] != offset
                    or type(page['count']) is not int or not 0 < page['count'] <= PAGE_RECORDS
                    or not re.fullmatch('[0-9a-f]{64}', page['sha256'])):
                raise ValueError('invalid diagnostic page sequence')
            offset += page['count']
        if type(section['count']) is not int or offset != section['count']:
            raise ValueError('diagnostic stream count changed')
    return record


def page(attempt, record, section, offset=0):
    stream = record['streams'].get(section)
    if stream is None or type(offset) is not int or not 0 <= offset <= stream['count']:
        raise ValueError('select an exact diagnostic stream offset')
    if offset == stream['count']:
        return {'offset': offset, 'records': [], 'total': offset, 'next_offset': None}
    descriptor = next(row for row in stream['pages'] if row['offset'] <= offset < row['offset'] + row['count'])
    raw = storage.ordinary(attempt / DIRECTORY / descriptor['path']).read_bytes()
    if len(raw) != descriptor['bytes'] or sha256(raw).hexdigest() != descriptor['sha256']:
        raise ValueError('diagnostic page content changed')
    import json
    value = json.loads(raw)
    if value['offset'] != descriptor['offset'] or len(value['records']) != descriptor['count']:
        raise ValueError('diagnostic page count changed')
    rows = value['records'][offset - value['offset']:]
    end = offset + len(rows)
    return {'offset': offset, 'records': rows, 'total': stream['count'],
            'next_offset': None if end == stream['count'] else end}


def item(attempt, record, section, offset):
    values = page(attempt, record, section, offset)['records']
    if not values:
        raise ValueError('diagnostic record does not exist')
    return values[0]


def retained_files(attempt):
    """Include complete and interrupted delivery files in the attempt's custody."""
    directory = attempt / DIRECTORY
    if not directory.exists():
        return {}
    storage.ordinary(directory, directory=True)
    files = {}
    for path in sorted(directory.iterdir()):
        if not (path.name == 'publication.json' or re.fullmatch('[a-z][a-z-]*-[0-9]+[.]json', path.name)
                or re.fullmatch('[.]record-[0-9a-f]{32}', path.name)):
            raise ValueError('unknown diagnostic delivery file')
        storage.ordinary(path)
        files['diagnostic-delivery-' + path.name] = DIRECTORY + '/' + path.name
    return files
