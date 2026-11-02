import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
from pathlib import Path

from usage_note.cache import UsageCache


class UsageCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.database = self.root / "usage.sqlite3"
        self.source = self.root / "codex"
        self.cache = UsageCache(self.database, self.source, parser_version=1)

    def test_reload_retains_json_states_and_removes_only_named_paths(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        first = {"a.jsonl": {"size": 4, "totals": {"tokens": 7}},
                 "b.jsonl": {"size": 6, "totals": {"tokens": 9}}}
        self.assertEqual(self.cache.save(first, set(), 0), 1)
        self.assertEqual(self.cache.save({"a.jsonl": {"size": 8}}, {"b.jsonl"}, 1), 2)
        self.assertEqual(UsageCache(self.database, self.source, parser_version=1).load(),
                         ({"a.jsonl": {"size": 8}}, 2))

    def test_normalized_source_identity_and_other_source_isolation(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        other = UsageCache(self.database, self.root / "other", parser_version=1)
        self.assertEqual(other.load(), ({}, 0))
        self.assertEqual(self.cache.save({"a": {"count": 1}}, set(), 0), 1)
        self.assertEqual(other.save({"a": {"count": 2}}, set(), 0), 1)
        alias = UsageCache(self.database, self.source / ".." / "codex", parser_version=1)
        self.assertEqual(alias.load(), ({"a": {"count": 1}}, 1))
        self.assertEqual(other.load(), ({"a": {"count": 2}}, 1))

    def test_parser_upgrade_invalidates_only_its_source_and_blocks_old_writer(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        other = UsageCache(self.database, self.root / "other", parser_version=1)
        self.assertEqual(other.load(), ({}, 0))
        self.assertEqual(self.cache.save({"a": {"old": True}}, set(), 0), 1)
        self.assertEqual(other.save({"b": {"keep": True}}, set(), 0), 1)
        upgraded = UsageCache(self.database, self.source, parser_version=2)
        states, revision = upgraded.load()
        self.assertEqual(states, {})
        self.assertGreater(revision, 1)
        self.assertIsNone(self.cache.save({"a": {"old": True}}, set(), 1))
        self.assertEqual(upgraded.save({"a": {"new": True}}, set(), revision), revision + 1)
        self.assertEqual(other.load(), ({"b": {"keep": True}}, 1))

    def test_stale_revision_cannot_overwrite_newer_result(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        stale = UsageCache(self.database, self.source, parser_version=1)
        self.assertEqual(stale.load(), ({}, 0))
        self.assertEqual(self.cache.save({"a": {"count": 2}}, set(), 0), 1)
        self.assertIsNone(stale.save({"a": {"count": 1}}, set(), 0))
        self.assertEqual(self.cache.load(), ({"a": {"count": 2}}, 1))
        self.assertTrue(stale.warnings)

    def test_failed_json_serialization_does_not_partially_write(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        self.assertIsNone(self.cache.save({"good": {"count": 1}, "bad": {"value": object()}},
                                          set(), 0))
        self.assertEqual(self.cache.load(), ({}, 0))
        self.assertTrue(self.cache.warnings)

    def test_sql_failure_rolls_back_prior_changed_records_and_revision(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("""CREATE TRIGGER reject_bad BEFORE INSERT ON entries
                WHEN NEW.path = 'bad' BEGIN SELECT RAISE(ABORT, 'rejected'); END""")
            connection.commit()
        self.assertIsNone(self.cache.save({"good": {"count": 1}, "bad": {"count": 2}},
                                          set(), 0))
        self.assertEqual(self.cache.load(), ({}, 0))

    def test_future_schema_is_left_intact_and_disables_writes(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA user_version = 99")
            connection.commit()
        self.assertEqual(self.cache.load(), ({}, 0))
        self.assertIsNone(self.cache.save({"a": {"count": 1}}, set(), 0))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone(), (99,))
            self.assertEqual(connection.execute("SELECT count(*) FROM entries").fetchone(), (0,))
        self.assertTrue(self.cache.warnings)

    def test_corrupt_database_is_recovered_or_degrades_without_raising(self):
        self.database.write_bytes(b"not a sqlite database")
        states, revision = self.cache.load()
        self.assertEqual(states, {})
        self.assertEqual(revision, 0)
        self.assertTrue(self.cache.warnings)
        self.assertEqual(self.cache.save({"a": {"count": 1}}, set(), revision), 1)
        self.assertEqual(self.cache.load(), ({"a": {"count": 1}}, 1))

    def test_locked_database_and_unwritable_path_fail_gracefully(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        with closing(sqlite3.connect(self.database)) as blocker:
            blocker.execute("BEGIN EXCLUSIVE")
            self.assertIsNone(self.cache.save({"a": {"count": 1}}, set(), 0))
            blocker.rollback()
        self.assertEqual(self.cache.load(), ({}, 0))
        invalid = UsageCache(self.root, self.source, parser_version=1)
        self.assertEqual(invalid.load(), ({}, 0))
        self.assertIsNone(invalid.save({"a": {"count": 1}}, set(), 0))
        self.assertTrue(invalid.warnings)

    def test_existing_cache_loads_while_another_writer_holds_reserved_lock(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        self.assertEqual(self.cache.save({"a": {"count": 4}}, set(), 0), 1)
        with closing(sqlite3.connect(self.database)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.cache.load(), ({"a": {"count": 4}}, 1))
            blocker.rollback()

    def test_bad_entry_isolated_without_losing_revision_or_good_entries(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        self.assertEqual(self.cache.save({"good": {"count": 4}, "bad": {"count": 5}},
                                         set(), 0), 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE entries SET state = '{broken' WHERE path = 'bad'")
            connection.commit()
        states, revision = self.cache.load()
        self.assertEqual(states, {"good": {"count": 4}})
        self.assertGreaterEqual(revision, 1)
        self.assertTrue(self.cache.warnings)
        self.assertEqual(self.cache.save({"bad": {"count": 6}}, set(), revision),
                         revision + 1)
        self.assertEqual(self.cache.load(),
                         ({"good": {"count": 4}, "bad": {"count": 6}}, revision + 1))

    def test_bad_entry_still_returns_good_data_and_real_revision_when_repair_is_locked(self):
        self.assertEqual(self.cache.load(), ({}, 0))
        self.assertEqual(self.cache.save({"good": {"count": 4}, "bad": {"count": 5}},
                                         set(), 0), 1)
        with closing(sqlite3.connect(self.database)) as blocker:
            blocker.execute("UPDATE entries SET state = '{broken' WHERE path = 'bad'")
            blocker.commit()
            blocker.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.cache.load(), ({"good": {"count": 4}}, 1))
            blocker.rollback()
        self.assertEqual(self.cache.save({"bad": {"count": 6}}, set(), 1), 2)

    def test_corrupt_file_with_journal_is_not_quarantined(self):
        self.database.write_bytes(b"not a sqlite database")
        journal = self.database.with_name(self.database.name + "-journal")
        journal.write_bytes(b"possibly active")
        self.assertEqual(self.cache.load(), ({}, 0))
        self.assertEqual(self.database.read_bytes(), b"not a sqlite database")
        self.assertIsNone(self.cache.save({"a": {"count": 1}}, set(), 0))
        self.assertTrue(self.cache.warnings)

    def test_gui_constructed_cache_can_be_used_on_background_thread(self):
        results = []

        def worker():
            states, revision = self.cache.load()
            results.append((states, self.cache.save({"a": {"count": 1}}, set(), revision)))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [({}, 1)])
        self.assertEqual(self.cache.load(), ({"a": {"count": 1}}, 1))


if __name__ == "__main__":
    unittest.main()
