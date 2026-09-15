import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

try:
    from usage_note.account_store import AccountStore
except ImportError:
    AccountStore = None


def snapshot(key, reset, *, spark_reset=None):
    at = "2026-09-14T06:00:00+00:00"
    limits = {"codex": {"at": at, "rate_limits": {"limit_id": "codex",
        "primary": {"window_minutes": 10080, "used_percent": 30, "resets_at": reset}}}}
    if spark_reset is not None:
        limits["codex_bengalfox"] = {"at": at, "rate_limits": {"limit_id": "codex_bengalfox",
            "primary": {"window_minutes": 300, "used_percent": 10, "resets_at": spark_reset}}}
    return {"key": key * 64, "email": f"{key}@example.test", "plan": "pro", "at": at, "limits": limits}


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(AccountStore, "Account snapshot store is required")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "accounts.json"
        self.store = AccountStore(self.path)
        self.now = datetime(2026, 9, 14, 7, tzinfo=timezone.utc)

    def test_current_first_then_earliest_visible_reset_ignoring_spark(self):
        self.store.remember(snapshot("a", 2_000_000_000))
        self.store.remember(snapshot("b", 1_950_000_000, spark_reset=1_850_000_000))
        self.store.remember(snapshot("c", 1_900_000_000))
        pages = self.store.pages({"key": "a" * 64, "status": "signed_in"}, self.now)
        self.assertEqual([p["key"][0] for p in pages], ["a", "c", "b"])
        self.assertTrue(pages[0]["current"])
        self.assertFalse(pages[1]["current"])
        self.assertEqual(pages[2]["next_reset"], 1_950_000_000)

    def test_new_current_account_has_placeholder_without_borrowing_other_limits(self):
        self.store.remember(snapshot("a", 1_900_000_000))
        pages = self.store.pages({"key": "b" * 64, "status": "signed_in", "email": "b@example.test"}, self.now)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0]["limits"], {})
        self.assertIsNone(pages[0]["at"])
        self.assertEqual(pages[1]["key"], "a" * 64)

    def test_restart_retains_independent_accounts_and_never_persists_secrets(self):
        one = snapshot("a", 1_900_000_000, spark_reset=1_850_000_000)
        one.update(access_token="fake-secret", account_id="raw-id")
        one["limits"]["codex"]["rate_limits"]["secret"] = "nested-secret"
        self.store.remember(one)
        self.store.remember(snapshot("b", 1_910_000_000))
        reloaded = AccountStore(self.path)
        pages = reloaded.pages({"key": "b" * 64, "status": "signed_in"}, self.now)
        self.assertEqual([p["key"][0] for p in pages], ["b", "a"])
        self.assertEqual(len(pages[1]["limits"]), 2)
        raw = self.path.read_text(encoding="utf-8")
        for value in ("fake-secret", "raw-id", "nested-secret", "access_token"):
            self.assertNotIn(value, raw)

    def test_past_reset_kept_for_ordering_unknown_last_and_percentage_not_reset(self):
        self.store.remember(snapshot("a", None))
        self.store.remember(snapshot("b", 1_700_000_000))
        self.store.remember(snapshot("c", 1_900_000_000))
        pages = self.store.pages({"status": "unavailable"}, self.now)
        self.assertEqual([p["key"][0] for p in pages[1:]], ["b", "c", "a"])
        self.assertTrue(pages[1]["reset_due"])
        self.assertEqual(pages[1]["limits"]["codex"]["rate_limits"]["primary"]["used_percent"], 30)

    def test_corrupt_cache_warns_and_invalid_identity_cannot_be_saved(self):
        self.path.write_text("{broken", encoding="utf-8")
        store = AccountStore(self.path)
        self.assertTrue(store.warnings)
        bad = snapshot("a", 1_900_000_000)
        bad["key"] = "unverified"
        with self.assertRaises(ValueError):
            store.remember(bad)

    def test_refresh_replaces_only_that_account_and_does_not_alias_returned_pages(self):
        self.store.remember(snapshot("a", 1_900_000_000))
        self.store.remember(snapshot("b", 1_800_000_000))
        self.store.remember(snapshot("a", 1_950_000_000))
        pages = self.store.pages({"key": "a" * 64, "status": "signed_in"}, self.now)
        pages[1]["limits"].clear()
        fresh = self.store.pages({"key": "a" * 64, "status": "signed_in"}, self.now)
        self.assertEqual(fresh[0]["next_reset"], 1_950_000_000)
        self.assertTrue(fresh[1]["limits"])

    def test_verified_plan_is_not_replaced_by_stale_local_login_metadata(self):
        self.store.remember(snapshot("a", 1_900_000_000))
        pages = self.store.pages({"key": "a" * 64, "status": "signed_in", "plan": "plus"}, self.now)
        self.assertEqual(pages[0]["plan"], "pro")
        missing = self.store.pages({"key": "b" * 64, "status": "signed_in", "plan": "plus"}, self.now)
        self.assertEqual(missing[0]["plan"], "plus")
