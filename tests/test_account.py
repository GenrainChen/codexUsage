import base64
import json
from pathlib import Path
import tempfile
import unittest

try:
    from usage_note.account import read_current_account
except ImportError:
    read_current_account = None


AUTH_CLAIM = "https://api.openai.com/auth"
PROFILE_CLAIM = "https://api.openai.com/profile"


def fake_jwt(claims):
    """Unsigned synthetic data only; never use account credentials in tests."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.fake-signature"


class CurrentAccountTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(read_current_account, "Current account metadata reader is required")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.codex_home = Path(self.directory.name)
        self.auth_path = self.codex_home / "auth.json"

    def write_auth(self, value):
        self.auth_path.write_text(json.dumps(value), encoding="utf-8")

    def test_reads_only_email_and_plan_from_id_token_without_modifying_login_file(self):
        self.write_auth({"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {
            "id_token": fake_jwt({"email": "current@example.test", "sub": "private-subject",
                                  AUTH_CLAIM: {"chatgpt_plan_type": "pro",
                                               "chatgpt_account_id": "private-account-id"}}),
            "access_token": fake_jwt({PROFILE_CLAIM: {"email": "fallback@example.test"},
                                      AUTH_CLAIM: {"chatgpt_plan_type": "plus"}}),
            "refresh_token": "fake-refresh-secret", "account_id": "private-account-id"},
            "last_refresh": "2026-09-14T00:00:00Z"})
        original = self.auth_path.read_bytes()

        self.assertEqual(read_current_account(self.codex_home),
                         {"status": "signed_in", "email": "current@example.test", "plan": "pro"})
        self.assertEqual(self.auth_path.read_bytes(), original)

    def test_access_token_profile_is_fallback_when_id_token_is_broken(self):
        self.write_auth({"auth_mode": "chatgpt", "tokens": {
            "id_token": "bad.jwt", "access_token": fake_jwt({
                PROFILE_CLAIM: {"email": "fallback@example.test"},
                AUTH_CLAIM: {"chatgpt_plan_type": "team"}})}})

        self.assertEqual(read_current_account(self.codex_home),
                         {"status": "signed_in", "email": "fallback@example.test", "plan": "team"})

    def test_missing_fields_use_access_claims_without_replacing_id_email(self):
        self.write_auth({"tokens": {
            "id_token": fake_jwt({"email": "primary@example.test", AUTH_CLAIM: []}),
            "access_token": fake_jwt({PROFILE_CLAIM: {"email": "secondary@example.test"},
                                      AUTH_CLAIM: {"chatgpt_plan_type": "future-plan"}})}})

        self.assertEqual(read_current_account(self.codex_home),
                         {"status": "signed_in", "email": "primary@example.test", "plan": "future-plan"})

    def test_switch_and_logout_reread_file_without_retaining_previous_account(self):
        for email in ("first@example.test", "second@example.test"):
            with self.subTest(email=email):
                self.write_auth({"tokens": {"id_token": fake_jwt({"email": email})}})
                self.assertEqual(read_current_account(self.codex_home),
                                 {"status": "signed_in", "email": email})
        self.auth_path.unlink()
        self.assertEqual(read_current_account(self.codex_home), {"status": "unavailable"})

    def test_api_key_login_does_not_display_stale_chatgpt_account(self):
        stale_tokens = {"id_token": fake_jwt({"email": "stale@example.test"})}
        for value in ({"auth_mode": "apikey", "OPENAI_API_KEY": "fake-api-key", "tokens": stale_tokens},
                      {"OPENAI_API_KEY": "fake-api-key"}):
            with self.subTest(mode=value.get("auth_mode")):
                self.write_auth(value)
                self.assertEqual(read_current_account(self.codex_home), {"status": "api_key"})

    def test_missing_or_damaged_login_file_is_unavailable(self):
        self.assertEqual(read_current_account(self.codex_home), {"status": "unavailable"})
        for raw in (b"{", b"\xff", b"[]", b"null", b"{}", b'{"tokens": []}',
                    b'{"tokens": {"id_token": "a.###.c"}}',
                    b'{"tokens": {"id_token": "a._w.c"}}',
                    b'{"tokens": {"id_token": "a.W10.c"}}'):
            with self.subTest(raw=raw):
                self.auth_path.write_bytes(raw)
                self.assertEqual(read_current_account(self.codex_home), {"status": "unavailable"})
        self.auth_path.unlink()
        self.auth_path.mkdir()
        self.assertEqual(read_current_account(self.codex_home), {"status": "unavailable"})

    def test_non_text_fields_and_control_characters_are_not_display_metadata(self):
        self.write_auth({"tokens": {"id_token": fake_jwt({
            "email": ["wrong@example.test"], AUTH_CLAIM: {"chatgpt_plan_type": {"wrong": "type"}}}),
            "access_token": fake_jwt({PROFILE_CLAIM: {"email": "bad\nemail@example.test"},
                                      AUTH_CLAIM: {"chatgpt_plan_type": "pro\x00"}})}})

        self.assertEqual(read_current_account(self.codex_home), {"status": "signed_in"})


if __name__ == "__main__":
    unittest.main()
