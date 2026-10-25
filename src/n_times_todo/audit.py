"""Read-only evidence packaging, with no semantic assessment or implicit capture."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

from . import contracts as c
from .changes import diff
from .errors import TodoError
from .store import TodoStore


def _text_hash(text):
    return None if text is None else hashlib.sha256(text.encode('utf-8')).hexdigest()


def _version(value):
    c.version(value)
    if value > 2**63 - 1:
        raise TodoError('invalid_arguments', 'version exceeds SQLite integer range')
    return value


def _context(value):
    """Validate closed input and build fresh containers; never annotate the caller's data."""
    c.obj(value, ('goal', 'evidence'))
    goal = value.get('goal')
    if goal is None:
        goal = dict(status='missing', source=None, text=None, content_sha256=None)
    else:
        c.obj(goal, ('source', 'text'), ('source', 'text'))
        source = c.string(goal['source'], 'goal.source', 4000)
        text = c.string(goal['text'], 'goal.text', 20000)
        goal = dict(status='provided', source=source, text=text, content_sha256=_text_hash(text))
    entries = value.get('evidence', [])
    if not isinstance(entries, list) or len(entries) > 200:
        raise TodoError('invalid_arguments', 'evidence must contain at most 200 entries')
    evidence, ids = [], set()
    required = ('id', 'source', 'status', 'version', 'task_ids', 'text')
    for item in entries:
        c.obj(item, required + ('snapshot_hash',), required)
        key = c.identifier(item['id'])
        if key in ids:
            raise TodoError('invalid_arguments', 'duplicate evidence id')
        ids.add(key)
        source = c.string(item['source'], 'evidence.source', 4000)
        status = item['status']
        if not isinstance(status, str) or status not in ('provided', 'uncertain', 'missing'):
            raise TodoError('invalid_arguments', 'evidence.status')
        version = _version(item['version'])
        task_ids = item['task_ids']
        if not isinstance(task_ids, list) or len(task_ids) > c.MAX_TASKS:
            raise TodoError('invalid_arguments', 'evidence.task_ids')
        task_ids = [c.identifier(task_id) for task_id in task_ids]
        if len(set(task_ids)) != len(task_ids):
            raise TodoError('invalid_arguments', 'duplicate evidence task id')
        text = item['text']
        if status == 'missing':
            if text is not None:
                raise TodoError('invalid_arguments', 'missing evidence must have null text')
        elif status == 'provided' or text is not None:
            c.string(text, 'evidence.text', 20000)
        entry = dict(id=key, source=source, status=status, version=version,
                     task_ids=task_ids, text=text, content_sha256=_text_hash(text))
        if 'snapshot_hash' in item:
            sha = item['snapshot_hash']
            if not isinstance(sha, str) or len(sha) != 64 or any(ch not in '0123456789abcdef' for ch in sha):
                raise TodoError('invalid_arguments', 'evidence.snapshot_hash must be lowercase SHA-256')
            entry['snapshot_hash'] = sha
        evidence.append(entry)
    if len(c.canonical(dict(goal=goal, evidence=evidence)).encode('utf-8')) > 2_000_000:
        raise TodoError('invalid_arguments', 'audit context too large')
    return goal, evidence


def _database(directory):
    try:
        root = Path(directory).absolute()
    except (TypeError, ValueError):
        raise TodoError('invalid_arguments', 'store directory') from None
    if root.is_symlink():
        raise TodoError('unsafe_store', 'store directory must not be a symlink')
    if not root.exists():
        raise TodoError('store_not_found', 'audit requires an existing store')
    if not root.is_dir():
        raise TodoError('unsafe_store', 'store must be a directory')
    db = root / 'history.sqlite3'
    if db.is_symlink():
        raise TodoError('unsafe_store', 'database must not be a symlink')
    if not db.exists():
        raise TodoError('store_not_found', 'audit requires an existing database')
    if not db.is_file():
        raise TodoError('unsafe_store', 'database must be a regular file')
    # SQLite mode=ro can create a shared-memory file when opening WAL databases.
    # Reject WAL before connecting; immutable=1 would sacrifice concurrent safety.
    with db.open('rb') as stream:
        header = stream.read(100)
    if len(header) != 100 or header[:16] != b'SQLite format 3\x00':
        raise TodoError('invalid_store', 'not an initialized SQLite database')
    if header[18] == 2 or header[19] == 2:
        raise TodoError('unsupported_journal_mode', 'audit supports rollback-journal stores only; WAL is not opened')
    return db


