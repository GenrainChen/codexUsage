"""Built-in completion events are evidence of output; tool intent is not."""

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from usage_note.reader import UsageReader


def change(identity="edit-1", at="2026-09-13T16:00:00Z", status="completed", **changes):
    return {"type": "event_msg", "timestamp": at, "payload": {
        "type": "item_completed", "thread_id": "thread-a", "turn_id": "turn-a",
        "item": {"type": "FileChange", "id": identity, "status": status,
                 "changes": changes}}}


def complete(turn="turn-a", at="2026-09-13T16:01:00Z"):
    return {"type": "event_msg", "timestamp": at,
            "payload": {"type": "task_complete", "turn_id": turn,
                        "last_agent_message": "Private conversation must not be retained"}}


class OutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.reader = UsageReader(self.home)
        self.path = self.home / "sessions" / "a.jsonl"
        self.path.parent.mkdir()

    def write(self, records, mode="w"):
        with self.path.open(mode, encoding="utf-8") as stream:
            if mode == "w":
                stream.write(json.dumps({"type": "session_meta", "payload": {
                    "id": "thread-a", "cwd": "D:/project"}}) + "\n")
            for record in records:
                stream.write(json.dumps(record) + "\n")

    def outputs(self):
        data = self.reader.scan()
        self.assertIn("output_days", data, "Reader must expose built-in output totals")
        return data["output_days"]

    def test_successful_add_update_delete_counts_lines_and_unique_files(self):
        self.write([
            change(**{"a.py": {"type": "add", "content": "one\ntwo\n"}}),
            change("edit-2", **{"D:/project/a.py": {"type": "update",
                   "unified_diff": "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n one\n-two\n+three\n+++literal\n"}}),
            change("edit-3", **{"old.md": {"type": "delete", "content": "old\n"}}),
            complete(),
        ])
        row = self.outputs()["2026-09-14"]
        self.assertEqual((row["added"], row["deleted"], row["turns"]), (4, 2, 1))
        self.assertEqual(len(row["files"]), 2)
        self.assertNotIn("a.py", json.dumps(row))
        self.assertNotIn("Private conversation", repr(self.reader._files))

    def test_failed_edits_and_aborted_turns_do_not_count(self):
        self.write([change(status="failed", **{"a.py": {"type": "add", "content": "x\n"}}),
                    {"type": "event_msg", "timestamp": "2026-09-13T16:00:00Z",
                     "payload": {"type": "turn_aborted", "turn_id": "turn-a"}}])
        self.assertEqual(self.outputs(), {})

    def test_incremental_refresh_fork_copy_and_archive_do_not_double_count(self):
        records = [change(**{"a.py": {"type": "add", "content": "x\n"}}), complete()]
        self.write(records)
        first = self.outputs()
        self.assertEqual(self.outputs(), first)
        shutil.copyfile(self.path, self.path.with_name("fork.jsonl"))
        self.assertEqual(self.outputs(), first)
        archived = self.home / "archived_sessions"
        archived.mkdir()
        self.path.rename(archived / "a.jsonl")
        self.assertEqual(self.outputs(), first)
        self.path = self.path.with_name("fork.jsonl")
        self.write([complete("turn-b")], "a")
        self.assertEqual(self.outputs()["2026-09-14"]["turns"], 2)

    def test_periods_use_beijing_dates_and_deduplicate_files_across_days(self):
        self.write([
            change(at="2026-09-13T15:59:59Z", **{"a.py": {"type": "add", "content": "x\n"}}),
            change("edit-2", at="2026-09-13T16:00:00Z", **{"a.py": {"type": "update",
                   "unified_diff": "@@ -1 +1 @@\n-x\n+y\n"}}),
            complete(), complete("turn-b", "2026-09-14T16:00:00Z"),
        ])
        outputs = self.outputs()
        self.assertEqual(set(outputs), {"2026-09-13", "2026-09-14", "2026-09-15"})
        from usage_note.output import period_output
        data = {"output_days": outputs}
        self.assertEqual(period_output(data, date(2026, 9, 14), "week"),
                         {"added": 1, "deleted": 1, "files": 1, "turns": 2, "recorded": True})
        self.assertEqual(period_output(data, date(2026, 9, 14), "month")["files"], 1)
        self.assertFalse(period_output(data, date(2026, 10, 1), "day")["recorded"])

    def test_truncation_rebuilds_output_and_excluded_model_stays_excluded(self):
        self.write([complete()])
        self.assertEqual(self.outputs()["2026-09-14"]["turns"], 1)
        self.write([{"type": "turn_context", "payload": {"model": "codex-auto-review"}}, complete()])
        self.assertEqual(self.outputs(), {})


if __name__ == "__main__":
    unittest.main()
