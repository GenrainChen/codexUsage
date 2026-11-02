"""CLI cache controls must stay within the selected local workspace."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO

import app
from tests.test_reader import context, event, meta, usage


class CacheCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "codex"
        self.home.mkdir()
        (self.root / "prices.json").write_text('{"models": {}}', encoding="utf-8")
        self.report = {"days": {}, "hours": {}, "output_days": {}, "warnings": []}

    def _run(self, *options):
        created = []

        class Reader:
            tz = "UTC+08:00"

            def __init__(_, home, **kwargs):
                created.append((home, kwargs))

            def scan(_, **kwargs):
                created.append(kwargs)
                return self.report.copy()

        output = StringIO()
        arguments = ["app.py", "--codex-home", str(self.home), "--report", *options]
        with patch.object(app, "ROOT", self.root), patch.object(sys, "argv", arguments), \
                patch("usage_note.reader.UsageReader", Reader), patch("sys.stdout", output):
            result = app.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["days"], {})
        return created

    def test_report_uses_local_persistent_cache_by_default(self):
        created = self._run()
        self.assertEqual(created[0], (self.home, {"cache_path": self.root / ".local" / "usage-cache.sqlite3"}))
        self.assertEqual(created[1], {})

    def test_rebuild_cache_forces_full_scan(self):
        created = self._run("--rebuild-cache")
        self.assertEqual(created[1], {"rebuild": True})

    def test_no_cache_disables_persistent_cache(self):
        created = self._run("--no-cache")
        self.assertEqual(created[0][1], {"cache_path": None})

    def test_rebuild_and_no_cache_are_mutually_exclusive(self):
        with patch.object(sys, "argv", ["app.py", "--rebuild-cache", "--no-cache"]), \
                patch("sys.stderr", StringIO()):
            with self.assertRaises(SystemExit) as caught:
                app.main()
        self.assertEqual(caught.exception.code, 2)

    def test_real_reader_cli_reuses_cache_and_no_cache_matches_totals(self):
        source = self.home / "sessions" / "synthetic.jsonl"
        source.parent.mkdir()
        rows = [meta(), context(), event(usage(100, 60, 10))]
        source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

        def run(*options):
            output = StringIO()
            arguments = ["app.py", "--codex-home", str(self.home), "--report", *options]
            with patch.object(app, "ROOT", self.root), patch.object(sys, "argv", arguments), \
                    patch("sys.stdout", output):
                self.assertEqual(app.main(), 0)
            return json.loads(output.getvalue())

        first = run()
        second = run()
        uncached = run("--no-cache")
        filtered = run("--no-cache", "--date", "2026-09-12")
        expected = {"input": 40, "cached": 60, "output": 10}
        for report in (first, second, uncached, filtered):
            tokens = report["days"]["2026-09-12"]["model-a"]
            self.assertEqual({key: tokens[key] for key in expected}, expected)
            self.assertNotIn("instants", report, "CLI preserves its existing report shape")
            self.assertNotIn("output_instants", report, "CLI does not export precise internal views")
        self.assertTrue((self.root / ".local" / "usage-cache.sqlite3").is_file())
        self.assertEqual(first["cache_stats"]["reread_files"], 1)
        self.assertEqual(second["cache_stats"]["reused_files"], 1)
        self.assertEqual(uncached["cache_path"], None)
        self.assertEqual(uncached["days"], second["days"])

    def test_ultrafast_cli_report_prices_separate_row_and_reuses_raw_tier_cache(self):
        source = self.home / "sessions" / "ultrafast.jsonl"
        source.parent.mkdir()
        rows = [meta(), context("known", service_tier="ultrafast"), event(usage(100, 60, 10))]
        source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        (self.root / "prices.json").write_text(json.dumps({"models": {
            "known": {"input": 2, "cached": .2, "output": 8},
        }}), encoding="utf-8")
        for options in ([], [], ["--no-cache"]):
            output = StringIO()
            arguments = ["app.py", "--codex-home", str(self.home), "--report", *options]
            with patch.object(app, "ROOT", self.root), patch.object(sys, "argv", arguments), \
                    patch("sys.stdout", output):
                self.assertEqual(app.main(), 0)
            report = json.loads(output.getvalue())
            models = report["days"]["2026-09-12"]
            self.assertEqual(set(models), {"known [ULTRAFAST]"})
            self.assertEqual(models["known [ULTRAFAST]"]["quote"]["pricing_multiplier"], 8)
            self.assertAlmostEqual(models["known [ULTRAFAST]"]["quote"]["total_usd"], .001376)
            self.assertEqual(models["known [ULTRAFAST]"]["input"], 40)


if __name__ == "__main__":
    unittest.main()
