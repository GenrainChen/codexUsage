"""Cache presentation and refresh state without opening a Tk window."""

import queue
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from usage_note.timebase import BEIJING_TZ
from usage_note.ui import UsageNote, cache_diagnostics


class Widget:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


class CacheUiTests(unittest.TestCase):
    def setUp(self):
        self.note = UsageNote.__new__(UsageNote)
        self.note._results = queue.Queue()
        self.note._busy = False
        self.note._closed = False
        self.note._scan_error = None
        self.note._settings_error = None
        self.note._last_scan = None
        self.note._next_refresh_at = datetime(2026, 9, 23, 12, 5, tzinfo=BEIJING_TZ)
        self.note.follow_today = False
        self.note.range_mode = "calendar"
        self.note.status_label = Widget()
        self.note.warning_label = Widget()
        self.note.refresh_button = Widget()
        self.note.rebuild_button = Widget()
        self.note.account_store = SimpleNamespace(warnings=[])
        self.note._schedule = lambda *_: None
        self.rendered = []
        self.note._render = lambda: (self.rendered.append(self.note.data), self.note._render_status())
        self.account_reads = []
        self.note._read_account_data = lambda: self.account_reads.append(True) or {"account_pages": []}
        self.cached = {"days": {"2026-09-22": {}}, "hours": {}, "warnings": [],
                       "cache_status": "cached", "cache_path": "cache.sqlite3"}
        self.updated = {"days": {"2026-09-23": {}}, "hours": {}, "warnings": [],
                        "cache_status": "updated", "cache_path": "cache.sqlite3"}
        self.calls = []
        self.note.reader = SimpleNamespace(
            load_cached=lambda: self.calls.append("load") or self.cached,
            scan=lambda **kwargs: self.calls.append(("scan", kwargs)) or self.updated)

    def _start_worker(self, *, rebuild=False):
        threads = []
        with patch("usage_note.ui.threading.Thread", side_effect=lambda **kwargs:
                   SimpleNamespace(start=lambda: threads.append(kwargs["target"]), join=lambda: None)):
            self.note.refresh(rebuild=rebuild)
        self.assertEqual(len(threads), 1)
        threads[0]()

    def test_cached_result_is_visible_before_account_query_and_keeps_refresh_busy(self):
        def account_data():
            self.note._poll()
            self.assertIs(self.note.data, self.cached)
            self.assertTrue(self.note._busy)
            self.assertEqual(self.note.refresh_button.options["state"], "disabled")
            self.assertEqual(self.note.rebuild_button.options["state"], "disabled")
            self.assertIsNone(self.note._last_scan)
            self.assertIn("历史缓存", self.note.status_label.options["text"])
            return {"account_pages": []}

        self.note._read_account_data = account_data
        self._start_worker()
        self.assertEqual(self.calls, ["load", ("scan", {})])
        self.note._poll()
        self.assertIs(self.note.data, self.updated)
        self.assertFalse(self.note._busy)
        self.assertEqual(self.note.refresh_button.options["state"], "normal")
        self.assertEqual(self.note.rebuild_button.options["state"], "normal")
        self.assertIsNotNone(self.note._last_scan)

    def test_rebuild_forces_scan_and_keeps_five_minute_schedule(self):
        deadline = self.note._next_refresh_at
        self._start_worker(rebuild=True)
        self.note._poll()
        self.note._poll()
        self.assertIn(("scan", {"rebuild": True}), self.calls)
        self.assertEqual(self.note._next_refresh_at, deadline)

    def test_stale_result_does_not_claim_successful_refresh(self):
        self.note.reader.load_cached = lambda: None
        self.note.reader.scan = lambda **kwargs: {
            **self.updated, "cache_status": "stale", "warnings": ["扫描失败"]}
        self._start_worker()
        self.note._poll()
        self.assertFalse(self.note._busy)
        self.assertIsNone(self.note._last_scan)
        self.assertIn("历史缓存", self.note.status_label.options["text"])
        self.assertNotIn("已刷新", self.note.status_label.options["text"])

    def test_reader_without_cache_interface_keeps_refresh_compatible(self):
        self.note.reader = SimpleNamespace(scan=lambda: self.updated)
        self._start_worker()
        self.assertEqual(self.note._results.qsize(), 1)
        self.note._poll()
        self.assertFalse(self.note._busy)
        self.assertIs(self.note.data, self.updated)

    def test_unreadable_snapshot_does_not_prevent_live_scan(self):
        def unreadable():
            raise OSError("corrupt snapshot")

        self.note.reader.load_cached = unreadable
        self._start_worker()
        self.assertEqual(self.note._results.qsize(), 1)
        self.note._poll()
        self.assertIs(self.note.data, self.updated)
        self.assertFalse(self.note._busy)

    def test_diagnostics_explain_path_work_and_stale_state(self):
        details = cache_diagnostics({
            "cache_path": str(Path("local") / "usage-cache.sqlite3"),
            "cache_status": "stale",
            "cache_stats": {"reused_files": 9, "appended_files": 2,
                            "reread_files": 1, "bytes_read": 4096, "elapsed_ms": 37},
        })
        for part in ("usage-cache.sqlite3", "未更新", "9", "2", "1", "4.0 KiB", "37 ms"):
            with self.subTest(part=part):
                self.assertIn(part, details)

    def test_warning_dialog_keeps_cache_diagnostics_when_many_warnings(self):
        self.note.data = {**self.updated, "warnings": [f"issue {index}" for index in range(35)]}
        self.note.pricebook = SimpleNamespace(warnings=[])
        self.note._settings_error = None
        self.note.root = object()
        with patch("usage_note.ui.messagebox.showinfo") as show:
            self.note._show_warnings()
        self.assertIn("cache.sqlite3", show.call_args.args[1])

    def test_navigation_render_preserves_cached_and_stale_status(self):
        self.note.data = self.cached
        self.note._last_scan = datetime(2026, 9, 23, 11, 55, tzinfo=BEIJING_TZ)
        self.note._busy = True
        self.note.selected_date = date(2026, 9, 22)
        self.note.period = "day"
        self.note.date_button = Widget()
        self.note.prev_button = Widget()
        self.note.today_button = Widget()
        self.note.next_button = Widget()
        self.note.period_buttons = {}
        self.note.range_buttons = {}
        self.note.range_label = SimpleNamespace(pack_forget=lambda: None)
        self.note.summary_card = SimpleNamespace(set_data=lambda *_: None)
        self.note.charts = SimpleNamespace(pack_forget=lambda: None)
        self.note.pricebook = SimpleNamespace(warnings=[])
        self.note.reader.tz = BEIJING_TZ
        self.note._render_quota = lambda: None
        self.note._render_models = lambda *_: None

        UsageNote._render(self.note)
        self.assertIn("正在检查更新", self.note.status_label.options["text"])
        self.assertNotIn("已刷新", self.note.status_label.options["text"])

        self.note.data = {**self.cached, "cache_status": "stale"}
        self.note._busy = False
        self.note.selected_date = date(2026, 9, 21)
        UsageNote._render(self.note)
        self.assertIn("更新失败", self.note.status_label.options["text"])
        self.assertNotIn("已刷新", self.note.status_label.options["text"])


if __name__ == "__main__":
    unittest.main()
