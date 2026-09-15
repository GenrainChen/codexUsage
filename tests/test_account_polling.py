from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from usage_note.account_limits import AccountLimitsError
from usage_note.account_store import AccountStore
from usage_note.ui import UsageNote
from tests.test_account_store import snapshot


class AccountPollingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.note = UsageNote.__new__(UsageNote)
        self.note.reader = SimpleNamespace(codex_home=Path(temp.name))
        self.note.account_store = AccountStore(Path(temp.name) / "accounts.json")
        self.note._quota_error = None
        self.note._closed = False

    def test_each_refresh_updates_the_current_account_snapshot(self):
        current = {"key": "a" * 64, "email": "a@example.test", "status": "signed_in"}
        with patch("usage_note.account.read_account_identity", return_value=current), \
                patch("usage_note.account_limits.fetch_account_limits", return_value=snapshot("a", 2_000_000_000)) as fetch:
            result = self.note._read_account_data()
            self.assertEqual(result["account_pages"][0]["next_reset"], 2_000_000_000)
            fetch.return_value = snapshot("a", 2_100_000_000)
            result = self.note._read_account_data()
            self.assertEqual(result["account_pages"][0]["next_reset"], 2_100_000_000)
            current.update(key="b" * 64, email="b@example.test")
            fetch.return_value = snapshot("b", 1_900_000_000)
            result = self.note._read_account_data()
            self.assertEqual(result["account_pages"][0]["key"], "b" * 64)
            self.assertEqual(result["account_pages"][1]["next_reset"], 2_100_000_000)

    def test_failed_new_account_read_keeps_old_snapshot_separate(self):
        self.note.account_store.remember(snapshot("a", 2_000_000_000))
        current = {"key": "b" * 64, "email": "b@example.test", "status": "signed_in"}
        with patch("usage_note.account.read_account_identity", return_value=current), \
                patch("usage_note.account_limits.fetch_account_limits", side_effect=AccountLimitsError("读取超时")):
            result = self.note._read_account_data()
        self.assertEqual(result["account_quota_error"], "读取超时")
        self.assertEqual(result["account_pages"][0]["limits"], {})
        self.assertEqual(result["account_pages"][1]["key"], "a" * 64)
        self.assertTrue(result["account_pages"][1]["limits"])

    def test_login_change_during_fetch_discards_result_and_retries_next_poll(self):
        a = {"key": "a" * 64, "status": "signed_in"}
        b = {"key": "b" * 64, "status": "signed_in"}
        with patch("usage_note.account.read_account_identity", side_effect=[a, b]), \
                patch("usage_note.account_limits.fetch_account_limits", return_value=snapshot("b", 2_000_000_000)):
            result = self.note._read_account_data()
        self.assertEqual(result["account_pages"][0]["key"], "b" * 64)
        self.assertEqual(result["account_pages"][0]["limits"], {})
        self.assertIn("切换", result["account_quota_error"])

    def test_logged_out_mode_never_queries_with_saved_other_account(self):
        self.note.account_store.remember(snapshot("a", 2_000_000_000))
        with patch("usage_note.account.read_account_identity", return_value={"status": "unavailable"}), \
                patch("usage_note.account_limits.fetch_account_limits") as fetch:
            result = self.note._read_account_data()
        fetch.assert_not_called()
        self.assertEqual(result["account_pages"][0]["limits"], {})
        self.assertTrue(result["account_pages"][0]["current"])
        self.assertEqual(result["account_pages"][1]["key"], "a" * 64)

    def test_window_closed_during_query_does_not_write_old_instance_cache(self):
        current = {"key": "a" * 64, "status": "signed_in"}
        def fetch(_home):
            self.note._closed = True
            return snapshot("a", 2_000_000_000)
        with patch("usage_note.account.read_account_identity", return_value=current), \
                patch("usage_note.account_limits.fetch_account_limits", side_effect=fetch):
            result = self.note._read_account_data()
        self.assertFalse(self.note.account_store.path.exists())
        self.assertEqual(result["account_pages"][0]["limits"], {})
