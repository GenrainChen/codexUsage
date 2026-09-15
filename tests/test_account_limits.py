import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_account import fake_jwt, AUTH_CLAIM

try:
    from usage_note.account import read_account_identity
    from usage_note.account_limits import AccountLimitsError, fetch_account_limits, _find_codex
except ImportError:
    read_account_identity = fetch_account_limits = _find_codex = None
    AccountLimitsError = RuntimeError


FAKE_SERVER = r'''
import json, os, sys, time
from pathlib import Path
case = os.environ.get("NOTE_FAKE_CASE", "normal")
home = Path(os.environ["CODEX_HOME"])
account_reads = 0
for line in sys.stdin:
    request = json.loads(line)
    with (home / "requests.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(request) + "\n")
    method = request.get("method")
    if method == "initialized":
        continue
    if case == "timeout":
        time.sleep(60)
    if case == "slow":
        time.sleep(0.08)
    if case == "exit":
        sys.exit(0)
    if case == "error":
        print(json.dumps({"id": request["id"], "error": {"message": "fake-secret-from-server"}}), flush=True)
        continue
    if method == "initialize":
        print(json.dumps({"method": "account/rateLimits/updated", "params": {"private": "ignore-me"}}), flush=True)
        print(json.dumps({"id": 9000, "result": {"private": "ignore-me"}}), flush=True)
        result = {"userAgent": "fake-server"}
    elif method == "account/read":
        account_reads += 1
        email = "other@example.test" if case == "rpc_switch" and account_reads == 2 else "current@example.test"
        if case == "wrong_email":
            email = "other@example.test"
        result = {"account": {"type": "chatgpt", "email": email,
                              "planType": "prolite" if account_reads == 2 else "pro",
                              "secret": "fake-secret-from-server"}}
    elif method == "account/rateLimits/read":
        account_id = "different-id" if case == "wrong_id" else "private-account-id"
        result = {"accountId": account_id,
                  "rateLimits": {"limitId": "old-fallback", "primary": {"usedPercent": 90}},
                  "rateLimitsByLimitId": {
                      "codex": {"limitId": "codex", "limitName": "Codex", "planType": "pro",
                                "primary": None,
                                "secondary": {"usedPercent": 40, "windowDurationMins": 10080,
                                              "resetsAt": 1790000000, "secret": "fake-window-secret"},
                                "credits": {"balance": "fake-secret-from-server"}},
                      "codex_bengalfox": {"limitId": "codex_bengalfox",
                                "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 1789600000},
                                "secondary": {"usedPercent": 1, "windowDurationMins": 10080, "resetsAt": 1790100000}}
                  }, "refreshToken": "fake-secret-from-server"}
        if case == "missing_id":
            del result["accountId"]
        if case == "fallback":
            del result["rateLimitsByLimitId"]
        if case == "null_fields":
            result["rateLimitsByLimitId"]["codex"]["secondary"] = {
                "usedPercent": None, "windowDurationMins": True, "resetsAt": float("nan")}
        if case == "local_switch":
            auth = json.loads((home / "auth.json").read_text())
            auth["tokens"]["account_id"] = "second-private-account-id"
            (home / "auth.json").write_text(json.dumps(auth))
    else:
        sys.exit(7)
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
'''


class AccountLimitsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(fetch_account_limits, "Account quota reader is required")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.server = self.home / "fake_server.py"
        self.server.write_text(FAKE_SERVER, encoding="utf-8")
        self.write_auth()
        self.processes = []
        self.calls = []
        self.case = "normal"
        original_popen = subprocess.Popen

        def fake_popen(args, **kwargs):
            self.calls.append((args, kwargs.copy()))
            kwargs["env"] = dict(kwargs["env"], NOTE_FAKE_CASE=self.case)
            process = original_popen([sys.executable, "-u", str(self.server)], **kwargs)
            self.processes.append(process)
            return process

        self.addCleanup(patch.stopall)
        patch("usage_note.account_limits._find_codex", return_value=Path("fake-codex.exe")).start()
        patch("usage_note.account_limits.subprocess.Popen", side_effect=fake_popen).start()

    def write_auth(self, account_id="private-account-id"):
        auth = {"auth_mode": "chatgpt", "tokens": {
            "account_id": account_id,
            "id_token": fake_jwt({"email": "current@example.test", AUTH_CLAIM: {"chatgpt_plan_type": "pro"}}),
            "refresh_token": "fake-secret-local"}}
        (self.home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")

    def assert_cleaned_up(self):
        for process in self.processes:
            self.assertIsNotNone(process.poll())
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)
        self.assertFalse(any(thread.name.startswith("codex-note-quota-") for thread in threading.enumerate()))

    def test_only_reads_current_account_with_verified_id_and_normalized_quota_groups(self):
        snapshot = fetch_account_limits(self.home)
        self.assertEqual(snapshot["key"], hashlib.sha256(b"private-account-id").hexdigest())
        self.assertEqual(snapshot["email"], "current@example.test")
        self.assertEqual(snapshot["plan"], "prolite")
        self.assertEqual(set(snapshot), {"key", "email", "plan", "at", "limits"})
        self.assertEqual(set(snapshot["limits"]), {"codex", "codex_bengalfox"})
        self.assertEqual(snapshot["limits"]["codex"], {
            "at": snapshot["at"], "rate_limits": {"limit_id": "codex", "limit_name": "Codex",
                "primary": None, "secondary": {"used_percent": 40, "window_minutes": 10080,
                                               "resets_at": 1790000000}}})
        encoded = json.dumps(snapshot)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("private-account-id", encoded)
        requests = [json.loads(line) for line in (self.home / "requests.jsonl").read_text().splitlines()]
        self.assertEqual([request["method"] for request in requests], [
            "initialize", "initialized", "account/read", "account/rateLimits/read", "account/read"])
        self.assertEqual(requests[2]["params"], {"refreshToken": False})
        self.assertEqual(requests[3]["params"], {"excludeResetCreditDetails": True})
        self.assertNotIn("capabilities", requests[0]["params"])
        self.assertEqual(self.calls[0][0], ["fake-codex.exe", "app-server", "--stdio"])
        self.assertEqual(self.calls[0][1]["env"]["CODEX_HOME"], str(self.home))
        self.assertEqual(self.calls[0][1]["stderr"], subprocess.DEVNULL)
        if os.name == "nt":
            self.assertEqual(self.calls[0][1]["creationflags"] & subprocess.CREATE_NO_WINDOW, subprocess.CREATE_NO_WINDOW)
        self.assert_cleaned_up()

    def test_account_id_is_required_even_if_email_matches(self):
        for case in ("wrong_id", "missing_id"):
            self.case = case
            with self.subTest(case=case), self.assertRaises(AccountLimitsError):
                fetch_account_limits(self.home)
            self.assert_cleaned_up()

    def test_local_or_rpc_identity_changes_discard_the_read(self):
        for case in ("local_switch", "rpc_switch", "wrong_email"):
            self.write_auth()
            self.case = case
            with self.subTest(case=case), self.assertRaises(AccountLimitsError):
                fetch_account_limits(self.home)
            self.assert_cleaned_up()

    def test_legacy_single_group_fallback_keeps_its_own_id(self):
        self.case = "fallback"
        result = fetch_account_limits(self.home)
        self.assertEqual(set(result["limits"]), {"old-fallback"})
        self.assertEqual(result["limits"]["old-fallback"]["rate_limits"]["primary"],
                         {"used_percent": 90, "window_minutes": None, "resets_at": None})
        self.assert_cleaned_up()

    def test_missing_invalid_window_values_remain_unknown(self):
        self.case = "null_fields"
        result = fetch_account_limits(self.home)
        self.assertEqual(result["limits"]["codex"]["rate_limits"]["secondary"],
                         {"used_percent": None, "window_minutes": None, "resets_at": None})
        self.assert_cleaned_up()

    def test_server_errors_and_exit_are_safe_and_clean_up_processes(self):
        for case in ("error", "exit"):
            self.case = case
            with self.subTest(case=case), self.assertRaises(AccountLimitsError) as error:
                fetch_account_limits(self.home)
            self.assertNotIn("fake-secret", str(error.exception))
            self.assert_cleaned_up()

    def test_timeout_is_a_total_deadline_and_cleans_up_a_blocked_reader(self):
        for case in ("timeout", "slow"):
            self.case = case
            started = time.monotonic()
            with self.subTest(case=case), self.assertRaises(AccountLimitsError):
                fetch_account_limits(self.home, timeout=0.2)
            self.assertLess(time.monotonic() - started, 1.2)
            self.assert_cleaned_up()

    def test_api_key_and_missing_account_id_do_not_launch_a_process(self):
        for auth in ({"auth_mode": "apikey", "OPENAI_API_KEY": "fake-api-key"},
                     {"tokens": {"id_token": fake_jwt({"email": "current@example.test"})}}):
            (self.home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
            with self.assertRaises(AccountLimitsError):
                fetch_account_limits(self.home)
        self.assertFalse(self.calls)

    def test_each_read_launches_a_new_process_after_account_switch(self):
        fetch_account_limits(self.home)
        fetch_account_limits(self.home)
        self.assertEqual(len(self.processes), 2)
        self.assert_cleaned_up()

    def test_startup_failure_does_not_expose_process_error_details(self):
        with patch("usage_note.account_limits.subprocess.Popen", side_effect=OSError("fake-secret-path")):
            with self.assertRaises(AccountLimitsError) as error:
                fetch_account_limits(self.home)
        self.assertNotIn("fake-secret", str(error.exception))

    def test_reader_start_failure_closes_the_new_process_and_returns_safe_error(self):
        with patch("usage_note.account_limits.threading.Thread.start", side_effect=RuntimeError("fake-secret-path")):
            with self.assertRaises(AccountLimitsError) as error:
                fetch_account_limits(self.home)
        self.assertNotIn("fake-secret", str(error.exception))
        self.assert_cleaned_up()


class AccountIdentityTests(unittest.TestCase):
    def test_identity_returns_only_display_metadata_and_hash_of_selected_account(self):
        self.assertIsNotNone(read_account_identity, "Stable account identity reader is required")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            auth = {"tokens": {"account_id": "selected-account", "refresh_token": "fake-secret",
                    "id_token": fake_jwt({"email": "current@example.test",
                        AUTH_CLAIM: {"chatgpt_plan_type": "pro", "chatgpt_account_id": "old-claim-id"}})}}
            path = home / "auth.json"
            path.write_text(json.dumps(auth), encoding="utf-8")
            before = path.read_bytes()
            result = read_account_identity(home)
            self.assertEqual(result, {"status": "signed_in", "email": "current@example.test", "plan": "pro",
                                     "key": hashlib.sha256(b"selected-account").hexdigest()})
            self.assertEqual(path.read_bytes(), before)


class CodexDiscoveryTests(unittest.TestCase):
    def test_prefers_path_executable(self):
        self.assertIsNotNone(_find_codex, "Installed Codex discovery is required")
        with patch("usage_note.account_limits.shutil.which", return_value="C:/tools/codex.exe"):
            self.assertEqual(_find_codex(), Path("C:/tools/codex.exe"))

    def test_installed_fallback_uses_newest_modification_time(self):
        self.assertIsNotNone(_find_codex, "Installed Codex discovery is required")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "OpenAI" / "Codex" / "bin"
            files = [base / name / "codex.exe" for name in ("older", "newer")]
            for index, path in enumerate(files):
                path.parent.mkdir(parents=True)
                path.write_bytes(b"fake")
                os.utime(path, (100 + index, 100 + index))
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch("usage_note.account_limits.shutil.which", return_value=None):
                self.assertEqual(_find_codex(), files[1])


if __name__ == "__main__":
    unittest.main()
