"""Real Tk behavior for persisted rolling periods and precise chart captions."""

import json
import os
import tempfile
import time
import tkinter as tk
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from usage_note.pricing import PriceBook
from usage_note.reader import UsageReader
from usage_note.timebase import BEIJING_TZ
from usage_note.ui import UsageNote


@unittest.skipUnless(os.environ.get("CODEX_NOTE_GUI_TESTS") == "1", "Opt in to visible Tk tests")
class RecentUiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.settings = self.path / "settings.json"
        self.instant = datetime(2026, 10, 12, 15, 30, 17, tzinfo=BEIJING_TZ)
        (self.path / "prices.json").write_text(json.dumps({"models": {
            "m": {"input": 2, "cached": .2, "output": 8}}}), encoding="utf-8")
        self.clock = patch("usage_note.ui.note_now", return_value=self.instant)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self._open()
        self.addCleanup(self._close)

    def _open(self):
        self.root = tk.Tk()
        self.note = UsageNote(self.root, UsageReader(self.path),
                              PriceBook(self.path / "prices.json", self.path / "overrides.json"), self.settings)
        self.root.attributes("-topmost", False)
        deadline = time.monotonic() + 3
        while self.note._busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.note._busy)

    def _close(self):
        if not self.note._closed:
            self.note.close()

    def _fixture(self):
        start = self.instant - timedelta(days=1)
        self.note.data = {"days": {"2026-10-12": {"m": {"input": 777}}}, "instants": {
            (start - timedelta(seconds=1)).isoformat(): {"m": {"input": 1000}},
            start.isoformat(): {"m": {"input": 10}},
            (self.instant - timedelta(seconds=1)).isoformat(): {"m": {"input": 20}},
            self.instant.isoformat(): {"m": {"input": 1000}},
        }, "output_instants": {
            start.isoformat(): {"added": 3, "deleted": 2, "files": ["hash"], "turns": 1},
            self.instant.isoformat(): {"added": 1000, "deleted": 0, "files": ["other"], "turns": 0},
        }}

    def test_recent_switch_totals_output_and_chart_share_exact_clock(self):
        self.assertTrue(hasattr(self.note, "range_buttons"), "Natural/recent selector required")
        self._fixture()
        self.note.range_buttons["recent"].invoke()
        self.root.update()
        self.assertEqual(self.note.total_tokens.cget("text"), "30")
        self.assertTrue(self.note.charts.winfo_ismapped(), "Recent day needs an hourly chart")
        self.assertEqual(sum(p["tokens"] for p in self.note.charts.points), 30)
        self.assertEqual(self.note.charts.points[-1]["end"], self.instant)
        self.note.summary_card.show_page(1)
        self.assertEqual(self.note.total_tokens.cget("text"), "5")
        self.assertIn("最近一天", self.note.total_caption.cget("text"))
        self.assertIn("2026-10-11 15:30:17", self.note.range_label.cget("text"))
        self.assertIn("2026-10-12 15:30:17", self.note.range_label.cget("text"))
        points = self.note.charts.points
        self.note.charts._hover(self.note.charts._coords["tokens"][0][0], "tokens")
        self.assertIn("15:30:17", self.note.charts.detail.cget("text"))
        self.note.charts._hover(self.note.charts._coords["tokens"][-1][0], "tokens")
        self.assertIn("15:30:17", self.note.charts.detail.cget("text"))
        self.assertIn("截至当前", self.note.charts.detail.cget("text"))
        for canvas in self.note.charts.plots.values():
            texts = [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]
            self.assertNotIn("尚未发生", texts)

    def test_recent_refresh_advances_cutoff_and_restores_calendar_navigation(self):
        self.assertTrue(hasattr(self.note, "range_buttons"), "Natural/recent selector required")
        self._fixture()
        self.note.selected_date = date(2026, 9, 1)
        self.note.follow_today = False
        self.note.range_buttons["recent"].invoke()
        for button in (self.note.prev_button, self.note.next_button, self.note.date_button):
            self.assertEqual(button.cget("state"), "disabled")
        with patch("usage_note.ui.note_now", return_value=self.instant + timedelta(seconds=1)):
            self.note.today_button.invoke()
            self.assertEqual(self.note.charts.points[-1]["end"], self.instant + timedelta(seconds=1))
            self.note.summary_card.show_page(0)
            self.assertEqual(self.note.total_tokens.cget("text"), "1,020")
            self.note._results.put((self.note.data, None))
            self.note._poll()
            self.assertEqual(sum(p["tokens"] for p in self.note.charts.points), 1020)
        self.note.range_buttons["calendar"].invoke()
        self.assertEqual(self.note.selected_date, date(2026, 9, 1))
        self.assertEqual(self.note.date_button.cget("state"), "normal")
        self.assertEqual(self.note.prev_button.cget("state"), "normal")
        self.assertFalse(self.note.charts.winfo_manager())

    def test_recent_selection_persists_and_controls_fit_minimum_window(self):
        self.assertTrue(hasattr(self.note, "range_buttons"), "Natural/recent selector required")
        self.assertEqual(self.note.range_mode, "calendar")
        self.note.range_buttons["recent"].invoke()
        self.root.geometry("390x650")
        for mode, caption in (("day", "最近一天"), ("week", "最近一周"), ("month", "最近一个月")):
            self.note.period_buttons[mode].invoke()
            self.root.update()
            self.assertIn(caption, self.note.total_caption.cget("text"))
            self.assertIn("小时" if mode != "month" else "天", self.note.charts.heading.cget("text"))
            for widget in (*self.note.range_buttons.values(), *self.note.period_buttons.values(),
                           self.note.date_button, self.note.prev_button, self.note.next_button,
                           self.note.today_button, self.note.range_label, self.note.refresh_button):
                self.assertTrue(widget.winfo_ismapped())
                x, y = widget.winfo_rootx() - self.root.winfo_rootx(), widget.winfo_rooty() - self.root.winfo_rooty()
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + widget.winfo_width(), 390)
                self.assertLessEqual(y + widget.winfo_height(), 650)
                self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
            for canvas in self.note.charts.plots.values():
                for item in canvas.find_all():
                    if canvas.type(item) == "text" and canvas.coords(item)[1] > 112:
                        left, _, right, _ = canvas.bbox(item)
                        self.assertGreaterEqual(left, 0, canvas.itemcget(item, "text"))
                        self.assertLessEqual(right, canvas.winfo_width(), canvas.itemcget(item, "text"))
        self.note.close()
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["range_mode"], "recent")
        self._open()
        self.assertEqual(self.note.range_mode, "recent")
        self.assertEqual(self.note.period, "month")
        self.assertTrue(self.note.charts.winfo_manager())

    def test_returning_to_calendar_tracks_today_after_midnight(self):
        self.assertTrue(hasattr(self.note, "range_buttons"), "Natural/recent selector required")
        self.note.selected_date = date(2026, 10, 12)
        self.note.follow_today = True
        self.note.range_buttons["recent"].invoke()
        with patch("usage_note.ui.note_today", return_value=date(2026, 10, 13)):
            self.note.range_buttons["calendar"].invoke()
        self.assertEqual(self.note.selected_date, date(2026, 10, 13))


if __name__ == "__main__":
    unittest.main()
