"""User-visible totals must stay honest when some prices are unavailable."""

import json
import tempfile
import unittest
from pathlib import Path
from datetime import date, datetime, timezone

from usage_note.pricing import PriceBook
from usage_note.ui import money, summarize_day, window_label
from usage_note.periods import period_buckets, period_models


class SummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)
        (path / "prices.json").write_text(json.dumps({"models": {
            "known": {"input": 2, "cached": .2, "output": 8, "source": "test"}
        }}), encoding="utf-8")
        self.prices = PriceBook(path / "prices.json", path / "overrides.json")

    def test_known_price_subtotal_survives_unknown_model(self):
        result = summarize_day({
            "known": {"input": 1_000_000, "cached": 2_000_000, "output": 500_000},
            "unknown": {"input": 20, "cached": 10, "output": 5},
        }, self.prices)
        self.assertEqual(result["tokens"], 3_500_035)
        self.assertAlmostEqual(result["known_usd"], 6.4)
        self.assertFalse(result["fully_priced"])
        self.assertEqual(result["unknown_models"], ["unknown"])
        self.assertTrue(result["categories"]["input"]["partial"])

    def test_unpriced_cache_write_does_not_claim_complete_price(self):
        result = summarize_day({
            "known": {"input": 100, "cached": 50, "output": 10, "cache_write": 25},
        }, self.prices)
        self.assertEqual(result["cache_write"], 25)
        self.assertEqual(result["tokens"], 160)
        self.assertFalse(result["fully_priced"])
        self.assertAlmostEqual(result["known_usd"], .00024)

    def test_empty_day_is_zero_not_missing(self):
        result = summarize_day({}, self.prices)
        self.assertEqual(result["tokens"], 0)
        self.assertEqual(result["known_usd"], 0)
        self.assertTrue(result["fully_priced"])

    def test_rate_limit_window_uses_actual_duration(self):
        self.assertEqual(window_label({"window_minutes": 60}), "1 小时")
        self.assertEqual(window_label({"window_minutes": 300}), "5 小时")
        self.assertEqual(window_label({"window_minutes": 10080}), "7 天")
        self.assertEqual(window_label({}), "额度窗口")

    def test_tiny_nonzero_amount_is_not_presented_as_zero(self):
        self.assertEqual(money(.00000002), "<$0.000001")
        self.assertEqual(money(0), "$0.00")

    def test_week_and_month_curve_costs_sum_to_period_with_mixed_prices(self):
        first = {"known": {"input": 1_000_000, "cached": 2_000_000, "output": 500_000}}
        second = {"known": {"input": 100, "cache_write": 25}, "unknown": {"output": 10}}
        data = {"days": {"2026-09-08": first, "2026-09-09": second},
                "hours": {"2026-09-08T10:00:00+00:00": first, "2026-09-09T12:00:00+00:00": second}}
        for mode in ("week", "month"):
            summary = summarize_day(period_models(data, date(2026, 9, 9), mode), self.prices)
            points = [summarize_day(b["models"], self.prices) for b in period_buckets(
                data, date(2026, 9, 9), mode, now=datetime(2026, 9, 15, tzinfo=timezone.utc), tz=timezone.utc)]
            self.assertEqual(sum(p["tokens"] for p in points), summary["tokens"])
            self.assertAlmostEqual(sum(p["known_usd"] for p in points), 6.40015)
            self.assertAlmostEqual(sum(p["known_usd"] for p in points), summary["known_usd"])
            self.assertFalse(summary["fully_priced"])
            self.assertEqual(sum(not p["fully_priced"] for p in points), 1)


if __name__ == "__main__":
    unittest.main()
