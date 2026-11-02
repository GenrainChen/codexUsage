"""Compressed history must preserve the same accounting and cache guarantees."""

from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import zstandard

from usage_note.logfiles import compressed_lines
from usage_note.reader import UsageReader
from tests.test_reader import context, event, meta, usage
from tests.test_output import change, complete


class CompressedLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'codex'
        self.path = self.home / 'archived_sessions' / 'a.jsonl.zst'
        self.path.parent.mkdir(parents=True)
        self.cache = self.root / 'cache.sqlite3'

    def reader(self):
        return UsageReader(self.home, cache_path=self.cache)

    def content(self, rows):
        return ''.join(json.dumps(row) + '\n' for row in rows).encode('utf-8')

    def write(self, rows):
        self.path.write_bytes(zstandard.ZstdCompressor().compress(self.content(rows)))

    def initial(self):
        return [meta(), context(service_tier='ultrafast'), event(usage(100, 60, 10))]

    def assert_views_equal(self, actual, expected):
        for key in ('days', 'hours', 'instants', 'output_days', 'output_instants',
                    'latest_event_at', 'latest_rate_limits', 'latest_rate_limits_by_id'):
            self.assertEqual(actual[key], expected[key], key)

    def test_compressed_and_plain_logs_have_identical_usage_output_and_tiers(self):
        rows = [*self.initial(), context(turn='next'),
                event(usage(180, 100, 25), usage(80, 40, 15), at='2026-09-12T16:00:00Z'),
                change(**{'a.py': {'type': 'add', 'content': 'PRIVATE_PATCH_SENTINEL\n'}}),
                complete(), context('codex-auto-review', 'excluded'),
                event(usage(200, 100, 30), usage(20, 0, 5), at='2026-09-13T01:00:00Z')]
        self.write(rows)
        compressed = self.reader().scan()
        self.path.with_suffix('').write_bytes(self.content(rows))
        self.path.unlink()
        plain = UsageReader(self.home).scan()
        self.assert_views_equal(compressed, plain)
        self.assertEqual(set(compressed['days']['2026-09-13']), {'model-a [ULTRAFAST]'})
        self.assertNotIn(b'PRIVATE_PATCH_SENTINEL', self.cache.read_bytes())

    def test_warm_and_restarted_cache_reuse_compressed_history_without_decompression(self):
        self.write(self.initial())
        reader = self.reader()
        first = reader.scan()
        with patch('usage_note.reader.compressed_lines', side_effect=AssertionError('decompressed again')):
            warm = reader.scan()
            self.assertEqual(warm['bytes_read'], 0)
            fresh = self.reader()
            self.assert_views_equal(fresh.load_cached(), first)
            restarted = fresh.scan()
        self.assert_views_equal(restarted, first)
        self.assertEqual(restarted['cache_stats']['reused_files'], 1)

    def test_compressed_replacement_and_appended_frames_are_reparsed(self):
        rows = self.initial()
        self.write(rows)
        self.reader().scan()
        tail = [event(usage(180, 100, 25), usage(80, 40, 15), at='2026-09-12T13:00:00Z')]
        with self.path.open('ab') as stream:
            stream.write(zstandard.ZstdCompressor().compress(self.content(tail)))
        appended = self.reader().scan()
        self.assertEqual(appended['days']['2026-09-12']['model-a [ULTRAFAST]']['output'], 25)
        self.assertEqual(appended['cache_stats']['reread_files'], 1)
        self.write([meta(), context(), event(usage(20, 0, 2))])
        replaced = self.reader().scan()
        self.assertEqual(replaced['days']['2026-09-12']['model-a']['input'], 20)
        self.assertNotIn('model-a [ULTRAFAST]', replaced['days']['2026-09-12'])

    def test_migration_and_coexisting_plain_compressed_copies_do_not_double_count(self):
        rows = [meta(), context(turn=None), event(usage(100, 60, 10))]
        plain = self.path.with_suffix('')
        plain.write_bytes(self.content(rows))
        first = self.reader().scan()
        self.write(rows)
        both = self.reader().scan()
        self.assert_views_equal(both, first)
        plain.unlink()
        migrated = self.reader().scan()
        self.assert_views_equal(migrated, first)
        self.assertEqual(migrated['files_scanned'], 1)

    def test_corrupt_or_truncated_archive_keeps_previous_complete_snapshot_and_retries(self):
        self.write(self.initial())
        reader = self.reader()
        first = reader.scan()
        compressed = self.path.read_bytes()
        for broken in (compressed[:-1], b'invalid zstd'):
            with self.subTest(corruption=len(broken)):
                self.path.write_bytes(broken)
                failed = reader.scan()
                self.assertEqual(failed['cache_status'], 'stale')
                self.assert_views_equal(failed, first)
                self.assert_views_equal(self.reader().load_cached(), first)
        self.path.write_bytes(compressed)
        self.assert_views_equal(reader.scan(), first)

    def test_frame_and_chunk_boundaries_preserve_lines_without_accepting_partial_tail(self):
        content = b'first\n' + os.urandom(150000).hex().encode() + b'\nlast\nunfinished'
        compressor = zstandard.ZstdCompressor()
        encoded = compressor.compress(content[:4]) + compressor.compress(content[4:])
        lines = list(compressed_lines(BytesIO(encoded), len(encoded)))
        self.assertEqual(b''.join(lines), content[:-len(b'unfinished')])

    def test_archive_rewritten_during_read_is_not_published(self):
        self.write(self.initial())
        reader = self.reader()
        original = reader._consume
        mutated = False

        def rewrite(obj, state, path):
            nonlocal mutated
            original(obj, state, path)
            if not mutated:
                mutated = True
                with path.open('ab') as stream:
                    stream.write(zstandard.ZstdCompressor().compress(b'\n'))

        with patch.object(reader, '_consume', side_effect=rewrite):
            failed = reader.scan()
        self.assertEqual(failed['cache_status'], 'stale')
        self.assertEqual(failed['days'], {})
        self.assertTrue(reader.scan()['days'])


if __name__ == '__main__':
    unittest.main()
