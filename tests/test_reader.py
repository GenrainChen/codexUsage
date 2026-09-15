import json
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
import os
import shutil
import struct
import tempfile
import unittest
from zoneinfo import ZoneInfo

try:
    from usage_note.reader import UsageReader
except ImportError:
    UsageReader = None


def usage(input=0, cached=0, output=0, reasoning=0, cache_write=0):
    return {
        "input_tokens": input,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input + output,
    }


def meta(id="thread-a", **extra):
    return {"timestamp": "2026-09-12T10:00:00Z", "type": "session_meta",
            "payload": {"id": id, "session_id": id, **extra}}


def context(model="model-a", turn="turn-a", **extra):
    return {"timestamp": "2026-09-12T10:00:00Z", "type": "turn_context",
            "payload": {"model": model, "turn_id": turn, **extra}}


def event(total, last=None, at="2026-09-12T12:00:00Z", rate=None):
    return {"timestamp": at, "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": total,
            "last_token_usage": total if last is None else last,
            "model_context_window": 258400}, "rate_limits": rate}}


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(UsageReader, "UsageReader must implement local token aggregation")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.reader = UsageReader(self.home, tz=timezone.utc)

    def write(self, records, name="a.jsonl", folder="sessions"):
        path = self.home / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        return path

    def row(self, data, day="2026-09-12", model="model-a"):
        return data["days"][day][model]

    def test_cumulative_snapshots_do_not_double_count_cache_or_reasoning(self):
        first = usage(100, 60, 10, 4)
        second = usage(180, 100, 25, 9)
        self.write([meta(), context(), event(first),
                    event(first, at="2026-09-12T12:00:01Z"),
                    event(second, usage(80, 40, 15, 5), at="2026-09-12T12:00:02Z")])
        row = self.row(self.reader.scan())
        self.assertEqual((row["input"], row["cached"], row["output"], row["requests"]),
                         (80, 100, 25, 2))
        self.assertEqual(row["reasoning"], 9)

    def test_model_switch_uses_delta_in_the_new_model(self):
        self.write([meta(), context(), event(usage(100, 60, 10)),
                    context("model-b", "turn-b"),
                    event(usage(180, 100, 25), usage(80, 40, 15), at="2026-09-12T12:02:00Z")])
        data = self.reader.scan()
        self.assertEqual(self.row(data)["input"], 40)
        self.assertEqual(self.row(data, model="model-b")["input"], 40)
        self.assertEqual(self.row(data, model="model-b")["output"], 15)

    def test_auto_review_is_excluded_without_charging_it_to_the_next_model(self):
        self.write([meta(), context(), event(usage(100, 60, 10)),
                    context("codex-auto-review", "review-turn"),
                    event(usage(150, 80, 15), usage(50, 20, 5), at="2026-09-12T12:01:00Z"),
                    context("model-a", "normal-turn"),
                    event(usage(230, 120, 30), usage(80, 40, 15), at="2026-09-12T12:02:00Z")])
        for data in (self.reader.scan(), self.reader.scan()):
            self.assertEqual(set(data["days"]["2026-09-12"]), {"model-a"})
            row = self.row(data)
            self.assertEqual((row["input"], row["cached"], row["output"], row["requests"]),
                             (80, 100, 25, 2))

    def test_auto_review_only_days_in_archives_are_not_usage_days(self):
        self.write([meta(), context("codex-auto-review"), event(usage(100, 60, 10))],
                   folder="archived_sessions")
        data = self.reader.scan()
        self.assertEqual(data["days"], {})
        self.assertEqual(data["hours"], {})

    def test_hourly_rows_preserve_counts_subsets_and_metadata(self):
        self.write([meta(), context(service_tier="priority", effort="high"),
                    event(usage(100, 60, 10, 4, 20), at="2026-09-12T12:05:23Z"),
                    context(service_tier="default", effort="medium"),
                    event(usage(180, 100, 25, 9, 30), usage(80, 40, 15, 5, 10),
                          at="2026-09-12T12:59:59Z"),
                    context("model-b", "turn-b"),
                    event(usage(230, 110, 30, 11, 35), usage(50, 10, 5, 2, 5),
                          at="2026-09-12T13:00:00Z")])
        data = self.reader.scan()
        self.assertEqual(list(data["hours"]), ["2026-09-12T12:00:00+00:00",
                                               "2026-09-12T13:00:00+00:00"])
        row = data["hours"]["2026-09-12T12:00:00+00:00"]["model-a"]
        self.assertEqual(row, self.row(data))
        self.assertEqual((row["input"], row["cached"], row["output"],
                          row["cache_write"], row["reasoning"], row["requests"]),
                         (80, 100, 25, 30, 9, 2))
        self.assertEqual(row["max_request_input"], 100)
        self.assertEqual(row["service_tiers"], ["default", "priority"])
        self.assertEqual(row["reasoning_efforts"], ["high", "medium"])
        self.assertEqual(row["context_windows"], [258400])
        for key in ("input", "cached", "output", "cache_write", "reasoning", "requests"):
            with self.subTest(field=key):
                daily = sum(row[key] for models in data["days"].values() for row in models.values())
                hourly = sum(row[key] for models in data["hours"].values() for row in models.values())
                self.assertEqual(daily, hourly)

    def test_local_hour_boundary_handles_half_hour_offsets_and_midnight(self):
        self.reader = UsageReader(self.home, tz=timezone(timedelta(hours=5, minutes=30)))
        self.write([meta(), context(), event(usage(100, 0, 10), at="2026-09-12T18:25:00Z"),
                    event(usage(180, 0, 25), usage(80, 0, 15), at="2026-09-12T18:35:00Z")])
        data = self.reader.scan()
        self.assertEqual(list(data["hours"]), ["2026-09-12T23:00:00+05:30",
                                               "2026-09-13T00:00:00+05:30"])
        self.assertEqual(data["hours"]["2026-09-12T23:00:00+05:30"]["model-a"],
                         self.row(data, day="2026-09-12"))
        self.assertEqual(data["hours"]["2026-09-13T00:00:00+05:30"]["model-a"],
                         self.row(data, day="2026-09-13"))

    def test_hourly_dedup_and_exclusion_apply_before_aggregation_and_on_cached_scans(self):
        records = [meta(), context(), event(usage(100, 60, 10)),
                   context("codex-auto-review", "review-turn"),
                   event(usage(150, 80, 15), usage(50, 20, 5), at="2026-09-12T13:15:00Z"),
                   context("model-a", "normal-turn"),
                   event(usage(230, 120, 30), usage(80, 40, 15), at="2026-09-12T14:05:00Z")]
        self.write(records)
        self.write(records, "copy.jsonl", folder="archived_sessions")
        first = self.reader.scan()
        cached = self.reader.scan()
        self.assertEqual(cached["bytes_read"], 0)
        self.assertEqual(cached["hours"], first["hours"])
        for data in (first, cached):
            self.assertEqual(list(data["hours"]), ["2026-09-12T12:00:00+00:00",
                                                   "2026-09-12T14:00:00+00:00"])
            self.assertEqual(data["duplicate_events"], 3)
            self.assertEqual(data["hours"]["2026-09-12T14:00:00+00:00"]["model-a"]["output"], 15)
            self.assertEqual(sum(row["input"] + row["cached"] + row["output"]
                                 for models in data["hours"].values() for row in models.values()), 205)
            self.assertTrue(all("codex-auto-review" not in models for models in data["hours"].values()))

    def test_daylight_saving_repeated_hour_retains_each_utc_offset(self):
        # A minimal TZif fixture avoids relying on a global tzdata install on Windows.
        transitions = [int(datetime(2026, 3, 8, 7, tzinfo=timezone.utc).timestamp()),
                       int(datetime(2026, 11, 1, 6, tzinfo=timezone.utc).timestamp())]
        tzif = (b"TZif\0" + b"\0" * 15 + struct.pack(">6l", 0, 0, 0, 2, 2, 8)
                + struct.pack(">2l", *transitions) + b"\x00\x01"
                + struct.pack(">lbb", -4 * 3600, 1, 0)
                + struct.pack(">lbb", -5 * 3600, 0, 4) + b"EDT\0EST\0")
        self.reader = UsageReader(self.home, tz=ZoneInfo.from_file(BytesIO(tzif)))
        self.write([meta(), context(), event(usage(100, 0, 10), at="2026-11-01T05:15:00Z"),
                    event(usage(180, 0, 25), usage(80, 0, 15), at="2026-11-01T06:15:00Z")])
        data = self.reader.scan()
        self.assertEqual(list(data["hours"]), ["2026-11-01T01:00:00-04:00",
                                               "2026-11-01T01:00:00-05:00"])
        self.assertEqual(data["hours"]["2026-11-01T01:00:00-04:00"]["model-a"]["input"], 100)
        self.assertEqual(data["hours"]["2026-11-01T01:00:00-05:00"]["model-a"]["input"], 80)
        self.assertEqual(self.row(data, day="2026-11-01")["input"], 180)

    def test_local_day_comes_from_event_timestamp_not_file_or_turn_date(self):
        self.reader = UsageReader(self.home, tz=timezone(timedelta(hours=-5)))
        self.write([meta(), context(), event(usage(100, 0, 10), at="2026-09-13T04:59:00Z"),
                    event(usage(180, 0, 25), usage(80, 0, 15), at="2026-09-13T05:01:00Z")])
        data = self.reader.scan()
        self.assertEqual(self.row(data)["input"], 100)
        self.assertEqual(self.row(data, day="2026-09-13")["input"], 80)

    def test_counter_reset_can_have_a_larger_total(self):
        self.write([meta(), context(), event(usage(100, 60, 10)),
                    context(turn="turn-b"), event(usage(140, 90, 5), at="2026-09-12T12:01:00Z"),
                    event(usage(170, 100, 12), usage(30, 10, 7), at="2026-09-12T12:02:00Z")])
        row = self.row(self.reader.scan())
        self.assertEqual((row["input"], row["cached"], row["output"]), (110, 160, 22))

    def test_zero_reset_with_stale_last_does_not_recharge_last_request(self):
        self.write([meta(), context(), event(usage(100, 60, 10)),
                    event(usage(), usage(100, 60, 10), at="2026-09-12T12:01:00Z"),
                    event(usage(80, 40, 5), at="2026-09-12T12:02:00Z")])
        row = self.row(self.reader.scan())
        self.assertEqual((row["input"], row["output"], row["requests"]), (80, 15, 2))

    def test_first_event_uses_last_when_total_contains_inherited_history(self):
        self.write([meta(history_base={"thread_id": "parent", "end_ordinal_exclusive": 10}),
                    context(), event(usage(1000, 700, 100), usage(100, 40, 10))])
        data = self.reader.scan()
        self.assertEqual((self.row(data)["input"], self.row(data)["output"]), (60, 10))
        self.assertTrue(data["warnings"])

    def test_cumulative_delta_recovers_missing_intermediate_snapshot(self):
        self.write([meta(), context(), event(usage(100, 60, 10)),
                    event(usage(280, 160, 35), usage(80, 40, 15), at="2026-09-12T12:02:00Z")])
        data = self.reader.scan()
        self.assertEqual((self.row(data)["input"], self.row(data)["output"]), (120, 35))
        self.assertTrue(data["warnings"])

    def test_copied_fork_history_is_deduplicated_but_independent_equal_usage_counts(self):
        history = [meta(), context(), event(usage(100, 60, 10))]
        self.write(history)
        self.write(history + [context(turn="fork-new-turn"),
                            event(usage(150, 80, 15), usage(50, 20, 5), at="2026-09-12T12:01:00Z")], "fork.jsonl")
        self.write([meta("independent"), context(turn="independent-turn"), event(usage(100, 60, 10))], "other.jsonl")
        row = self.row(self.reader.scan())
        self.assertEqual((row["input"], row["cached"], row["output"], row["requests"]), (110, 140, 25, 3))

    def test_fork_path_order_does_not_discard_recovered_cumulative_usage(self):
        first = event(usage(100, 0, 10))
        shared = event(usage(300, 0, 30), usage(100, 0, 10), at="2026-09-12T12:02:00Z")
        for fork_name in ("a-fork.jsonl", "zz-fork.jsonl"):
            with self.subTest(fork_name=fork_name), tempfile.TemporaryDirectory() as root:
                self.home = Path(root)
                reader = UsageReader(self.home, tz=timezone.utc)
                self.write([meta(), context(), first, shared], "z-original.jsonl")
                self.write([meta(history_base={"thread_id": "parent"}), context(), shared], fork_name)
                data = reader.scan()
                row = self.row(data)
                self.assertEqual((row["input"], row["output"], row["requests"]), (300, 30, 2))
                self.assertEqual(data["duplicate_events"], 1)
                self.assertEqual(self.row(reader.scan())["input"], 300)

    def test_dedup_uses_the_shortest_observed_counter_interval_without_double_counting(self):
        first = event(usage(100, 0, 10))
        middle = event(usage(200, 0, 20), usage(100, 0, 10), at="2026-09-12T12:01:00Z")
        shared = event(usage(300, 0, 30), usage(100, 0, 10), at="2026-09-12T12:02:00Z")
        self.write([meta(), context(), first, shared], "a-sparse-copy.jsonl")
        self.write([meta(), context(), first, middle, shared], "z-complete-original.jsonl")
        row = self.row(self.reader.scan())
        self.assertEqual((row["input"], row["output"], row["requests"]), (300, 30, 3))

    def test_partial_fork_history_keeps_uncovered_portions_of_a_long_counter_interval(self):
        first = event(usage(100, 0, 10))
        middle = event(usage(300, 0, 30), usage(100, 0, 10), at="2026-09-12T12:02:00Z")
        shared = event(usage(400, 0, 40), usage(100, 0, 10), at="2026-09-12T12:03:00Z")
        self.write([meta(), context(), first, shared], "original.jsonl")
        self.write([meta(history_base={"thread_id": "parent"}), context(), middle, shared], "fork.jsonl")
        row = self.row(self.reader.scan())
        self.assertEqual((row["input"], row["output"], row["requests"]), (400, 40, 3))

    def test_last_only_usage_is_not_counted_again_when_cumulative_reporting_resumes(self):
        self.write([meta(), context(), event(usage(100, 0, 10)),
                    event(None, usage(100, 0, 10), at="2026-09-12T12:01:00Z"),
                    event(usage(300, 0, 30), usage(100, 0, 10), at="2026-09-12T12:02:00Z")])
        row = self.row(self.reader.scan())
        self.assertEqual((row["input"], row["output"], row["requests"]), (300, 30, 3))

    def test_archive_move_and_repeat_scan_do_not_duplicate_usage(self):
        path = self.write([meta(), context(), event(usage(100, 60, 10))])
        self.reader.scan()
        cached = self.reader.scan()
        self.assertEqual(cached["bytes_read"], 0)
        archive = self.home / "archived_sessions"
        archive.mkdir()
        shutil.move(str(path), str(archive / path.name))
        self.assertEqual(self.row(self.reader.scan())["input"], 40)

    def test_partial_tail_is_retried_and_append_reads_only_new_bytes(self):
        path = self.write([meta(), context(), event(usage(100, 60, 10))])
        self.reader.scan()
        new_line = (json.dumps(event(usage(180, 100, 25), usage(80, 40, 15),
                          at="2026-09-12T12:01:00Z")) + "\n").encode()
        with path.open("ab") as stream:
            stream.write(new_line[:60])
        self.assertEqual(self.row(self.reader.scan())["input"], 40)
        with path.open("ab") as stream:
            stream.write(new_line[60:])
        data = self.reader.scan()
        self.assertEqual(self.row(data)["input"], 80)
        self.assertLess(data["bytes_read"], path.stat().st_size)

    def test_replaced_file_discards_old_cached_contributions(self):
        self.write([meta(), context(), event(usage(100, 60, 10))])
        self.reader.scan()
        self.write([meta(), context(), event(usage(20, 0, 2))])
        self.assertEqual(self.row(self.reader.scan())["input"], 20)

    def test_same_size_in_place_rewrite_is_reread_when_footer_is_unchanged(self):
        footer = {"timestamp": "2026-09-12T12:03:00Z", "type": "event_msg", "payload": {
            "type": "task_complete", "turn_id": "unchanged-task-completion-footer-longer-than-64-bytes"}}
        path = self.write([meta(), context(), event(usage(100, 60, 10)), footer])
        self.reader.scan()
        before = path.stat()
        self.write([meta(), context(), event(usage(200, 60, 10)), footer])
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        self.assertEqual(path.stat().st_size, before.st_size)
        data = self.reader.scan()
        self.assertEqual(self.row(data)["input"], 140)
        self.assertEqual(data["days"], UsageReader(self.home, tz=timezone.utc).scan()["days"])

    def test_latest_rate_only_event_preserves_window_duration_and_timestamp(self):
        rate = {"limit_id": "codex", "primary": {"used_percent": 12,
                "window_minutes": 10080, "resets_at": 1789816690}, "secondary": None}
        self.write([meta(), context(), event(usage(100, 60, 10)),
                    {"timestamp": "2026-09-12T12:01:00Z", "type": "event_msg", "payload": {
                        "type": "token_count", "info": None, "rate_limits": rate}}])
        data = self.reader.scan()
        self.assertEqual(data["latest_rate_limits"], rate)
        self.assertEqual(data["latest_rate_limits_at"], "2026-09-12T12:01:00+00:00")
        self.assertEqual(self.row(data)["requests"], 1)

    def test_rate_ids_in_one_file_keep_independent_latest_snapshots(self):
        general = {"limit_id": "codex", "primary": {"used_percent": 20, "window_minutes": 10080}}
        spark = {"limit_id": "codex_bengalfox", "limit_name": "GPT-5.3-Codex-Spark",
                 "primary": {"used_percent": 4, "window_minutes": 300},
                 "secondary": {"used_percent": 1, "window_minutes": 10080}}
        newer_general = {"limit_id": "codex", "primary": {"used_percent": 26, "window_minutes": 10080}}
        path = self.write([meta(), context(), event(usage(100, 60, 10), rate=general),
                           event(None, at="2026-09-12T12:01:00Z", rate=spark)])
        first = self.reader.scan()
        self.assertEqual(first["latest_rate_limits_by_id"]["codex"]["rate_limits"], general)
        self.assertEqual(first["latest_rate_limits_by_id"]["codex_bengalfox"]["rate_limits"], spark)
        # Rate-only refreshes have no usage info and must still update one group.
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": "2026-09-12T12:02:00Z", "type": "event_msg",
                                    "payload": {"type": "token_count", "info": None,
                                                "rate_limits": newer_general}}) + "\n")
        updated = self.reader.scan()
        self.assertEqual(updated["latest_rate_limits_by_id"]["codex"], {
            "at": "2026-09-12T12:02:00+00:00", "rate_limits": newer_general})
        self.assertEqual(updated["latest_rate_limits_by_id"]["codex_bengalfox"],
                         first["latest_rate_limits_by_id"]["codex_bengalfox"])
        self.assertEqual(updated["latest_rate_limits"], newer_general)
        self.assertEqual(updated["days"], first["days"])
        self.assertEqual(updated["hours"], first["hours"])
        self.assertLess(updated["bytes_read"], path.stat().st_size)
        cached = self.reader.scan()
        self.assertEqual(cached["latest_rate_limits_by_id"], updated["latest_rate_limits_by_id"])
        self.assertEqual(cached["bytes_read"], 0)

    def test_rate_ids_across_files_choose_event_time_not_file_order(self):
        newest = {"limit_id": "codex", "primary": {"used_percent": 30, "window_minutes": 10080}}
        older = {"limit_id": "codex", "primary": {"used_percent": 10, "window_minutes": 10080}}
        spark = {"limit_id": "codex_bengalfox", "primary": {"used_percent": 8, "window_minutes": 300}}
        self.write([event(None, at="2026-09-12T12:05:00Z", rate=newest)], "a-newest.jsonl")
        self.write([event(None, at="2026-09-12T12:04:00Z", rate=older),
                    event(None, at="2026-09-12T12:06:00Z", rate=spark)], "z-older.jsonl",
                   folder="archived_sessions")
        data = self.reader.scan()
        self.assertEqual(set(data["latest_rate_limits_by_id"]), {"codex", "codex_bengalfox"})
        self.assertEqual(data["latest_rate_limits_by_id"]["codex"]["rate_limits"], newest)
        self.assertEqual(data["latest_rate_limits_by_id"]["codex_bengalfox"]["rate_limits"], spark)
        self.assertEqual(data["latest_rate_limits"], spark)
        self.assertEqual(data["days"], {})
        self.assertEqual(data["hours"], {})

    def test_rate_id_missing_or_unknown_is_not_inferred_from_model(self):
        missing = {"primary": {"used_percent": None, "window_minutes": 300}}
        unknown = {"limit_id": "future-quota", "limit_name": "Future", "primary": None}
        self.write([context("gpt-5.3-codex-spark"), event(None, rate=missing),
                    context("gpt-6-astra"),
                    event(None, at="2026-09-12T12:01:00Z", rate=unknown)])
        data = self.reader.scan()
        self.assertEqual(set(data["latest_rate_limits_by_id"]), {"unclassified", "future-quota"})
        self.assertEqual(data["latest_rate_limits_by_id"]["unclassified"]["rate_limits"], missing)
        self.assertEqual(data["latest_rate_limits_by_id"]["future-quota"]["rate_limits"], unknown)

    def test_cache_write_is_an_input_subset_and_tier_metadata_is_preserved(self):
        self.write([meta(), context(service_tier="priority", effort="high"),
                    event(usage(300000, 180000, 50, reasoning=20, cache_write=50000))])
        row = self.row(self.reader.scan())
        self.assertEqual((row["input"], row["cached"], row["cache_write"], row["output"]),
                         (120000, 180000, 50000, 50))
        self.assertEqual(row["max_request_input"], 300000)
        self.assertEqual(row["service_tiers"], ["priority"])
        self.assertEqual(row["reasoning_efforts"], ["high"])

    def test_malformed_metadata_is_reported_without_exposing_conversation_text(self):
        path = self.write([meta(), context(), event(usage(100, 60, 10))])
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"type":"event_msg","payload":{"type":"token_count" BROKEN PRIVATE TEXT}\n')
            stream.write(json.dumps({"type": "response_item", "payload": {"private": "secret"}}) + "\n")
        data = self.reader.scan()
        self.assertEqual(self.row(data)["input"], 40)
        self.assertTrue(data["warnings"])
        self.assertNotIn("PRIVATE", str(data))
        self.assertNotIn("secret", str(data))


if __name__ == "__main__":
    unittest.main()
