"""The note uses UTC+8 even when Windows is configured for another timezone."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from usage_note.reader import UsageReader
from usage_note.periods import period_buckets, period_models, local_midnight
from usage_note.ui import local_time
from usage_note.timebase import BEIJING_TZ, today as note_today


class BeijingTimeTests(unittest.TestCase):
    def test_default_reader_rebuckets_at_beijing_midnight_and_monday(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            sessions = home / "sessions"
            sessions.mkdir()
            records = [{"type": "turn_context", "payload": {"model": "model-a", "turn_id": "turn-a"}}]
            for stamp, total in (("2026-09-13T15:59:59Z", 100), ("2026-09-13T16:00:00Z", 200)):
                records.append({"type": "event_msg", "timestamp": stamp, "payload": {
                    "type": "token_count", "info": {"total_token_usage": {
                        "input_tokens": total, "output_tokens": 0, "total_tokens": total}}}})
            (sessions / "usage.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
            data = UsageReader(home).scan()
            self.assertEqual(set(data["days"]), {"2026-09-13", "2026-09-14"})
            self.assertEqual(list(data["hours"]), ["2026-09-13T23:00:00+08:00", "2026-09-14T00:00:00+08:00"])
            self.assertEqual(period_models(data, date(2026, 9, 14), "week")["model-a"]["input"], 100)
            buckets = period_buckets(data, date(2026, 9, 14), "week",
                                     now=datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc))
            self.assertEqual(len(buckets), 1)
            self.assertEqual(buckets[0]["models"]["model-a"]["input"], 100)
            self.assertEqual(buckets[0]["at"].utcoffset(), timedelta(hours=8))

    def test_month_begins_at_beijing_midnight_before_utc_date_changes(self):
        self.assertEqual(local_midnight(date(2026, 10, 1)).isoformat(), "2026-10-01T00:00:00+08:00")
        buckets = period_buckets({}, date(2026, 10, 1), "month",
                                 now=datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc))
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]["at"].date(), date(2026, 10, 1))
        self.assertTrue(buckets[0]["current"])

    def test_quota_snapshot_and_reset_epoch_display_in_beijing_time(self):
        instant = datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(local_time(instant.isoformat()), "09-14 00:30")
        self.assertEqual(local_time(instant.timestamp()), "09-14 00:30")
        self.assertEqual(local_time("2026-09-13T11:30:00-05:00"), "09-14 00:30")
        self.assertEqual(local_time(None), "未知时间")

    def test_today_follows_beijing_calendar_when_utc_is_still_previous_day(self):
        instant = datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc)
        with patch("usage_note.timebase.now", return_value=instant.astimezone(BEIJING_TZ)):
            self.assertEqual(note_today(), date(2026, 9, 14))


if __name__ == "__main__":
    unittest.main()
