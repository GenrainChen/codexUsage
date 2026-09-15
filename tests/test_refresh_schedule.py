"""Wall-clock refreshes must stay on five-minute boundaries without drift."""

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from usage_note.ui import UsageNote
from usage_note.timebase import BEIJING_TZ


class RefreshScheduleTests(unittest.TestCase):
    def setUp(self):
        self.instant = datetime(2026, 9, 14, 1, 43, 12, 123456, BEIJING_TZ)
        self.note = UsageNote.__new__(UsageNote)
        self.scheduled = []
        self.refreshes = []
        self.note._schedule = lambda delay, callback: self.scheduled.append((delay, callback))
        self.note.refresh = lambda: self.refreshes.append(self.instant)
        clock = patch("usage_note.ui.note_now", side_effect=lambda: self.instant)
        clock.start()
        self.addCleanup(clock.stop)

    def test_first_refresh_targets_next_boundary_including_midnight(self):
        cases = [
            ("2026-09-14T01:43:12.123456+08:00", "2026-09-14T01:45:00+08:00", 107877),
            ("2026-09-14T01:45:00+08:00", "2026-09-14T01:50:00+08:00", 300000),
            ("2026-09-14T01:49:59.999999+08:00", "2026-09-14T01:50:00+08:00", 1),
            ("2026-09-30T23:58:30+08:00", "2026-10-01T00:00:00+08:00", 90000),
        ]
        for now, expected, milliseconds in cases:
            with self.subTest(now=now):
                self.instant = datetime.fromisoformat(now)
                self.note._schedule_auto_refresh()
                self.assertEqual(self.note._next_refresh_at.isoformat(), expected)
                self.assertEqual(self.scheduled[-1][0], milliseconds)
        self.assertEqual(self.refreshes, [])

    def test_late_callback_does_not_shift_next_boundary(self):
        self.note._schedule_auto_refresh()
        self.instant = datetime(2026, 9, 14, 1, 45, 2, tzinfo=BEIJING_TZ)
        self.scheduled[-1][1]()
        self.assertEqual(self.refreshes, [self.instant])
        self.assertEqual(self.scheduled[-1][0], 298000)
        self.assertEqual(self.note._next_refresh_at.minute, 50)

    def test_waking_after_missed_boundaries_refreshes_once_then_realigns(self):
        self.note._schedule_auto_refresh()
        self.instant = datetime(2026, 9, 14, 2, 2, tzinfo=BEIJING_TZ)
        self.scheduled[-1][1]()
        self.assertEqual(self.refreshes, [self.instant])
        self.assertEqual(self.scheduled[-1][0], 180000)
        self.assertEqual(self.note._next_refresh_at.hour, 2)
        self.assertEqual(self.note._next_refresh_at.minute, 5)

    def test_early_callback_or_backward_clock_does_not_refresh_off_boundary(self):
        self.note._schedule_auto_refresh()
        self.instant -= timedelta(minutes=10)
        self.scheduled[-1][1]()
        self.assertEqual(self.refreshes, [])
        self.assertEqual(self.note._next_refresh_at.minute, 35)
        self.assertEqual(self.scheduled[-1][0], 107877)
