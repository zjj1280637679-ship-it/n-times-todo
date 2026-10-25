import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from n_times_todo import TodoStore, TodoError
from n_times_todo.api import dispatch
from n_times_todo.changes import apply, diff
from n_times_todo.contracts import decode, task
from n_times_todo.render import markdown


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TodoStore(Path(self.temp.name)/'history')

    def write(self, changes, version=0, key='r1', **kwargs):
        return self.store.update(changes=changes, expected_version=version, request_id=key, **kwargs)

    def add(self, key='a', title='任务'):
        return {'op': 'add', 'task': {'id': key, 'title': title}}

    def assertError(self, code, func, *args, **kwargs):
        with self.assertRaises(TodoError) as error:
            func(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_full_snapshots_and_automatic_views(self):
        first = self.write([self.add()])['snapshot']
        self.write([{'op':'edit','id':'a','set':{'status':'done','notes':'已验证'}}], 1, 'r2')
        self.assertEqual(self.store.get(1), first)
        self.assertEqual(self.store.get()['tasks'][0]['status'], 'done')
        for version in range(3):
            data = self.store.root/'versions'/f'{version:08d}.json'
            self.assertEqual(json.loads(data.read_text()), self.store.get(version))
            self.assertTrue(data.with_suffix('.md').is_file())
        self.assertIn('<del>todo</del> → <ins>done</ins>', (self.store.root/'versions/00000002.md').read_text())

    def test_restore_appends_and_preserves_history(self):
        self.write([self.add()])
        self.write([{'op':'remove','id':'a'}], 1, 'r2')
        old = self.store.get(1)
        restored = self.store.restore(version=1, expected_version=2, request_id='r3')['snapshot']
        self.assertEqual(restored['version'], 3)
        self.assertEqual(restored['tasks'], old['tasks'])
        self.assertEqual(self.store.get(2)['tasks'], [])
        self.assertEqual(self.store.get(1), old)
        self.assertEqual(restored['restored_from'], 1)
        self.assertEqual(self.store.verify()['versions'], 4)

    def test_idempotence_and_request_conflict(self):
        changes = [self.add()]
        first = self.write(changes)
        repeated = self.write(changes)
        self.assertTrue(repeated['replayed'])
        self.assertEqual(first['snapshot'], repeated['snapshot'])
        self.assertEqual(self.store.get()['version'], 1)
        self.assertError('request_conflict', self.write, changes, 1)
        self.assertError('request_conflict', self.store.restore, version=0, expected_version=0, request_id='r1')

    def test_noop_is_a_new_version_but_retry_is_not(self):
        self.write([self.add()])
        changes = [{'op':'edit','id':'a','set':{'status':'todo'}}]
        result = self.write(changes, 1, 'r2')['snapshot']
        self.assertEqual(result['version'], 2)
        self.assertFalse(any(result['changes'].values()))
        self.write(changes, 1, 'r2')
        self.assertEqual(self.store.get()['version'], 2)

    def test_versions_not_wallclock_define_order(self):
        with patch('n_times_todo.store.utc_now', return_value='2000-01-01T00:00:00.000000Z'):
            self.write([self.add()])
            self.write([{'op':'edit','id':'a','set':{'title':'下一版'}}], 1, 'r2')
        self.assertEqual(self.store.verify()['head_version'], 2)

    def test_failed_batch_rolls_back(self):
        self.assertError('task_not_found', self.write, [self.add(), {'op':'remove','id':'missing'}])
        self.assertEqual(self.store.get()['version'], 0)
        self.assertEqual(self.store.verify()['requests'], 0)

    def test_exception_between_snapshot_and_request_rolls_back(self):
        original = self.store._insert
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('simulated crash before commit')
        with patch.object(self.store, '_insert', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.write([self.add()])
        self.assertEqual(self.store.get()['version'], 0)
        self.assertEqual(self.write([self.add()])['snapshot']['version'], 1)

    def test_two_writers_cas_one_success(self):
        def writer(n):
            store = TodoStore(self.store.root)
            try:
                return store.update(changes=[self.add(str(n))], expected_version=0, request_id=f'r{n}')['snapshot']['version']
            except TodoError as error:
                return error.code
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(writer, [1,2]))
        self.assertCountEqual(results, [1, 'version_conflict'])
        self.assertEqual(len(self.store.get()['tasks']), 1)

    def test_concurrent_readers_observe_consistent_snapshot_and_index(self):
        self.write([self.add()])
        def writer():
            store = TodoStore(self.store.root)
            for number in range(1, 21):
                store.update(changes=[{'op':'edit','id':'a','set':{'notes':str(number)}}],
                             expected_version=number, request_id=f'update-{number}')
        def reader():
            store = TodoStore(self.store.root)
            return [store.get()['version'] for _ in range(80)]
        with ThreadPoolExecutor(2) as pool:
            write_future = pool.submit(writer)
            read_future = pool.submit(reader)
            write_future.result()
            versions = read_future.result()
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(self.store.get()['version'], 21)

    def test_unknown_store_version_is_not_modified(self):
        with sqlite3.connect(self.store.db) as con:
            con.execute('PRAGMA user_version=2')
        self.assertError('unsupported_store_version', TodoStore, self.store.root)
        with sqlite3.connect(self.store.db) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_two_writers_same_request_commit_once(self):
        def writer(_):
            return TodoStore(self.store.root).update(changes=[self.add()], expected_version=0, request_id='same')
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(writer, [1,2]))
        self.assertEqual([r['snapshot']['version'] for r in results], [1,1])
        self.assertCountEqual([r['replayed'] for r in results], [False,True])

    def test_views_failure_does_not_undo_commit_and_retry_repairs(self):
        directory = self.store.root/'versions'
        directory.write_text('blocked')
        result = self.write([self.add()])
        self.assertEqual(result['snapshot']['version'], 1)
        self.assertEqual(result['views']['status'], 'pending')
        directory.unlink()
        repeated = self.write([self.add()])
        self.assertTrue(repeated['replayed'])
        self.assertEqual(repeated['views']['status'], 'ready')

    def test_conflicting_view_is_preserved(self):
        self.write([self.add()])
        page = self.store.root/'versions/00000001.md'
        page.write_text('external edit')
        result = self.store.sync()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(page.read_text(), 'external edit')
        self.assertEqual(self.store.get()['tasks'][0]['title'], '任务')

    def test_post_commit_sync_read_error_returns_committed_receipt(self):
        with patch.object(self.store, 'sync', side_effect=sqlite3.OperationalError('simulated read failure')):
            result = self.write([self.add()])
        self.assertEqual(result['snapshot']['version'], 1)
        self.assertEqual(result['views']['status'], 'pending')
        self.assertEqual(self.store.get()['version'], 1)
        self.assertTrue(self.write([self.add()])['replayed'])

    def test_snapshot_size_limit_is_atomic(self):
        changes = [{'op':'add','task':{'id':f't{i}','title':'x','notes':'a'*19000}} for i in range(110)]
        self.assertError('snapshot_too_large', self.write, changes)
        self.assertEqual(self.store.get()['version'], 0)

    def test_markdown_punctuation_is_inert_text(self):
        text = '![tracker](https://evil.example/pixel) **bold** `code` [x](javascript:alert(1))'
        self.write([self.add(title=text)], reason=text)
        page = markdown(self.store.get())
        self.assertNotIn('![tracker]', page)
        self.assertNotIn('[x](javascript:', page)
        self.assertNotIn('**bold**', page)
        self.assertIn('&#33;&#91;tracker&#93;', page)

    def test_missing_views_regenerated(self):
        self.write([self.add()])
        page = self.store.root/'versions/00000001.json'
        original = page.read_bytes()
        page.unlink()
        self.assertEqual(self.store.sync()['status'], 'ready')
        self.assertEqual(page.read_bytes(), original)

    def test_reused_historical_id_rejected(self):
        self.write([self.add()])
        self.write([{'op':'remove','id':'a'}], 1, 'r2')
        self.assertError('duplicate_task_id', self.write, [self.add(title='different')], 2, 'r3')

    def test_generated_id_replay_is_stable(self):
        changes = [{'op':'add','task':{'title':'generated'}}]
        first = self.write(changes)
        self.assertTrue(first['snapshot']['tasks'][0]['id'].startswith('t-'))
        self.assertEqual(first['snapshot'], self.write(changes)['snapshot'])

    def test_triggers_prevent_normal_mutation(self):
        with sqlite3.connect(self.store.db) as con:
            for query in ['DELETE FROM snapshots', "UPDATE snapshots SET digest='x'"]:
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(query)

    def test_hash_detects_tamper_after_trigger_removed(self):
        self.write([self.add()])
        with sqlite3.connect(self.store.db) as con:
            con.execute('DROP TRIGGER snapshots_no_update')
            con.execute("UPDATE snapshots SET digest='bad' WHERE version=1")
        self.assertError('integrity_error', self.store.get)
        self.assertError('integrity_error', self.write, [self.add('b')], 1, 'r2')

    def test_request_index_is_bound_to_snapshot_hash(self):
        self.write([self.add()])
        with sqlite3.connect(self.store.db) as con:
            con.execute('DROP TRIGGER requests_no_update')
            con.execute("UPDATE requests SET request_digest='" + '0'*64 + "'")
        self.assertError('integrity_error', self.store.verify)
        self.assertError('integrity_error', self.write, [self.add()])

    def test_history_pagination_and_arbitrary_diff(self):
        self.write([self.add()])
        self.write([{'op':'edit','id':'a','set':{'status':'done'}}], 1, 'r2')
        page = self.store.history(limit=1)
        self.assertEqual(page['next_after'], 0)
        self.assertEqual(self.store.history(after=0)['versions'][0]['version'], 1)
        self.assertEqual(self.store.compare(0,2)['changes']['added'][0]['status'], 'done')
        self.assertError('invalid_arguments', self.store.compare, None, 1)

    def test_task_metadata_not_code_execution(self):
        self.write([self.add(title='<script>alert(1)</script>')])
        self.assertNotIn('<script>', markdown(self.store.get()))

    def test_api_compact_write_and_store_not_model_argument(self):
        result = dispatch(self.store, {'tool':'todo.update','arguments':{'changes':[self.add()], 'expected_version':0,'request_id':'a'}})
        self.assertTrue(result['ok'])
        self.assertNotIn('snapshot', result['data'])
        self.assertEqual(result['data']['version'], 1)
        self.assertFalse(dispatch(self.store, {'tool':'todo.get','arguments':{'store':'/tmp/elsewhere'}})['ok'])

    def test_invalid_arguments_do_not_change_store(self):
        bad = [None, [], {}, {'tool':[],'arguments':{}}, {'tool':'todo.restore','arguments':{'version':True,'expected_version':0,'request_id':'r'}},
               {'tool':'todo.update','arguments':{'changes':[{'op':'add','task':{'title':'t','status':[]}}], 'expected_version':0,'request_id':'r'}}]
        for request in bad:
            self.assertFalse(dispatch(self.store, request)['ok'])
        self.assertEqual(self.store.get()['version'], 0)

    def test_symlink_views_not_written(self):
        if not hasattr(os, 'symlink'):
            self.skipTest('symlinks unavailable')
        target = Path(self.temp.name)/'target'
        target.mkdir()
        (self.store.root/'versions').symlink_to(target, target_is_directory=True)
        self.assertEqual(self.write([self.add()])['views']['status'], 'pending')
        self.assertEqual(list(target.iterdir()), [])


