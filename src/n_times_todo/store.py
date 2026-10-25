"""Single authority: transactional append-only SQLite snapshots.

JSON/Markdown pages are automatically materialized views, not a second database.
A mirror failure never rolls back an already durable commit or asks callers to
blindly repeat the write. sync() recreates missing views, never overwrites conflicts.
"""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3

from . import contracts as c
from .changes import apply, diff
from .errors import TodoError
from .render import markdown, json_document

SCHEMA = '''
CREATE TABLE IF NOT EXISTS snapshots (
 version INTEGER PRIMARY KEY, document TEXT NOT NULL, digest TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS requests (
 request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
 version INTEGER NOT NULL REFERENCES snapshots(version));
CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots
 BEGIN SELECT RAISE(ABORT, 'immutable_snapshot'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots
 BEGIN SELECT RAISE(ABORT, 'immutable_snapshot'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_update BEFORE UPDATE ON requests
 BEGIN SELECT RAISE(ABORT, 'immutable_request'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_delete BEFORE DELETE ON requests
 BEGIN SELECT RAISE(ABORT, 'immutable_request'); END;
'''


def digest(value):
    return hashlib.sha256(c.canonical(value).encode('utf-8')).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class TodoStore:
    def __init__(self, directory):
        self.root = Path(directory).absolute()
        if self.root.is_symlink():
            raise TodoError('unsafe_store', 'store directory must not be a symlink')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise TodoError('unsafe_store')
        self.db = self.root / 'history.sqlite3'
        if self.db.is_symlink():
            raise TodoError('unsafe_store', 'database must not be a symlink')
        with closing(self._connect()) as con:
            schema_version = con.execute('PRAGMA user_version').fetchone()[0]
            if schema_version not in (0, 1):
                raise TodoError('unsupported_store_version')
            con.executescript(SCHEMA)
            con.execute('BEGIN IMMEDIATE')
            if not con.execute('SELECT 1 FROM snapshots LIMIT 1').fetchone():
                self._insert(con, [], None, 'init', '创建空清单', '', None)
            con.execute('PRAGMA user_version=1')
            con.commit()
        try:
            os.chmod(self.db, 0o600)
        except OSError:
            pass  # Documented best effort on platforms without POSIX permissions.

    def _connect(self):
        con = sqlite3.connect(self.db, timeout=10, isolation_level=None)
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA synchronous=FULL')
        return con

    @staticmethod
    def _insert(con, tasks, parent, kind, reason, actor, restored_from, request_id=None, request_digest=None):
        record = dict(schema_version=1, version=0 if parent is None else parent['version'] + 1,
                      parent_version=None if parent is None else parent['version'],
                      parent_hash=None if parent is None else parent['hash'],
                      created_at=utc_now(), kind=kind, reason=reason, actor=actor,
                      restored_from=restored_from, request_id=request_id, request_digest=request_digest, tasks=tasks,
                      changes=diff([] if parent is None else parent['tasks'], tasks))
        encoded = c.canonical(record)
        if len(encoded.encode('utf-8')) > 2_000_000:
            raise TodoError('snapshot_too_large', 'full snapshot exceeds 2 MB; transaction not committed')
        sha = digest(record)
        con.execute('INSERT INTO snapshots VALUES (?, ?, ?)',
                    (record['version'], encoded, sha))
        return {**record, 'hash': sha}

    @staticmethod
    def _read_all(con):
        # Both snapshots and the request index must be read from one SQLite view.
        if not con.in_transaction:
            con.execute('BEGIN')
        result = []
        try:
            for number, text, sha in con.execute('SELECT version, document, digest FROM snapshots ORDER BY version'):
                record = c.decode(text)
                required = {'schema_version', 'version', 'parent_version', 'parent_hash', 'created_at',
                            'kind', 'reason', 'actor', 'restored_from', 'request_id', 'request_digest', 'tasks', 'changes'}
                if not isinstance(record, dict) or set(record) != required:
                    raise ValueError('shape')
                previous = result[-1] if result else None
                if (type(number) is not int or number != len(result) or record['version'] != number or
                    type(record['version']) is not int or type(record['schema_version']) is not int or record['schema_version'] != 1 or
                    digest(record) != sha or text != c.canonical(record) or
                    record['parent_version'] != (None if previous is None else previous['version']) or
                    record['parent_hash'] != (None if previous is None else previous['hash']) or
                    record['tasks'] != c.tasks(record['tasks']) or
                    record['changes'] != diff([] if previous is None else previous['tasks'], record['tasks'])):
                    raise ValueError('chain')
                if number == 0 and (record['kind'] != 'init' or record['tasks'] or record['request_id'] is not None or record['request_digest'] is not None):
                    raise ValueError('genesis')
                if record['kind'] not in ('init', 'update', 'restore') or (number and record['kind'] == 'init'):
                    raise ValueError('kind')
                if record['kind'] == 'restore':
                    source = record['restored_from']
                    if type(source) is not int or not 0 <= source < number or record['tasks'] != result[source]['tasks']:
                        raise ValueError('restore')
                elif record['restored_from'] is not None:
                    raise ValueError('source')
                if number:
                    c.identifier(record['request_id'])
                    if not isinstance(record['request_digest'], str) or len(record['request_digest']) != 64 or any(ch not in '0123456789abcdef' for ch in record['request_digest']):
                        raise ValueError('request digest')
                result.append({**record, 'hash': sha})
            if not result:
                raise ValueError('missing genesis')
            index = con.execute('SELECT request_id, request_digest, version FROM requests ORDER BY version').fetchall()
            expected = [(r['request_id'], r['request_digest'], r['version']) for r in result[1:]]
            if index != expected:
                raise ValueError('request index binding')
        except (ValueError, TypeError, KeyError, TodoError):
            raise TodoError('integrity_error', 'snapshot chain verification failed') from None
        return result

    def get(self, version=None):
        if version is not None:
            c.version(version)
        with closing(self._connect()) as con:
            records = self._read_all(con)
        number = len(records) - 1 if version is None else version
        if number >= len(records):
            raise TodoError('version_not_found')
        return records[number]

    def history(self, after=-1, limit=100):
        if type(after) is not int or after < -1 or type(limit) is not int or not 1 <= limit <= 1000:
            raise TodoError('invalid_arguments', 'history cursor/limit')
        with closing(self._connect()) as con:
            records = self._read_all(con)
        selected = [r for r in records if r['version'] > after][:limit]
        return {'versions': [{k: r[k] for k in ('version', 'created_at', 'kind', 'reason', 'hash', 'parent_hash')}
                             for r in selected],
                'next_after': selected[-1]['version'] if selected and selected[-1]['version'] < len(records)-1 else None}

    def compare(self, before, after):
        c.version(before)
        c.version(after)
        return {'before': before, 'after': after, 'changes': diff(self.get(before)['tasks'], self.get(after)['tasks'])}

    def update(self, *, changes, expected_version, request_id, reason='', actor=''):
        return self._write('update', dict(changes=changes), expected_version, request_id, reason, actor)

    def restore(self, *, version, expected_version, request_id, reason='', actor=''):
        c.version(version)
        return self._write('restore', dict(version=version), expected_version, request_id, reason, actor)

    def _write(self, kind, payload, expected_version, request_id, reason, actor):
        c.version(expected_version)
        c.identifier(request_id)
        c.string(reason, 'reason', 4000, True)
        c.string(actor, 'actor', 200, True)
        try:
            request_sha = digest(dict(kind=kind, payload=payload, expected_version=expected_version,
                                      reason=reason, actor=actor))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise TodoError('invalid_arguments', 'request serialization') from None
        con = self._connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            records = self._read_all(con)
            prior = con.execute('SELECT request_digest, version FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior[0] != request_sha:
                    raise TodoError('request_conflict')
                snapshot, replayed = records[prior[1]], True
            else:
                head = records[-1]
                if head['version'] != expected_version:
                    raise TodoError('version_conflict', f'expected {expected_version}, actual {head["version"]}')
                if kind == 'update':
                    updated = apply(head['tasks'], payload['changes'], request_id,
                                    {t['id'] for record in records for t in record['tasks']})
                    restored_from = None
                else:
                    restored_from = payload['version']
                    if restored_from >= len(records):
                        raise TodoError('version_not_found')
                    updated = records[restored_from]['tasks']
                snapshot = self._insert(con, updated, head, kind, reason, actor, restored_from, request_id, request_sha)
                con.execute('INSERT INTO requests VALUES (?, ?, ?)', (request_id, request_sha, snapshot['version']))
                replayed = False
            con.commit()
        except sqlite3.OperationalError as exc:
            con.rollback()
            raise TodoError('store_unavailable', 'transaction did not complete; inspect/retry same request_id') from exc
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        # The durable snapshot is committed before optional materialization.
        try:
            view = self.sync()
        except (OSError, sqlite3.Error, TodoError) as error:
            view = {'status': 'pending', 'written': 0,
                    'errors': [error.code if isinstance(error, TodoError) else 'materialization_unavailable']}
        return {'snapshot': snapshot, 'replayed': replayed, 'views': view}

    def verify(self):
        with closing(self._connect()) as con:
            records = self._read_all(con)
            requests = con.execute('SELECT request_id, request_digest, version FROM requests').fetchall()
            if any(type(v) is not int or not 1 <= v < len(records) for _, _, v in requests):
                raise TodoError('integrity_error', 'request index')
        return {'ok': True, 'versions': len(records), 'head_version': records[-1]['version'],
                'head_hash': records[-1]['hash'], 'requests': len(requests),
                'trust': 'hash chain detects alteration, not authenticated authorship or rollback to a whole older database'}

    def sync(self):
        """Repair absent materialized pages. Conflicting existing bytes are not overwritten."""
        with closing(self._connect()) as con:
            records = self._read_all(con)
        directory = self.root / 'versions'
        errors = []
        try:
            if directory.is_symlink():
                raise OSError('symlink view directory')
            directory.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            return {'status': 'pending', 'errors': ['view_directory_unavailable']}
        written = 0
        for record in records:
            stem = f'{record["version"]:08d}'
            for suffix, content in [('json', json_document(record)), ('md', markdown(record))]:
                path = directory / f'{stem}.{suffix}'
                data = content.encode('utf-8')
                try:
                    if path.is_symlink():
                        raise OSError('symlink view')
                    if path.exists():
                        if not path.is_file() or path.read_bytes() != data:
                            errors.append(f'view_conflict:{path.name}')
                        continue
                    # Link an fsynced, exclusive temporary file into place: no partial final page.
                    import tempfile
                    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=directory)
                    try:
                        with os.fdopen(fd, 'wb') as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                        try:
                            os.link(temporary, path)
                            written += 1
                        except FileExistsError:
                            if path.is_symlink() or path.read_bytes() != data:
                                errors.append(f'view_conflict:{path.name}')
                    finally:
                        os.unlink(temporary)
                except OSError:
                    errors.append(f'view_unavailable:{path.name}')
        return {'status': 'ready' if not errors else 'pending', 'written': written, 'errors': errors}
