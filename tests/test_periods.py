"""Calendar boundaries and chart buckets must preserve recorded consumption."""

import unittest
import struct
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

from usage_note.periods import (
    merge_models, move_period, period_bounds, period_buckets, period_models,
)

UTC = timezone.utc


class PeriodTests(unittest.TestCase):
    def test_calendar_week_crosses_year_and_starts_monday(self):
        self.assertEqual(period_bounds(date(2027, 1, 1), "week"),
                         (date(2026, 12, 28), date(2027, 1, 4)))

    def test_month_handles_leap_day(self):
        self.assertEqual(period_bounds(date(2024, 2, 29), "month"),
                         (date(2024, 2, 1), date(2024, 3, 1)))

    def test_navigation_clamps_day_in_short_month_and_crosses_year(self):
        self.assertEqual(move_period(date(2024, 3, 31), "month", -1), date(2024, 2, 29))
        self.assertEqual(move_period(date(2026, 12, 31), "month", 1), date(2027, 1, 31))
        self.assertEqual(move_period(date(2026, 9, 14), "week", -1), date(2026, 9, 7))

    def test_week_uses_hourly_records_not_interpolated_daily_total(self):
        data = {"hours": {
            "2026-09-07T01:00:00+00:00": {"a": {"input": 10, "cached": 20}},
            "2026-09-07T03:00:00+00:00": {"a": {"output": 7}},
        }, "days": {"2026-09-07": {"a": {"input": 999}}}}
        buckets = period_buckets(data, date(2026, 9, 7), "week",
                                 now=datetime(2026, 9, 7, 3, 30, tzinfo=UTC), tz=UTC)
        self.assertEqual(len(buckets), 4)
        self.assertEqual(buckets[0]["models"], {})
        self.assertEqual(buckets[1]["models"]["a"]["input"], 10)
        self.assertEqual(buckets[2]["models"], {})
        self.assertEqual(buckets[3]["models"]["a"]["output"], 7)
        self.assertTrue(buckets[3]["current"])
        self.assertFalse(buckets[2]["current"])

    def test_completed_week_has_168_hours_and_future_week_has_none(self):
        now = datetime(2026, 9, 14, 12, tzinfo=UTC)
        self.assertEqual(len(period_buckets({}, date(2026, 9, 7), "week", now=now, tz=UTC)), 168)
        self.assertEqual(period_buckets({}, date(2026, 9, 21), "week", now=now, tz=UTC), [])

    def test_month_daily_buckets_fill_gaps_and_do_not_include_future_days(self):
        data = {"days": {
            "2026-08-31": {"a": {"input": 1000}},
            "2026-09-01": {"a": {"input": 10}},
            "2026-09-03": {"a": {"input": 30}},
            "2026-10-01": {"a": {"input": 1000}},
        }}
        buckets = period_buckets(data, date(2026, 9, 2), "month",
                                 now=datetime(2026, 9, 3, 18, tzinfo=UTC), tz=UTC)
        self.assertEqual(len(buckets), 3)
        self.assertEqual(buckets[1]["models"], {})
        self.assertEqual(sum(b["models"].get("a", {}).get("input", 0) for b in buckets), 40)
        self.assertEqual(period_models(data, date(2026, 9, 2), "month")["a"]["input"], 40)

    def test_hour_keys_with_different_offsets_refer_to_distinct_instants(self):
        data = {"hours": {
            "2026-11-01T01:00:00-05:00": {"a": {"input": 10}},
            "2026-11-01T01:00:00-06:00": {"a": {"input": 20}},
        }}
        buckets = period_buckets(data, date(2026, 11, 1), "week",
                                 now=datetime(2026, 11, 2, tzinfo=UTC), tz=UTC)
        by_hour = {b["at"].isoformat(): b["models"] for b in buckets}
        self.assertEqual(by_hour["2026-11-01T06:00:00+00:00"]["a"]["input"], 10)
        self.assertEqual(by_hour["2026-11-01T07:00:00+00:00"]["a"]["input"], 20)

    def test_period_models_merges_counts_maximums_and_metadata_without_mutation(self):
        a = {"m": {"input": 100, "cached": 50, "output": 5, "cache_write": 20,
                   "requests": 1, "max_request_input": 150, "service_tiers": ["standard"]}}
        b = {"m": {"input": 10, "cached": 3, "output": 2, "reasoning": 1,
                   "requests": 2, "max_request_input": 20, "service_tiers": ["priority"]}}
        result = merge_models([a, b])["m"]
        self.assertEqual([result[k] for k in ("input", "cached", "output", "cache_write", "requests")],
                         [110, 53, 7, 20, 3])
        self.assertEqual(result["max_request_input"], 150)
        self.assertEqual(result["service_tiers"], ["priority", "standard"])
        self.assertEqual(a["m"]["input"], 100)
        self.assertEqual(a["m"]["service_tiers"], ["standard"])

    def test_local_month_boundary_uses_local_date(self):
        tz = timezone(timedelta(hours=-5))
        buckets = period_buckets({}, date(2026, 8, 31), "month",
                                 now=datetime(2026, 9, 1, 2, tzinfo=UTC), tz=tz)
        self.assertEqual(len(buckets), 31)
        self.assertTrue(buckets[-1]["current"])
        self.assertEqual(buckets[-1]["at"].day, 31)

    def test_dst_weeks_have_167_or_169_real_hours_and_keep_repeated_hour(self):
        transitions = [int(datetime(2026, 3, 8, 7, tzinfo=UTC).timestamp()),
                       int(datetime(2026, 11, 1, 6, tzinfo=UTC).timestamp())]
        tzif = (b"TZif\0" + b"\0" * 15 + struct.pack(">6l", 0, 0, 0, 2, 2, 8)
                + struct.pack(">2l", *transitions) + b"\x00\x01"
                + struct.pack(">lbb", -4 * 3600, 1, 0)
                + struct.pack(">lbb", -5 * 3600, 0, 4) + b"EDT\0EST\0")
        tz = ZoneInfo.from_file(BytesIO(tzif))
        now = datetime(2026, 12, 1, tzinfo=UTC)
        self.assertEqual(len(period_buckets({}, date(2026, 3, 8), "week", now=now, tz=tz)), 167)
        data = {"hours": {
            "2026-11-01T01:00:00-04:00": {"a": {"input": 10}},
            "2026-11-01T01:00:00-05:00": {"a": {"input": 20}},
        }}
        buckets = period_buckets(data, date(2026, 11, 1), "week", now=now, tz=tz)
        self.assertEqual(len(buckets), 169)
        repeated = [b for b in buckets if b["at"].date() == date(2026, 11, 1) and b["at"].hour == 1]
        self.assertEqual([b["models"]["a"]["input"] for b in repeated], [10, 20])
        self.assertEqual([b["current"] for b in repeated], [False, False])


if __name__ == "__main__":
    unittest.main()
