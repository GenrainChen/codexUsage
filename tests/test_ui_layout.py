"""Real Tk checks for controls that must survive a small desktop window."""

import json
import os
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest.mock import patch

from usage_note.pricing import PriceBook
from usage_note.reader import UsageReader
from usage_note.ui import UsageNote
from usage_note.periods import period_bounds
from usage_note.timebase import BEIJING_TZ, now as note_now


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@unittest.skipUnless(os.environ.get("CODEX_NOTE_GUI_TESTS") == "1",
                     "Set CODEX_NOTE_GUI_TESTS=1 to allow visible Tk layout tests")
class LayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)
        (path / "prices.json").write_text(json.dumps({"models": {
            "model-with-a-long-name": {"input": 2, "cached": .2, "output": 8,
                "source": "https://example.invalid/" + "very-long-price-source/" * 30}
        }}), encoding="utf-8")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.addCleanup(self._close)
        self.note = UsageNote(self.root, UsageReader(path),
                              PriceBook(path / "prices.json", path / "overrides.json"),
                              path / "settings.json")
        self.root.title("Codex 用量便签 · 布局回归测试")
        self.root.attributes("-topmost", False)
        deadline = time.monotonic() + 3
        while self.note._busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.note._busy, "empty local reader did not finish")
        self.note.data = {
            "days": {self.note.selected_date.isoformat(): {
                "model-with-a-long-name": {
                    "input": 9_999_999, "cached": 88_888_888,
                    "output": 3_333_333, "cache_write": 12_345,
                },
                "unknown-model": {"input": 1, "cached": 2, "output": 3},
            }},
            "latest_rate_limits": {
                "primary": {"window_minutes": 300, "used_percent": 25.7,
                            "resets_at": 1_800_000_000},
                "secondary": {"window_minutes": 10080, "used_percent": 49.4,
                              "resets_at": 1_800_010_000},
            },
            "latest_rate_limits_at": "2026-09-14T03:00:00+00:00",
            "warnings": ["测试提示，应该在最小窗口中可见"],
            "files_scanned": 1,
        }
        hour = note_now().replace(minute=0, second=0, microsecond=0).isoformat()
        self.note.data["hours"] = {hour: self.note.data["days"][self.note.selected_date.isoformat()]}
        self.note._render()

    def _close(self):
        if hasattr(self, "note") and self.note._closed:
            return
        for child in list(self.root.winfo_children()):
            if isinstance(child, tk.Toplevel):
                child.destroy()
        if hasattr(self, "note"):
            self.note.close()
        else:
            self.root.destroy()

    def assert_fully_visible(self, widget, window):
        self.assertTrue(widget.winfo_ismapped(), f"{widget} was not mapped")
        left = widget.winfo_rootx() - window.winfo_rootx()
        top = widget.winfo_rooty() - window.winfo_rooty()
        self.assertGreaterEqual(left, 0)
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(left + widget.winfo_width(), window.winfo_width())
        self.assertLessEqual(top + widget.winfo_height(), window.winfo_height())
        self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight(),
                                "the control was vertically clipped")

    def test_footer_and_disclaimer_survive_normal_and_minimum_size(self):
        footer = self.note.warning_label.master
        disclaimer = next(child for child in descendants(footer)
                          if isinstance(child, tk.Button) and "非订阅账单" in child.cget("text"))
        for width, height in ((460, 800), (390, 650)):
            with self.subTest(size=f"{width}x{height}"):
                self.root.geometry(f"{width}x{height}")
                self.root.update_idletasks()
                self.root.update()
                self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (width, height))
                self.assert_fully_visible(footer, self.root)
                self.assert_fully_visible(self.note.warning_label, self.root)
                self.assert_fully_visible(disclaimer, self.root)
                self.assert_fully_visible(self.note.refresh_button, self.root)

    def test_summary_swipes_between_consumption_and_output_without_height_jump(self):
        self.assertTrue(hasattr(self.note, "summary_card"), "Consumption/output pager is required")
        card = self.note.summary_card
        self.note.data["output_days"] = {self.note.selected_date.isoformat(): {
            "added": 1234, "deleted": 56, "files": ["file-a", "file-b"], "turns": 7}}
        self.note._render()
        for size in ("390x650", "460x800"):
            self.root.geometry(size)
            card.show_page(0)
            self.root.update()
            original = (card.winfo_height(), self.note.canvas.winfo_rooty(), self.note.canvas.winfo_height())
            self.assertIn("TOKEN", card.total_caption.cget("text"))
            card.total_tokens.event_generate("<ButtonPress-1>", x=220, y=15)
            card.total_tokens.event_generate("<ButtonRelease-1>", x=100, y=17)
            self.root.update()
            self.assertEqual(card.page, 1)
            self.assertEqual(card.total_tokens.cget("text"), "1,290")
            self.assertEqual(card.total_price.cget("text"), "7 轮")
            self.assertEqual(original, (card.winfo_height(), self.note.canvas.winfo_rooty(), self.note.canvas.winfo_height()))
            for widget in (card.total_tokens, card.prev_button, card.next_button, card.note):
                self.assert_fully_visible(widget, self.root)
            self.note._render()
            self.assertEqual(card.page, 1, "Refreshing must keep the selected page")
            card.prev_button.invoke()
            self.root.update()
            self.assertEqual(card.page, 0)
            self.assertIn("TOKEN", card.total_caption.cget("text"))
            self.assertEqual(original, (card.winfo_height(), self.note.canvas.winfo_rooty(), self.note.canvas.winfo_height()))

    def test_summary_ignores_vertical_drags_and_handles_empty_output(self):
        self.assertTrue(hasattr(self.note, "summary_card"), "Consumption/output pager is required")
        card = self.note.summary_card
        self.root.update()
        card.total_tokens.event_generate("<ButtonPress-1>", x=200, y=15)
        card.total_tokens.event_generate("<ButtonRelease-1>", x=195, y=80)
        self.root.update()
        self.assertEqual(card.page, 0)
        card.next_button.invoke()
        self.root.update()
        self.assertEqual(card.total_tokens.cget("text"), "—")
        self.assertIn("暂无", card.note.cget("text"))
        self.root.focus_force()
        card.focus_set()
        self.root.update()
        card.event_generate("<Left>")
        self.root.update()
        self.assertEqual(card.page, 0)

    def test_date_picker_includes_days_with_only_output_records(self):
        self.note.data["output_days"] = {"2026-01-02": {
            "added": 5, "deleted": 2, "files": ["file-a"], "turns": 1}}
        self.note.summary_card.show_page(1)
        self.note._choose_date()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        entry = next(w for w in descendants(dialog) if isinstance(w, ttk.Combobox))
        self.assertIn("2026-01-02", entry.cget("values"))
        entry.set("2026-01-02")
        next(w for w in descendants(dialog) if isinstance(w, tk.Button) and w.cget("text") == "查看").invoke()
        self.assertEqual(self.note.summary_card.total_tokens.cget("text"), "7")

    def test_price_save_button_remains_visible_with_long_source(self):
        self.note._edit_prices("model-with-a-long-name")
        dialog = next(child for child in self.root.winfo_children()
                      if isinstance(child, tk.Toplevel))
        self.root.update_idletasks()
        self.root.update()
        save = next(child for child in descendants(dialog)
                    if isinstance(child, tk.Button) and child.cget("text") == "保存本地价格")
        self.assert_fully_visible(save, dialog)

    def test_period_curves_match_totals_and_mark_partial_prices_on_hover(self):
        for mode in ("week", "month"):
            with self.subTest(mode=mode):
                self.note.period_buttons[mode].invoke()
                self.root.update()
                self.assertTrue(self.note.charts.winfo_ismapped())
                points = self.note.charts.points
                self.assertEqual(sum(p["tokens"] for p in points),
                                 int(self.note.total_tokens.cget("text").replace(",", "")))
                self.assertTrue(any(not p["fully_priced"] for p in points))
                self.assertEqual(self.note.next_button.cget("state"), "disabled")
                index = next(i for i, p in enumerate(points) if p["tokens"])
                x = self.note.charts._coords["tokens"][index][0]
                self.note.charts.plots["tokens"].event_generate("<Motion>", x=round(x), y=60)
                self.root.update()
                self.assertIn("已知价", self.note.charts.detail.cget("text"))
                self.assertIn(f"{points[index]['tokens']:,}", self.note.charts.detail.cget("text"))
                self.note.charts._clear_hover()
                self.note.charts.plots["tokens"].event_generate("<Button-1>", x=round(x), y=60)
                self.root.update()
                self.assertIn(f"{points[index]['tokens']:,}", self.note.charts.detail.cget("text"))
                texts = [self.note.charts.plots["known_usd"].itemcget(item, "text")
                         for item in self.note.charts.plots["known_usd"].find_all()
                         if self.note.charts.plots["known_usd"].type(item) == "text"]
                self.assertTrue(any("已知价小计" in value for value in texts))
        self.note.period_buttons["day"].invoke()
        self.root.update()
        self.assertFalse(self.note.charts.winfo_ismapped())

    def test_week_month_navigation_and_controls_at_minimum_width(self):
        self.root.geometry("390x650")
        for mode in ("week", "month"):
            self.note.period_buttons[mode].invoke()
            original = period_bounds(self.note.selected_date, mode)
            self.note._move_date(-1)
            self.assertLess(period_bounds(self.note.selected_date, mode)[0], original[0])
            self.assertEqual(self.note.next_button.cget("state"), "normal")
            self.note.next_button.invoke()
            self.assertEqual(period_bounds(self.note.selected_date, mode), original)
            self.root.update()
            for control in (*self.note.period_buttons.values(), self.note.today_button,
                            self.note.date_button, self.note.next_button, self.note.refresh_button):
                self.assert_fully_visible(control, self.root)
            self.note.canvas.yview_moveto(1)
            self.root.update()
            self.assertGreater(self.note.canvas.yview()[0], 0)
            self.note.today_button.invoke()
            self.root.update()
            self.assertEqual(self.note.canvas.yview()[0], 0)
        self.note.selected_date = date(2025, 1, 1)
        self.note._set_period("week")
        self.root.update()
        self.assert_fully_visible(self.note.today_button, self.root)
        self.assert_fully_visible(self.note.next_button, self.root)

    def test_general_quota_is_visible_and_spark_is_hidden(self):
        self.note.data["latest_rate_limits_by_id"] = {
            "codex": {"at": "2026-09-14T03:01:00+00:00", "rate_limits": {
                "limit_id": "codex", "primary": {"window_minutes": 10080, "used_percent": 26}}},
            "codex_bengalfox": {"at": "2026-09-14T03:02:00+00:00", "rate_limits": {
                "limit_id": "codex_bengalfox", "primary": {"window_minutes": 300, "used_percent": 0},
                "secondary": {"window_minutes": 10080, "used_percent": 10}}},
        }
        self.note.data["account_pages"] = [{
            "key": "a" * 64, "email": "a@example.test", "current": True,
            "at": "2026-09-14T03:02:00+00:00",
            "limits": self.note.data["latest_rate_limits_by_id"],
        }]
        self.note._render()
        self.root.geometry("390x650")
        self.root.update()
        texts = [child.cget("text") for child in descendants(self.note.quota) if isinstance(child, tk.Label)]
        self.assertIn("通用额度", texts)
        self.assertNotIn("GPT-5.3-Codex-Spark", texts)
        self.assertEqual(texts.count("每周"), 1)
        self.assertEqual(texts.count("5 小时"), 0)
        self.assertIn("余 74%", texts)
        self.assertNotIn("余 100%", texts)
        self.assertNotIn("余 90%", texts)
        buttons = [child.cget("text") for child in descendants(self.note.quota)
                   if isinstance(child, tk.Button)]
        self.assertFalse(any("日志额度" in text for text in buttons))

    def test_switching_from_current_month_to_historical_day_survives_refresh(self):
        with patch("usage_note.ui.note_today", return_value=date(2026, 9, 14)):
            self.note.selected_date = date(2026, 9, 8)
            self.note._set_period("month")
            self.assertTrue(self.note.follow_today)
            self.note._set_period("day")
            self.note._results.put((self.note.data, None))
            self.note._poll()
            self.assertEqual(self.note.selected_date, date(2026, 9, 8))
            self.assertFalse(self.note.follow_today)
            self.note._set_period("month")
            self.assertTrue(self.note.follow_today)

    def test_beijing_today_navigation_and_refresh_use_the_same_calendar(self):
        with patch("usage_note.timebase.now", return_value=datetime(2026, 9, 14, 0, 30, tzinfo=BEIJING_TZ)):
            self.note.selected_date = date(2026, 9, 13)
            self.note._today()
            self.assertEqual(self.note.selected_date, date(2026, 9, 14))
            self.note._move_date(1)
            self.assertEqual(self.note.selected_date, date(2026, 9, 14))
            self.note._set_period("week")
            self.note._results.put((self.note.data, None))
            self.note._poll()
            self.assertEqual(self.note.next_button.cget("state"), "disabled")
            self.assertTrue(self.note.follow_today)
            self.assertEqual(self.note._last_scan.utcoffset(), BEIJING_TZ.utcoffset(None))
            self.root.geometry("390x650")
            self.root.update()
            self.assert_fully_visible(self.note.timezone_label, self.root)
            self.assertIn("UTC+8", self.note.timezone_label.cget("text"))

    def test_manual_refresh_preserves_next_automatic_boundary(self):
        target = self.note._next_refresh_at
        instant = target - timedelta(minutes=2, seconds=40)
        with patch("usage_note.ui.note_now", return_value=instant):
            self.note.refresh_button.invoke()
            deadline = time.monotonic() + 3
            while self.note._busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            self.assertFalse(self.note._busy)
            self.assertEqual(self.note._last_scan, instant)
            self.assertEqual(self.note._next_refresh_at, target)
            self.assertIn(f"下次 {target:%H:%M}", self.note.status_label.cget("text"))

    def test_model_pages_show_one_card_and_preserve_selection_after_refresh(self):
        def texts():
            return [child.cget("text") for child in descendants(self.note.models_frame)
                    if isinstance(child, tk.Label)]
        self.assertNotIn("unknown-model", texts(), "Only the selected model should be shown")
        self.assertIn("model-with-a-long-name", texts())
        self.assertEqual(self.note.model_prev_button.cget("state"), "disabled")
        self.assertEqual(self.note.model_page_label.cget("text"), "1 / 2")
        self.note.model_next_button.invoke()
        self.assertIn("unknown-model", texts())
        self.assertNotIn("model-with-a-long-name", texts())
        self.assertEqual(self.note.model_next_button.cget("state"), "disabled")
        self.assertEqual(self.note.model_page_label.cget("text"), "2 / 2")
        models = self.note.data["days"][self.note.selected_date.isoformat()]
        models["unknown-model"]["input"] = 999_999_999
        self.note._results.put((self.note.data, None))
        self.note._poll()
        self.assertIn("unknown-model", texts(), "Auto-refresh must preserve the selected model")
        self.assertEqual(self.note.model_page_label.cget("text"), "1 / 2")
        self.note.model_next_button.invoke()
        self.assertIn("model-with-a-long-name", texts())
        self.note.model_prev_button.invoke()
        self.assertIn("unknown-model", texts())

    def test_model_navigation_handles_empty_period_and_single_model(self):
        self.assertTrue(hasattr(self.note, "model_page_label"), "Model pager is required")
        self.note._move_date(-1)
        self.assertEqual(self.note.model_page_label.cget("text"), "0 / 0")
        self.assertEqual(self.note.model_prev_button.cget("state"), "disabled")
        self.assertEqual(self.note.model_next_button.cget("state"), "disabled")
        self.note.data["days"][self.note.selected_date.isoformat()] = {
            "only-model": {"input": 100, "cached": 0, "output": 0}}
        self.note._render()
        self.assertEqual(self.note.model_page_label.cget("text"), "1 / 1")
        self.assertEqual(self.note.model_next_button.cget("state"), "disabled")
        self.assertEqual(self.note.model_prev_button.cget("state"), "disabled")

    def test_current_account_changes_without_relabeling_historical_quotas(self):
        self.note.data["current_account"] = {"status": "signed_in", "email": "first@example.test", "plan": "pro"}
        self.note._render()
        texts = [child.cget("text") for child in descendants(self.note.quota) if isinstance(child, tk.Label)]
        self.assertTrue(any("当前登录" in text and "first@example.test" in text for text in texts))
        self.assertIn("尚无该账号的额度快照", texts)
        self.assertFalse(any(text.startswith("余 ") for text in texts))
        self.note.data["current_account"] = {"status": "signed_in", "email": "second-with-a-very-long-address@example.test", "plan": "pro"}
        self.note._results.put((self.note.data, None))
        self.note._poll()
        self.root.geometry("390x650")
        self.root.update()
        self.assertNotIn("first@example.test", self.note.current_account_label.cget("text"))
        self.assertIn("second-with-a-very-long-address", self.note.current_account_label.cget("text"))
        self.assertLessEqual(self.note.current_account_label.winfo_reqwidth(), self.note.quota.winfo_width())
        self.note.canvas.yview_moveto(1)
        self.root.update()
        for control in (self.note.model_prev_button, self.note.model_page_label, self.note.model_next_button):
            self.assert_fully_visible(control, self.root)
        self.note.data["current_account"] = {"status": "unavailable"}
        self.note._render()
        self.assertNotIn("second-with-a-very-long-address", self.note.current_account_label.cget("text"))

    def test_window_position_is_saved_after_moving_and_restored_on_reopen(self):
        self.root.geometry("410x780+160+90")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        expected = self.root.geometry()
        path = self.note.settings_path
        self.assertTrue(path.exists(), "Moving the window must save without requiring close")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["geometry"], expected)
        self.root.geometry("420x790+210+110")
        self.root.update()
        expected = self.root.geometry()
        reader, prices = self.note.reader, self.note.pricebook
        self.note.close()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["geometry"], expected)
        self.root = tk.Tk()
        self.note = UsageNote(self.root, reader, prices, path)
        self.root.title("Codex 用量便签 · 位置恢复测试")
        self.root.attributes("-topmost", False)
        self.root.update()
        self.assertEqual(self.root.geometry(), expected)

    def test_closing_minimized_window_keeps_last_normal_position(self):
        self.root.geometry("410x780+160+90")
        self.root.update()
        expected = self.root.geometry()
        path = self.note.settings_path
        self.root.iconify()
        self.root.update()
        self.note.close()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["geometry"], expected)

    def test_account_pager_current_first_then_reset_order_and_refresh_keeps_selection(self):
        self.assertTrue(hasattr(self.note, "account_store"), "Per-account quota store is required")
        from tests.test_account_store import snapshot
        self.note.account_store.remember(snapshot("a", 2_000_000_000))
        self.note.account_store.remember(snapshot("b", 1_850_000_000, spark_reset=1_950_000_000))
        self.note.account_store.remember(snapshot("c", 1_900_000_000))
        current = {"key": "a" * 64, "email": "a@example.test", "status": "signed_in"}
        self.note.data["current_account"] = current
        self.note.data["account_pages"] = self.note.account_store.pages(current, note_now())
        self.note._render()
        self.assertIn("当前登录", self.note.current_account_label.cget("text"))
        self.assertIn("a@example.test", self.note.current_account_label.cget("text"))
        self.assertEqual(self.note.account_page_label.cget("text"), "1 / 3")
        self.assertEqual(self.note.account_prev_button.cget("state"), "disabled")
        self.note.account_next_button.invoke()
        self.assertIn("b@example.test", self.note.current_account_label.cget("text"))
        self.note.account_store.remember(snapshot("b", 2_050_000_000))
        self.note.data["account_pages"] = self.note.account_store.pages(current, note_now())
        self.note._render()
        self.assertIn("b@example.test", self.note.current_account_label.cget("text"))
        self.assertEqual(self.note.account_page_label.cget("text"), "3 / 3")
        self.assertEqual(self.note.account_next_button.cget("state"), "disabled")
        current = {"key": "c" * 64, "email": "c@example.test", "status": "signed_in"}
        self.note.data["current_account"] = current
        self.note.data["account_pages"] = self.note.account_store.pages(current, note_now())
        self.note._render()
        self.assertIn("当前登录：c@example.test", self.note.current_account_label.cget("text"))
        self.assertEqual(self.note.account_page_label.cget("text"), "1 / 3")

    def test_expired_account_snapshot_is_not_shown_as_fresh_remaining_quota(self):
        self.assertTrue(hasattr(self.note, "account_store"))
        from tests.test_account_store import snapshot
        self.note.account_store.remember(snapshot("a", 1_700_000_000))
        current = {"key": "a" * 64, "email": "a@example.test", "status": "signed_in"}
        self.note.data["account_pages"] = self.note.account_store.pages(current, note_now())
        self.note._render()
        texts = [w.cget("text") for w in descendants(self.note.quota) if isinstance(w, tk.Label)]
        self.assertTrue(any("待刷新" in t for t in texts))
        self.assertFalse(any(t.startswith("余 70%") for t in texts))


if __name__ == "__main__":
    unittest.main()
