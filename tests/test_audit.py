"""Synthetic audit export acceptance tests; no model, credentials or network."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from n_times_todo import TodoError, TodoStore, export_audit
from n_times_todo.changes import diff
from n_times_todo.cli import main


def fingerprint(root):
    """Observe content, names, permissions and modification times, ignoring atime."""
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mode,
            path.lstat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
        for path in [root, *sorted(root.rglob('*'))]
    }


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TodoStore(self.root / 'history')

    def update(self, changes, version=0, request_id='first'):
        return self.store.update(changes=changes, expected_version=version,
                                 request_id=request_id)['snapshot']

    def add(self, task_id='alpha', title='合成任务'):
        return {'op': 'add', 'task': {'id': task_id, 'title': title}}

    def evidence(self, **changes):
        return {**{'id': 'report', 'source': 'synthetic:test-run-1',
                   'status': 'provided', 'version': 1, 'task_ids': ['alpha'],
                   'text': '合成运行报告：通过。'}, **changes}

    def export(self, context=None, **kwargs):
        return export_audit(self.store.root,
                            context={} if context is None else context, **kwargs)

    def assertError(self, code, function, *args, **kwargs):
        with self.assertRaises(TodoError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_complete_snapshots_diff_and_stable_evidence_bindings(self):
        original = self.update([self.add()])
        current = self.update([
            {'op': 'edit', 'id': 'alpha',
             'set': {'title': '改名后的同一任务', 'status': 'done', 'notes': '完整保留的备注'}},
            self.add('beta', '未验证事项'),
        ], 1, 'second')
        context = {
            'goal': {'source': 'synthetic:brief-1', 'text': '验证并保留完整清单。'},
            'evidence': [self.evidence(version=2, snapshot_hash=current['hash'])],
        }
        package = self.export(context, since=1, expected_version=2)
        self.assertEqual(package['schema_version'], 1)
        self.assertEqual(package['kind'], 'todo.audit_context')
        self.assertEqual(package['selection'], {
            'since_version': 1, 'head_version': 2, 'head_hash': current['hash']})
        self.assertEqual(package['baseline'], original)
        self.assertEqual(package['current'], current)
        self.assertEqual(package['comparison'], {
            'before': 1, 'after': 2, 'changes': diff(original['tasks'], current['tasks'])})
        self.assertEqual(package['evidence'][0]['snapshot_hash'], current['hash'])
        self.assertEqual(package['evidence'][0]['content_sha256'],
                         hashlib.sha256(context['evidence'][0]['text'].encode()).hexdigest())
        self.assertEqual(package['coverage']['current_task_ids_without_provided_evidence'], ['beta'])
        self.assertEqual(package['assessment']['status'], 'not_evaluated')
        self.assertEqual(set(package['assessment']['checks']),
                         {'omissions', 'goal_drift', 'misunderstandings'})

    def test_intermediate_changes_survive_empty_endpoint_diff(self):
        self.update([self.add()])
        self.update([{'op': 'remove', 'id': 'alpha'}], 1, 'remove')
        package = self.export()
        self.assertFalse(any(package['comparison']['changes'].values()))
        self.assertEqual([item['version'] for item in package['transitions']], [1, 2])
        self.assertEqual(package['transitions'][0]['changes']['added'][0]['id'], 'alpha')
        self.assertEqual(package['transitions'][1]['changes']['removed'][0]['id'], 'alpha')
        self.assertEqual(package['transitions'][1]['parent_hash'], package['transitions'][0]['hash'])

    def test_restore_is_visible_and_baseline_can_equal_head(self):
        first = self.update([self.add()])
        self.update([{'op': 'remove', 'id': 'alpha'}], 1, 'remove')
        restored = self.store.restore(version=1, expected_version=2,
                                      request_id='restore')['snapshot']
        package = self.export(since=1)
        self.assertEqual(package['current']['tasks'], first['tasks'])
        self.assertEqual(package['transitions'][-1]['kind'], 'restore')
        self.assertEqual(package['transitions'][-1]['restored_from'], 1)
        self.assertEqual(package['current'], restored)
        same = self.export(since=3)
        self.assertEqual(same['transitions'], [])
        self.assertEqual(same['baseline'], same['current'])

    def test_missing_goal_and_explicit_uncertainty_are_preserved(self):
        self.update([self.add(), self.add('beta')])
        package = self.export({'evidence': [
            self.evidence(id='missing', status='missing', text=None),
            self.evidence(id='unconfirmed', status='uncertain', text='结果尚待确认。', task_ids=['beta']),
            self.evidence(id='unknown', status='uncertain', text=None, task_ids=[]),
        ]})
        self.assertEqual(package['goal'], {
            'status': 'missing', 'source': None, 'text': None, 'content_sha256': None})
        self.assertEqual(package['coverage']['missing_evidence_ids'], ['missing'])
        self.assertEqual(package['coverage']['uncertain_evidence_ids'], ['unconfirmed', 'unknown'])
        self.assertEqual(package['coverage']['current_task_ids_without_provided_evidence'], ['alpha', 'beta'])
        self.assertIsNone(package['evidence'][0]['content_sha256'])
        self.assertEqual(package['assessment']['status'], 'not_evaluated')

    def test_old_and_notebook_level_evidence_do_not_imply_current_task_coverage(self):
        self.update([self.add()])
        self.update([{'op': 'edit', 'id': 'alpha', 'set': {'notes': '新版本'}}], 1, 'second')
        package = self.export({'evidence': [
            self.evidence(id='old', version=1),
            self.evidence(id='notebook', version=2, task_ids=[]),
        ]})
        self.assertEqual(package['coverage']['current_task_ids_without_provided_evidence'], ['alpha'])
        self.assertEqual(package['evidence'][0]['version'], 1)
        self.assertEqual(package['evidence'][0]['snapshot_hash'], self.store.get(1)['hash'])

    def test_evidence_can_refer_to_removed_task_in_its_original_version(self):
        self.update([self.add()])
        self.update([{'op': 'remove', 'id': 'alpha'}], 1, 'remove')
        package = self.export({'evidence': [self.evidence()]}, since=2)
        self.assertEqual(package['current']['tasks'], [])
        self.assertEqual(package['evidence'][0]['task_ids'], ['alpha'])
        self.assertEqual(package['evidence'][0]['version'], 1)

    def test_input_and_store_are_unchanged_and_views_are_not_repaired(self):
        self.update([self.add()])
        page = self.store.root / 'versions' / '00000001.md'
        page.write_text('synthetic conflicting old page', encoding='utf-8')
        (self.store.root / 'versions' / '00000000.json').unlink()
        evidence_path = self.root / 'evidence.json'
        context = {'goal': {'source': 'synthetic:brief', 'text': '原始目标'},
                   'evidence': [self.evidence()]}
        evidence_path.write_text(json.dumps(context), encoding='utf-8')
        original_input = copy.deepcopy(context)
        before = fingerprint(self.root)
        first = self.export(context)
        second = self.export(context)
        self.assertEqual(first, second)
        self.assertEqual(context, original_input)
        self.assertEqual(fingerprint(self.root), before)
        self.assertEqual(page.read_text(), 'synthetic conflicting old page')
        self.assertFalse((self.store.root / 'versions' / '00000000.json').exists())

    def test_source_is_a_declaration_and_text_is_inert(self):
        self.update([self.add()])
        external = self.root / 'source.txt'
        external.write_text('THIS MUST NOT BE AUTOMATICALLY IMPORTED', encoding='utf-8')
        text = '<script>run()</script> $(touch never) [image](https://invalid.example/x)'
        package = self.export({
            'goal': {'source': 'https://invalid.example/brief', 'text': text},
            'evidence': [self.evidence(source=str(external), text=text)],
        })
        self.assertEqual(package['goal']['text'], text)
        self.assertEqual(package['evidence'][0]['source'], str(external))
        self.assertEqual(package['evidence'][0]['text'], text)
        self.assertNotIn('THIS MUST NOT BE AUTOMATICALLY IMPORTED', json.dumps(package))
        self.assertFalse((self.root / 'never').exists())

    def test_missing_store_is_never_initialized(self):
        absent = self.root / 'absent' / 'nested'
        self.assertError('store_not_found', export_audit, absent, context={})
        self.assertFalse((self.root / 'absent').exists())
        empty = self.root / 'empty'
        empty.mkdir()
        before = fingerprint(empty)
        self.assertError('store_not_found', export_audit, empty, context={})
        self.assertEqual(fingerprint(empty), before)

    def test_empty_and_non_sqlite_database_are_rejected_without_writes(self):
        for data in [b'', b'synthetic non-SQLite input']:
            with self.subTest(data=data):
                directory = self.root / ('empty-db' if not data else 'garbage-db')
                directory.mkdir()
                (directory / 'history.sqlite3').write_bytes(data)
                before = fingerprint(directory)
                self.assertError('invalid_store', export_audit, directory, context={})
                self.assertEqual(fingerprint(directory), before)

    def test_future_schema_is_not_downgraded(self):
        with sqlite3.connect(self.store.db) as con:
            con.execute('PRAGMA user_version=2')
        before = fingerprint(self.store.root)
        self.assertError('unsupported_store_version', self.export)
        self.assertEqual(fingerprint(self.store.root), before)

    def test_wal_is_rejected_without_creating_shared_memory_or_other_files(self):
        with sqlite3.connect(self.store.db) as con:
            self.assertEqual(con.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        con.close()
        # Closing the final connection normally removes WAL/SHM; the header stays WAL.
        self.assertFalse(Path(str(self.store.db) + '-shm').exists())
        before = fingerprint(self.store.root)
        self.assertError('unsupported_journal_mode', self.export)
        self.assertEqual(fingerprint(self.store.root), before)

    def test_database_hash_tampering_is_rejected(self):
        self.update([self.add()])
        with sqlite3.connect(self.store.db) as con:
            con.execute('DROP TRIGGER snapshots_no_update')
            con.execute("UPDATE snapshots SET digest='bad' WHERE version=1")
        before = fingerprint(self.store.root)
        self.assertError('integrity_error', self.export)
        self.assertEqual(fingerprint(self.store.root), before)

    def test_request_index_tampering_is_rejected(self):
        self.update([self.add()])
        with sqlite3.connect(self.store.db) as con:
            con.execute('DROP TRIGGER requests_no_update')
            con.execute("UPDATE requests SET request_digest=?", ('0' * 64,))
        before = fingerprint(self.store.root)
        self.assertError('integrity_error', self.export)
        self.assertEqual(fingerprint(self.store.root), before)

    def test_version_mismatches_are_explicit(self):
        self.update([self.add()])
        before = fingerprint(self.store.root)
        self.assertError('version_conflict', self.export, expected_version=0)
        self.assertError('version_not_found', self.export, since=2)
        self.assertError('version_not_found', self.export,
                         {'evidence': [self.evidence(version=2)]})
        self.assertError('evidence_version_mismatch', self.export,
                         {'evidence': [self.evidence(snapshot_hash='0' * 64)]})
        self.assertError('evidence_task_not_found', self.export,
                         {'evidence': [self.evidence(task_ids=['unknown'])]})
        self.assertError('evidence_task_not_found', self.export,
                         {'evidence': [self.evidence(version=0)]})
        self.assertEqual(fingerprint(self.store.root), before)

    def test_closed_context_and_evidence_contracts(self):
        self.update([self.add()])
        bad_contexts = [
            [], 'text', {'unknown': True}, {'goal': {}},
            {'goal': {'source': '', 'text': 'goal'}},
            {'goal': {'source': 'brief', 'text': 'goal', 'extra': 1}},
            {'goal': {'source': 'brief', 'text': 'x' * 20001}},
            {'evidence': {}}, {'evidence': [None]},
            {'evidence': [self.evidence(status='verified')]},
            {'evidence': [self.evidence(status='missing', text='contradiction')]},
            {'evidence': [self.evidence(text=None)]},
            {'evidence': [self.evidence(text='')]},
            {'evidence': [self.evidence(source='')]},
            {'evidence': [self.evidence(task_ids=['alpha', 'alpha'])]},
            {'evidence': [self.evidence(task_ids='alpha')]},
            {'evidence': [self.evidence(version=True)]},
            {'evidence': [self.evidence(extra='closed')]},
            {'evidence': [self.evidence(snapshot_hash='malformed')]},
            {'evidence': [self.evidence(), self.evidence()]},
            {'evidence': [self.evidence(text='\ud800')]},
            {'evidence': [self.evidence(text='nul\x00byte')]},
            {'evidence': [self.evidence(id='bad id')]},
            {'evidence': [self.evidence(id=f'e-{n}') for n in range(201)]},
            {'evidence': [self.evidence(id=f'e-{n}', text='x' * 19999) for n in range(105)]},
        ]
        before = fingerprint(self.store.root)
        self.assertError('invalid_arguments', export_audit, self.store.root, context=None)
        for context in bad_contexts:
            with self.subTest(context=repr(context)[:160]):
                self.assertError('invalid_arguments', self.export, context)
        self.assertEqual(fingerprint(self.store.root), before)

    def test_versions_reject_bool_negative_and_wrong_types(self):
        for value in [True, -1, 1.0, '0', [], {}]:
            with self.subTest(value=value):
                self.assertError('invalid_arguments', self.export, since=value)
                self.assertError('invalid_arguments', self.export, expected_version=value)

    def test_python_version_integers_must_fit_sqlite(self):
        self.update([self.add()])
        before = fingerprint(self.store.root)
        for value in (2**63, 10**5000):
            self.assertError('invalid_arguments', self.export, since=value)
            self.assertError('invalid_arguments', self.export, expected_version=value)
            self.assertError('invalid_arguments', self.export,
                             {'evidence': [self.evidence(version=value)]})
        self.assertEqual(fingerprint(self.store.root), before)

    def test_invalid_unicode_in_stored_document_is_an_integrity_error(self):
        self.update([self.add()])
        with sqlite3.connect(self.store.db) as con:
            document = json.loads(con.execute('SELECT document FROM snapshots WHERE version=1').fetchone()[0])
            document['reason'] = '\ud800'
            con.execute('DROP TRIGGER snapshots_no_update')
            con.execute('UPDATE snapshots SET document=? WHERE version=1', (json.dumps(document),))
        before = fingerprint(self.store.root)
        self.assertError('integrity_error', self.export)
        self.assertEqual(fingerprint(self.store.root), before)

    def test_uri_punctuation_in_store_path_is_literal(self):
        directory = self.root / 'history # ? % 文本'
        store = TodoStore(directory)
        store.update(changes=[self.add()], expected_version=0, request_id='special-path')
        before = fingerprint(directory)
        package = export_audit(directory, context={})
        self.assertEqual(package['current']['tasks'][0]['id'], 'alpha')
        self.assertEqual(fingerprint(directory), before)

    def test_symlink_store_and_database_are_rejected_without_following(self):
        if not hasattr(os, 'symlink'):
            self.skipTest('symlinks unavailable')
        linked = self.root / 'linked'
        linked.symlink_to(self.store.root, target_is_directory=True)
        with self.assertRaises(TodoError):
            export_audit(linked, context={})
        directory = self.root / 'db-link'
        directory.mkdir()
        (directory / 'history.sqlite3').symlink_to(self.store.db)
        before = fingerprint(self.store.root)
        with self.assertRaises(TodoError):
            export_audit(directory, context={})
        self.assertEqual(fingerprint(self.store.root), before)

    def test_read_only_files_and_directory_are_sufficient(self):
        self.update([self.add()])
        self.store.db.chmod(0o400)
        self.store.root.chmod(0o500)
        try:
            before = fingerprint(self.store.root)
            self.assertEqual(self.export()['current']['version'], 1)
            self.assertEqual(fingerprint(self.store.root), before)
        finally:
            self.store.root.chmod(0o700)
            self.store.db.chmod(0o600)

    def test_concurrent_writer_and_readers_observe_coherent_packages(self):
        self.update([self.add()])
        barrier = threading.Barrier(3)

        def writer():
            barrier.wait()
            for number in range(1, 21):
                self.store.update(
                    changes=[{'op': 'edit', 'id': 'alpha', 'set': {'notes': str(number)}}],
                    expected_version=number, request_id=f'concurrent-{number}')

        def reader():
            barrier.wait()
            packages = [self.export() for _ in range(25)]
            versions = [package['current']['version'] for package in packages]
            self.assertEqual(versions, sorted(versions))
            for package in packages:
                current = package['current']
                self.assertEqual(package['selection']['head_version'], current['version'])
                self.assertEqual(package['selection']['head_hash'], current['hash'])
                self.assertEqual(package['comparison']['after'], current['version'])
                self.assertEqual(package['comparison']['changes'],
                                 diff(package['baseline']['tasks'], current['tasks']))
                transitions = package['transitions']
                self.assertEqual([item['version'] for item in transitions],
                                 list(range(1, current['version'] + 1)))
                self.assertEqual(transitions[-1]['hash'], current['hash'])
                self.assertEqual(current['tasks'][0]['notes'],
                                 '' if current['version'] == 1 else str(current['version'] - 1))
            return versions

        with ThreadPoolExecutor(max_workers=3) as pool:
            writes = pool.submit(writer)
            readers = [pool.submit(reader) for _ in range(2)]
            writes.result(timeout=20)
            for future in readers:
                future.result(timeout=20)
        self.assertEqual(self.export()['current']['version'], 21)


class AuditCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, store, text='{}', *arguments):
        output = io.StringIO()
        with patch('sys.stdin', io.StringIO(text)), redirect_stdout(output):
            code = main(['--store', str(store), 'audit', *arguments])
        return code, json.loads(output.getvalue())

    def test_audit_bypasses_mutating_constructor_and_prints_package(self):
        store = TodoStore(self.root / 'history')
        before = fingerprint(self.root)
        with patch('n_times_todo.cli.TodoStore', side_effect=AssertionError('must not initialize')):
            code, package = self.cli(store.root, '{}', '--expected-version', '0')
        self.assertEqual(code, 0)
        self.assertEqual(package['kind'], 'todo.audit_context')
        self.assertEqual(package['current']['version'], 0)
        self.assertEqual(fingerprint(self.root), before)

    def test_missing_store_failure_does_not_create_anything(self):
        missing = self.root / 'absent' / 'nested'
        code, output = self.cli(missing)
        self.assertEqual(code, 1)
        self.assertFalse(output['ok'])
        self.assertEqual(output['error']['code'], 'store_not_found')
        self.assertFalse((self.root / 'absent').exists())

    def test_malformed_duplicate_nonfinite_and_oversized_json_fail(self):
        store = TodoStore(self.root / 'history')
        before = fingerprint(self.root)
        for text in ['', '{bad', '{"goal":null,"goal":null}', '{"evidence":NaN}',
                     '{"goal":"' + 'x' * 2_000_001 + '"}']:
            with self.subTest(text=text[:80]):
                code, output = self.cli(store.root, text)
                self.assertEqual(code, 1)
                self.assertFalse(output['ok'])
                self.assertEqual(output['error']['code'], 'invalid_arguments')
        self.assertEqual(fingerprint(self.root), before)

    def test_cli_runs_outside_checkout_and_keeps_context_file_unchanged(self):
        store = TodoStore(self.root / 'history')
        store.update(changes=[{'op': 'add', 'task': {'id': 'cli', 'title': '合成命令行事项'}}],
                     expected_version=0, request_id='cli-add')
        context = self.root / 'context.json'
        context.write_text(json.dumps({'goal': {'source': 'synthetic:cli', 'text': '检查合成清单'}}),
                           encoding='utf-8')
        before = fingerprint(self.root)
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
        with context.open(encoding='utf-8') as stream:
            result = subprocess.run([
                sys.executable, '-m', 'n_times_todo.cli', '--store', str(store.root),
                'audit', '--since', '0', '--expected-version', '1'], stdin=stream,
                cwd=self.root, env=env, text=True, capture_output=True, check=True)
        package = json.loads(result.stdout)
        self.assertEqual(package['current']['tasks'][0]['id'], 'cli')
        self.assertEqual(package['goal']['source'], 'synthetic:cli')
        self.assertEqual(fingerprint(self.root), before)

    def test_invalid_stdin_bytes_are_structured_errors_under_strict_utf8(self):
        missing = self.root / 'not-created'
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
               'PYTHONIOENCODING': 'utf-8:strict'}
        result = subprocess.run([
            sys.executable, '-m', 'n_times_todo.cli', '--store', str(missing), 'audit'],
            input=b'\xff', cwd=self.root, env=env, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['error']['code'], 'invalid_arguments')
        self.assertEqual(result.stderr, b'')
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
