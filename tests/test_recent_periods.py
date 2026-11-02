"""Rolling statistics use exact recorded instants, including clipped bins."""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from usage_note import periods
from usage_note.output import period_output
from usage_note.reader import UsageReader
from usage_note.timebase import BEIJING_TZ
from tests.test_reader import meta, context, event, usage
from tests.test_output import change, complete


class RecentPeriodTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 15, 30, tzinfo=BEIJING_TZ)

    def test_bounds_are_fixed_elapsed_durations_across_leap_month_boundary(self):
        self.assertTrue(hasattr(periods, "recent_bounds"), "Rolling bounds are required")
        now = datetime(2024, 3, 15, 15, 30, tzinfo=BEIJING_TZ)
        for mode, start in (("day", "2024-03-14T15:30:00+08:00"),
                            ("week", "2024-03-08T15:30:00+08:00"),
                            ("month", "2024-02-14T15:30:00+08:00")):
            self.assertEqual(periods.recent_bounds(mode, now=now),
                             (datetime.fromisoformat(start), now))
        with self.assertRaises(ValueError):
            periods.recent_bounds("year", now=now)

    def test_exact_cutoffs_preserve_fast_metadata_and_ignore_calendar_views(self):
        data = {"days": {"2026-10-12": {"m": {"input": 99999}}}, "instants": {
            "2026-10-11T15:29:59+08:00": {"m": {"input": 1000}},
            "2026-10-11T15:30:00+08:00": {"m": {"input": 10}},
            "2026-10-12T07:29:59+00:00": {"m": {"input": 20},
                "m [fast]": {"output": 5, "service_tiers": ["priority"],
                    "max_request_input": 200, "context_windows": [1000]}},
            "2026-10-12T15:30:00+08:00": {"m": {"input": 1000}},
        }}
        rows = periods.period_models(data, self.now.date(), "day", recent=True, now=self.now)
        self.assertEqual(rows["m"]["input"], 30)
        self.assertEqual(rows["m [fast]"]["service_tiers"], ["priority"])
        self.assertEqual(rows["m [fast]"]["max_request_input"], 200)
        self.assertEqual(rows["m [fast]"]["context_windows"], [1000])

    def test_all_chart_grains_clip_both_ends_and_equal_summary(self):
        for mode, days, bins in (("day", 1, 25), ("week", 7, 169), ("month", 30, 31)):
            start = self.now - timedelta(days=days)
            data = {"instants": {
                (start - timedelta(seconds=1)).isoformat(): {"m": {"input": 1000}},
                start.isoformat(): {"m": {"input": 10, "cached": 2}},
                (self.now - timedelta(seconds=1)).isoformat(): {"m": {"output": 20}},
                self.now.isoformat(): {"m": {"input": 1000}},
            }}
            points = periods.period_buckets(data, self.now.date(), mode, recent=True, now=self.now)
            self.assertEqual(len(points), bins)
            self.assertEqual(points[0]["at"], start)
            self.assertEqual(points[-1]["end"], self.now)
            self.assertTrue(points[-1]["current"])
            self.assertTrue(all(start <= p["at"] < p["end"] <= self.now for p in points))
            for key, want in (("input", 10), ("cached", 2), ("output", 20)):
                self.assertEqual(sum(p["models"].get("m", {}).get(key, 0) for p in points), want)
                self.assertEqual(periods.period_models(data, self.now.date(), mode,
                                                     recent=True, now=self.now)["m"][key], want)

    def test_output_filters_exact_instants_and_unions_files(self):
        data = {"output_instants": {
            "2026-10-11T15:29:59+08:00": {"added": 1000, "deleted": 0, "files": ["x"], "turns": 0},
            "2026-10-11T15:30:00+08:00": {"added": 10, "deleted": 2, "files": ["a"], "turns": 1},
            "2026-10-12T15:29:59+08:00": {"added": 20, "deleted": 3, "files": ["a", "b"], "turns": 2},
            "2026-10-12T15:30:00+08:00": {"added": 1000, "deleted": 0, "files": ["x"], "turns": 0},
        }}
        self.assertEqual(period_output(data, self.now.date(), "day", recent=True, now=self.now),
                         {"added": 30, "deleted": 5, "files": 2, "turns": 3, "recorded": True})

    def test_precise_reader_views_survive_dedup_cache_restart_and_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / "sessions" / "a.jsonl"
            path.parent.mkdir()
            records = [meta(cwd="D:/private-project"), context(service_tier="priority"),
                event(usage(20, 2, 5), at="2026-10-12T07:29:59.123456Z"),
                change(at="2026-10-11T07:29:59Z", **{"secret.py": {"type": "add", "content": "private patch\n"}}),
                change(at="2026-10-12T07:29:59Z", **{"secret.py": {"type": "add", "content": "duplicate\n"}}),
                complete(at="2026-10-12T07:29:59Z"),
                context("codex-auto-review", "excluded"),
                event(usage(120, 2, 5), usage(100), at="2026-10-12T07:29:59.5Z")]
            path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            shutil.copyfile(path, path.with_name("fork.jsonl"))
            reader = UsageReader(home, cache_path=home / "cache.sqlite3")
            first = reader.scan()
            self.assertIn("instants", first, "Exact usage views are required")
            self.assertIn("output_instants", first, "Exact output views are required")
            self.assertEqual(len(first["instants"]), 1)
            self.assertEqual(sum(row["requests"] for models in first["instants"].values()
                                 for row in models.values()), 1)
            self.assertTrue(any("123456" in key for key in first["instants"]))
            self.assertEqual(period_output(first, self.now.date(), "day", recent=True, now=self.now),
                             {"added": 0, "deleted": 0, "files": 0, "turns": 1, "recorded": True})
            serialized = json.dumps({key: first[key] for key in ("instants", "output_instants")})
            for private in ("secret.py", "private patch", "private-project", "thread-a", "turn-a", "edit-1"):
                self.assertNotIn(private, serialized)
            fresh = UsageReader(home, cache_path=home / "cache.sqlite3")
            cached = fresh.load_cached()
            for key in ("instants", "output_instants"):
                self.assertEqual(cached[key], first[key])
                self.assertEqual(fresh.scan()[key], first[key])


if __name__ == "__main__":
    unittest.main()