class PureTests(unittest.TestCase):
    def test_added_removed_completed_reopened_modified_order(self):
        before = [task({'id':'a','title':'A'}), task({'id':'b','title':'B','status':'done'})]
        after = apply(before, [{'op':'edit','id':'a','set':{'status':'done','title':'A2'}},
                               {'op':'edit','id':'b','set':{'status':'doing'}},
                               {'op':'move','id':'b','before':'a'}], 'r')
        change = diff(before, after)
        self.assertEqual(change['completed'], ['a'])
        self.assertEqual(change['reopened'], ['b'])
        self.assertEqual(change['order'], {'before':['a','b'],'after':['b','a']})
        self.assertEqual(before[0]['title'], 'A')

    def test_insert_does_not_mark_all_survivors_reordered(self):
        before = [task({'id':'a','title':'A'}), task({'id':'b','title':'B'})]
        after = apply(before, [{'op':'add','task':{'id':'c','title':'C'}}, {'op':'move','id':'c','before':'a'}], 'r')
        self.assertIsNone(diff(before, after)['order'])
        self.assertEqual(diff(after, before)['removed'][0]['id'], 'c')

    def test_unknown_fields_and_duplicate_id(self):
        for value in [{'id':'a','title':'x','extra':1}, {'id':'a','title':'x','status':True}, {'id':'a','title':'\ud800'}]:
            with self.assertRaises(TodoError):
                task(value)
        with self.assertRaises(TodoError):
            apply([], [{'op':'add','task':{'id':'a','title':'A'}}, {'op':'remove','id':'a'}, {'op':'add','task':{'id':'a','title':'B'}}], 'r')

    def test_json_parser_rejects_duplicates_and_nonfinite(self):
        for text in [b'bytes', '\ud800', '{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{bad']:
            with self.assertRaises(TodoError):
                decode(text)