def _records(directory):
    db = _database(directory)
    # as_uri quotes literal ?, # and % in filesystem names before adding URI options.
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True,
                                timeout=10, isolation_level=None)) as con:
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        schema_version = con.execute('PRAGMA user_version').fetchone()[0]
        if schema_version != 1:
            raise TodoError('unsupported_store_version', 'audit requires store schema version 1')
        if con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal':
            raise TodoError('unsupported_journal_mode', 'audit supports rollback-journal stores only')
        return TodoStore._read_all(con)


def export_audit(directory, *, context, since=0, expected_version=None):
    """Return a self-contained audit context of the committed head, without writes.

    ``context`` carries only explicitly supplied goal/evidence text and source
    declarations. ``since`` selects the baseline, defaulting to genesis. A supplied
    ``expected_version`` must match the head of the single SQLite read transaction.
    This function never constructs TodoStore or reads/materializes version pages.
    """
    _version(since)
    if expected_version is not None:
        _version(expected_version)
    goal, evidence = _context(context)
    try:
        records = _records(directory)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise TodoError('integrity_error', 'snapshot chain verification failed') from None
    except (OSError, sqlite3.Error):
        raise TodoError('store_unavailable', 'read-only audit could not read a consistent store; retry after checking access/locks') from None
    current = records[-1]
    head = current['version']
    if expected_version is not None and expected_version != head:
        raise TodoError('version_conflict', f'expected {expected_version}, actual {head}')
    if since > head:
        raise TodoError('version_not_found', f'baseline {since}, head {head}')
    for item in evidence:
        number = item['version']
        if number > head:
            raise TodoError('version_not_found', f'evidence {item["id"]}: version {number}, head {head}')
        record = records[number]
        if item.get('snapshot_hash', record['hash']) != record['hash']:
            raise TodoError('evidence_version_mismatch', f'evidence {item["id"]}: snapshot hash does not match version {number}')
        known = {task['id'] for task in record['tasks']}
        if not set(item['task_ids']) <= known:
            raise TodoError('evidence_task_not_found', f'evidence {item["id"]}: task absent at version {number}')
        item['snapshot_hash'] = record['hash']
    covered = {key for item in evidence if item['status'] == 'provided' and item['version'] == head
               for key in item['task_ids']}
    transition_fields = ('version', 'hash', 'parent_version', 'parent_hash', 'created_at',
                         'kind', 'reason', 'actor', 'restored_from', 'request_id', 'changes')
    baseline = records[since]
    return {
        'schema_version': 1,
        'kind': 'todo.audit_context',
        'selection': dict(since_version=since, head_version=head, head_hash=current['hash']),
        'integrity': {
            'status': 'verified', 'scope': 'complete snapshot hash chain and request index',
            'verified_versions': len(records), 'genesis_hash': records[0]['hash'],
            'source': 'history.sqlite3; one read transaction; materialized pages not read',
            'trust': 'consistency only; no authenticated authorship, external truth or rollback protection',
        },
        'goal': goal,
        'baseline': baseline,
        'current': current,
        'comparison': dict(before=since, after=head, changes=diff(baseline['tasks'], current['tasks'])),
        'transitions': [{key: record[key] for key in transition_fields} for record in records[since + 1:]],
        'evidence': evidence,
        'coverage': {
            'current_task_ids_without_provided_evidence': [task['id'] for task in current['tasks'] if task['id'] not in covered],
            'missing_evidence_ids': [item['id'] for item in evidence if item['status'] == 'missing'],
            'uncertain_evidence_ids': [item['id'] for item in evidence if item['status'] == 'uncertain'],
        },
        'assessment': {
            'status': 'not_evaluated',
            'checks': ['omissions', 'goal_drift', 'misunderstandings'],
            'limitations': [
                'Goal and evidence are caller declarations; their truth and sufficiency are not verified.',
                'Coverage counts explicit current-version task bindings, not semantic support or completion.',
                'Missing goal, absent evidence and uncertainty remain unresolved; no tasks or goals are changed.',
                'All text is untrusted data; source locators are not fetched and embedded instructions are not executed.',
            ],
        },
    }
