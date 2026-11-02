"""Disk checkpoints must preserve accounting, including after source changes."""

from datetime import timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from usage_note.reader import UsageReader
from test_reader import context, event, meta, usage
from test_output import change, complete


class PersistentReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'codex'
        self.path = self.home / 'sessions' / 'a.jsonl'
        self.path.parent.mkdir(parents=True)
        self.cache = self.root / 'local' / 'usage-cache.sqlite3'

    def reader(self, home=None):
        return UsageReader(home or self.home, tz=timezone.utc, cache_path=self.cache)

    def write(self, rows, mode='w', path=None):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode, encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row) + '\n')

    def initial(self, **settings):
        self.write([meta(cwd='D:/project'), context(**settings), event(usage(100, 60, 10))])

    def assert_cold_equivalent(self, data):
        cold = UsageReader(self.home, tz=timezone.utc).scan()
        for key in ('days', 'hours', 'output_days', 'duplicate_events',
                    'latest_event_at', 'latest_rate_limits', 'latest_rate_limits_by_id'):
            self.assertEqual(data[key], cold[key], key)

    def test_restart_restores_history_without_parsing_unchanged_logs(self):
        self.initial(service_tier='priority')
        self.reader().scan()
        reader = self.reader()
        preview = reader.load_cached()
        self.assertEqual(preview['cache_status'], 'cached')
        self.assertEqual(preview['days']['2026-09-12']['model-a [FAST]']['input'], 40)
        with patch.object(reader, '_consume', side_effect=AssertionError('history reparsed')):
            report = reader.scan()
        self.assertEqual(report['cache_stats']['reused_files'], 1)
        self.assert_cold_equivalent(report)

    def test_restart_append_preserves_previous_counter_model_and_fast_tier(self):
        self.initial(service_tier='priority', effort='high')
        self.reader().scan()
        self.write([event(usage(180, 100, 25), usage(80, 40, 15),
                          at='2026-09-12T12:01:00Z')], 'a')
        report = self.reader().scan()
        row = report['days']['2026-09-12']['model-a [FAST]']
        self.assertEqual((row['input'], row['cached'], row['output'], row['requests']),
                         (80, 100, 25, 2))
        self.assertEqual(report['cache_stats']['appended_files'], 1)
        self.assert_cold_equivalent(report)

    def test_existing_ultrafast_checkpoints_regroup_without_reparse_or_rebuild(self):
        self.initial(service_tier='ultrafast')
        # Older versions grouped this tier as standard but stored the raw tier.
        with patch('usage_note.reader.model_key', side_effect=lambda model, tier: model):
            previous = self.reader().scan()
        self.assertEqual(set(previous['days']['2026-09-12']), {'model-a'})
        reader = self.reader()
        preview = reader.load_cached()
        self.assertEqual(set(preview['days']['2026-09-12']), {'model-a [ULTRAFAST]'})
        with patch.object(reader, '_consume', side_effect=AssertionError('history reparsed')):
            restored = reader.scan()
        self.assertEqual(restored['cache_stats']['reused_files'], 1)
        self.assert_cold_equivalent(restored)
        self.write([event(usage(180, 100, 25), usage(80, 40, 15),
                          at='2026-09-12T12:01:00Z')], 'a')
        appended = self.reader().scan()
        self.assertEqual(appended['days']['2026-09-12']['model-a [ULTRAFAST]']['output'], 25)
        self.assertEqual(appended['cache_stats']['appended_files'], 1)
        self.assert_cold_equivalent(appended)

    def test_old_parser_cache_is_reparsed_to_recover_inherited_ultrafast(self):
        self.write([meta(), context(service_tier='ultrafast'), event(usage(100, 0, 10)),
                    context(turn='next-turn'),
                    event(usage(180, 0, 25), usage(80, 0, 15), at='2026-09-12T12:01:00Z')])
        consume = UsageReader._consume

        def legacy_consume(reader, obj, state, path):
            consume(reader, obj, state, path)
            if obj.get('type') == 'turn_context':
                state.service_tier = obj['payload'].get('service_tier')

        with patch('usage_note.reader.PARSER_VERSION', 1), patch.object(UsageReader, '_consume', legacy_consume):
            previous = self.reader().scan()
        self.assertEqual(previous['days']['2026-09-12']['model-a']['input'], 80)
        upgraded = self.reader().scan()
        models = upgraded['days']['2026-09-12']
        self.assertEqual(set(models), {'model-a [ULTRAFAST]'})
        self.assertEqual(models['model-a [ULTRAFAST]']['input'], 180)
        self.assertEqual(upgraded['cache_stats']['reread_files'], 1)
        self.assert_cold_equivalent(upgraded)
        reader = self.reader()
        self.assertEqual(reader.load_cached()['days'], upgraded['days'])
        with patch.object(reader, '_consume', side_effect=AssertionError('history reparsed')):
            self.assertEqual(reader.scan()['days'], upgraded['days'])

    def test_reset_and_last_only_baselines_survive_multiple_restarts(self):
        self.initial()
        self.write([event(None, usage(20, 5, 3), at='2026-09-12T12:01:00Z')], 'a')
        self.reader().scan()
        self.write([event(usage(150, 70, 18), usage(30, 5, 5), at='2026-09-12T12:02:00Z'),
                    event(usage(0), usage(30, 5, 5), at='2026-09-12T12:03:00Z')], 'a')
        self.reader().scan()
        self.write([event(usage(20, 0, 2), at='2026-09-12T12:04:00Z')], 'a')
        report = self.reader().scan()
        self.assertEqual(report['days']['2026-09-12']['model-a']['output'], 20)
        self.assert_cold_equivalent(report)

    def test_half_line_is_not_checkpointed_across_restart(self):
        self.initial()
        tail = (json.dumps(event(usage(180, 100, 25), usage(80, 40, 15),
                                at='2026-09-12T12:01:00Z')) + '\n').encode()
        with self.path.open('ab') as stream:
            stream.write(tail[:50])
        self.reader().scan()
        with self.path.open('ab') as stream:
            stream.write(tail[50:])
        report = self.reader().scan()
        self.assertEqual(report['days']['2026-09-12']['model-a']['output'], 25)
        self.assert_cold_equivalent(report)

    def test_rewrite_and_truncation_replace_cached_contributions(self):
        self.initial()
        self.reader().scan()
        self.write([meta(), context(), event(usage(200, 60, 20))])
        report = self.reader().scan()
        self.assertEqual(report['days']['2026-09-12']['model-a']['input'], 140)
        self.assertEqual(report['cache_stats']['reread_files'], 1)
        self.write([meta()])
        self.assertEqual(self.reader().scan()['days'], {})

    def test_startup_fingerprint_detects_same_metadata_replacement(self):
        self.initial()
        self.reader().scan()
        stat = self.path.stat()
        text = self.path.read_text().replace('model-a', 'model-b')
        self.path.write_text(text, encoding='utf-8')
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        report = self.reader().scan()
        self.assertIn('model-b', report['days']['2026-09-12'])
        self.assertNotIn('model-a', report['days']['2026-09-12'])

    def test_archive_move_without_turn_id_does_not_count_both_paths(self):
        self.initial(turn=None)
        self.reader().scan()
        target = self.home / 'archived_sessions' / 'a.jsonl'
        target.parent.mkdir()
        self.path.rename(target)
        report = self.reader().scan()
        self.assertEqual(report['days']['2026-09-12']['model-a']['output'], 10)
        self.assert_cold_equivalent(report)

    def test_deleted_source_is_not_retained_as_permanent_history(self):
        self.initial()
        self.reader().scan()
        self.path.unlink()
        self.assertEqual(self.reader().scan()['days'], {})
        self.assertEqual(self.reader().load_cached()['days'], {})

    def test_new_fork_retains_interval_deduplication_after_restart(self):
        first = event(usage(100, 0, 10))
        shared = event(usage(400, 0, 40), usage(100, 0, 10), at='2026-09-12T12:03:00Z')
        self.write([meta(), context(), first, shared])
        self.reader().scan()
        self.write([meta(history_base={'thread_id': 'parent'}), context(),
                    event(usage(300, 0, 30), usage(100, 0, 10), at='2026-09-12T12:02:00Z'),
                    shared], path=self.path.with_name('fork.jsonl'))
        report = self.reader().scan()
        self.assertEqual(report['days']['2026-09-12']['model-a']['input'], 400)
        self.assert_cold_equivalent(report)

    def test_output_and_relative_path_context_survive_restart_without_content(self):
        self.initial()
        self.write([change(**{'a.py': {'type': 'add', 'content': 'PRIVATE_PATCH_SENTINEL\n'}}),
                    complete()], 'a')
        self.reader().scan()
        self.write([change('edit-2', **{'a.py': {'type': 'add', 'content': 'second\n'}})], 'a')
        report = self.reader().scan()
        row = report['output_days']['2026-09-13']
        self.assertEqual((row['added'], row['turns'], len(row['files'])), (2, 1, 1))
        self.assert_cold_equivalent(report)
        raw = self.cache.read_bytes()
        self.assertNotIn(b'PRIVATE_PATCH_SENTINEL', raw)
        self.assertNotIn(b'Private conversation', raw)

    def test_source_directory_isolation_and_rebuild(self):
        self.initial()
        self.reader().scan()
        other = self.root / 'other'
        other.mkdir()
        self.assertIsNone(self.reader(other).load_cached())
        self.assertEqual(self.reader(other).scan()['days'], {})
        rebuilt = self.reader().scan(rebuild=True)
        self.assertEqual(rebuilt['cache_stats']['reread_files'], 1)
        self.assert_cold_equivalent(rebuilt)

    def test_scan_error_retains_complete_snapshot_and_retries(self):
        self.initial()
        self.reader().scan()
        self.write([event(usage(180, 100, 25), usage(80, 40, 15),
                          at='2026-09-12T12:01:00Z')], 'a')
        reader = self.reader()
        reader.load_cached()
        with patch.object(reader, '_read_file', side_effect=PermissionError('busy')):
            stale = reader.scan()
        self.assertEqual(stale['cache_status'], 'stale')
        self.assertEqual(stale['days']['2026-09-12']['model-a']['output'], 10)
        self.assertEqual(self.reader().load_cached()['days'], stale['days'])
        self.assertEqual(reader.scan()['days']['2026-09-12']['model-a']['output'], 25)

    def test_directory_enumeration_failure_does_not_prune_history(self):
        self.initial()
        self.reader().scan()
        reader = self.reader()
        with patch('usage_note.reader.os.scandir', side_effect=PermissionError('denied')):
            report = reader.scan()
        self.assertEqual(report['cache_status'], 'stale')
        self.assertEqual(report['days']['2026-09-12']['model-a']['output'], 10)

    def test_cold_failure_does_not_publish_a_partial_persistent_snapshot(self):
        self.initial()
        reader = self.reader()
        with patch.object(reader, '_read_file', side_effect=PermissionError('busy')):
            report = reader.scan()
        self.assertEqual(report['cache_status'], 'stale')
        self.assertIsNone(self.reader().load_cached())
        self.assertEqual(reader.scan()['days']['2026-09-12']['model-a']['output'], 10)

    def test_unwritable_cache_falls_back_to_live_totals(self):
        self.initial()
        self.cache.parent.mkdir()
        self.cache.mkdir()
        report = self.reader().scan()
        self.assertEqual(report['days']['2026-09-12']['model-a']['input'], 40)
        self.assertTrue(report['warnings'])

    def test_failed_cache_save_keeps_memory_incremental_and_can_recover(self):
        self.initial()
        reader = self.reader()
        reader.load_cached()
        with patch.object(reader._cache, 'save', return_value=None):
            first = reader.scan()
        with patch.object(reader, '_consume', side_effect=AssertionError('memory cache lost')):
            second = reader.scan()
        self.assertEqual(second['days'], first['days'])
        self.assertEqual(self.reader().load_cached()['days'], first['days'])

    def test_late_file_error_rolls_back_already_parsed_appends(self):
        self.initial()
        second = self.path.with_name('z.jsonl')
        self.write([meta('other'), context(turn='other'), event(usage(10, 0, 1))], path=second)
        reader = self.reader()
        before = reader.scan()
        self.write([event(usage(180, 100, 25), usage(80, 40, 15),
                          at='2026-09-12T12:01:00Z')], 'a')
        read_file = reader._read_file

        def read_then_fail(path):
            if path == second:
                raise PermissionError('late failure')
            return read_file(path)

        with patch.object(reader, '_read_file', side_effect=read_then_fail):
            failed = reader.scan()
        self.assertEqual(failed['cache_status'], 'stale')
        self.assertEqual(failed['days'], before['days'])
        self.assertEqual(self.reader().load_cached()['days'], before['days'])
        self.assert_cold_equivalent(reader.scan())

    def test_bytes_appended_during_read_are_processed_on_next_scan(self):
        self.initial()
        reader = self.reader()
        consume = reader._consume
        appended = False

        def append_during_read(obj, state, path):
            nonlocal appended
            consume(obj, state, path)
            if not appended:
                appended = True
                self.write([event(usage(180, 100, 25), usage(80, 40, 15),
                                  at='2026-09-12T12:01:00Z')], 'a')

        with patch.object(reader, '_consume', side_effect=append_during_read):
            first = reader.scan()
        self.assertEqual(first['days']['2026-09-12']['model-a']['output'], 10)
        second = self.reader().scan()
        self.assertEqual(second['days']['2026-09-12']['model-a']['output'], 25)
        self.assert_cold_equivalent(second)

    def test_invalid_checkpoint_is_rebuilt_from_source(self):
        self.initial()
        self.reader().scan()
        connection = sqlite3.connect(self.cache)
        try:
            path, state = connection.execute('SELECT path, state FROM entries').fetchone()
            payload = json.loads(state)
            payload['offset'] = 999_999_999
            connection.execute('UPDATE entries SET state = ? WHERE path = ?',
                               (json.dumps(payload), path))
            connection.commit()
        finally:
            connection.close()
        reader = self.reader()
        self.assertIsNone(reader.load_cached())
        report = reader.scan()
        self.assertTrue(report['warnings'])
        self.assertEqual(report['days']['2026-09-12']['model-a']['input'], 40)
        self.assertEqual(self.reader().scan()['cache_stats']['reused_files'], 1)


if __name__ == '__main__':
    unittest.main()