class ProcessTests(unittest.TestCase):
    def test_cli_installed_style_outside_checkout(self):
        with tempfile.TemporaryDirectory() as root:
            store = str(Path(root)/'state')
            command = [sys.executable, '-m', 'n_times_todo.cli', '--store', store]
            subprocess.run(command + ['init'], check=True, capture_output=True, text=True, cwd=root)
            request = {'tool':'todo.update','arguments':{'expected_version':0,'request_id':'cli','changes':[{'op':'add','task':{'title':'demo'}}]}}
            result = subprocess.run(command+['call'], input=json.dumps(request), check=True, capture_output=True, text=True, cwd=root)
            self.assertEqual(json.loads(result.stdout)['data']['version'], 1)
            result = subprocess.run(command+['show','--at','1'], check=True, capture_output=True, text=True, cwd=root)
            self.assertIn('demo', result.stdout)

    def test_process_exit_before_commit_rolls_back(self):
        with tempfile.TemporaryDirectory() as root:
            store = TodoStore(root)
            script = '''import os,sys
from n_times_todo import TodoStore
s=TodoStore(sys.argv[1])
original=s._insert
def crash(*a,**k):
 original(*a,**k)
 os._exit(17)
s._insert=crash
s.update(changes=[{"op":"add","task":{"title":"x"}}],expected_version=0,request_id="crash")
'''
            result = subprocess.run([sys.executable, '-c', script, root], capture_output=True)
            self.assertEqual(result.returncode, 17)
            self.assertEqual(TodoStore(root).get()['version'], 0)

    def test_process_exit_after_commit_views_recover(self):
        with tempfile.TemporaryDirectory() as root:
            script = '''import os,sys
from n_times_todo import TodoStore
s=TodoStore(sys.argv[1])
s.sync=lambda: os._exit(18)
s.update(changes=[{"op":"add","task":{"title":"x"}}],expected_version=0,request_id="crash")
'''
            result = subprocess.run([sys.executable, '-c', script, root], capture_output=True)
            self.assertEqual(result.returncode, 18)
            store = TodoStore(root)
            self.assertEqual(store.get()['version'], 1)
            self.assertEqual(store.sync()['status'], 'ready')
            self.assertTrue((Path(root)/'versions/00000001.md').is_file())

class ModelTests(unittest.TestCase):
    def test_finite_model_and_mutants(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1]/'scripts/check_model.py'
        spec = importlib.util.spec_from_file_location('todo_model_test', path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        result = module.run()
        self.assertTrue(result['baseline']['ok'])
        self.assertEqual(result['baseline']['states'], 421)
        self.assertTrue(all(not item['ok'] for item in result['mutants'].values()))


if __name__ == '__main__':
    unittest.main()
