"""Read current local login display metadata without exposing credentials."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import unicodedata


_AUTH_CLAIM = "https://api.openai.com/auth"
_PROFILE_CLAIM = "https://api.openai.com/profile"


def _claims(token: object) -> dict | None:
    """Decode display claims only; this does not validate a login or signature."""
    if not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1].encode("ascii")
        payload += b"=" * (-len(payload) % 4)
        value = json.loads(base64.b64decode(payload, altchars=b"-_", validate=True))
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _display_text(value: object) -> str | None:
    if not isinstance(value, str) or any(unicodedata.category(char).startswith("C") for char in value):
        return None
    return value.strip() or None


def _nested_claim(claims: dict, namespace: str, key: str) -> str | None:
    values = claims.get(namespace)
    return _display_text(values.get(key)) if isinstance(values, dict) else None


def read_account_identity(codex_home: Path) -> dict:
    """Return display metadata and a hash of the currently selected account ID.

    Each call rereads the file, so a refresh follows account switches and logout.
    JWT claims are unverified local display metadata, not evidence of a live
    session or ownership of historical usage. A key is available only when
    auth.json identifies the selected account. Raw IDs and credentials never
    leave this function and nothing is written back or cached.
    """
    unavailable = {"status": "unavailable"}
    try:
        auth = json.loads((codex_home / "auth.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        return unavailable
    if not isinstance(auth, dict):
        return unavailable
    mode = auth.get("auth_mode")
    if mode == "apikey" or (mode is None and _display_text(auth.get("OPENAI_API_KEY"))):
        return {"status": "api_key"}

    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        return unavailable
    identity = _claims(tokens.get("id_token"))
    access = _claims(tokens.get("access_token"))
    if identity is None and access is None:
        return unavailable
    identity = identity or {}
    access = access or {}
    email = _display_text(identity.get("email")) or _nested_claim(access, _PROFILE_CLAIM, "email")
    plan = (_nested_claim(identity, _AUTH_CLAIM, "chatgpt_plan_type")
            or _nested_claim(access, _AUTH_CLAIM, "chatgpt_plan_type"))
    result = {"status": "signed_in"}
    if email:
        result["email"] = email
    if plan:
        result["plan"] = plan
    account_id = _display_text(tokens.get("account_id"))
    if account_id:
        result["key"] = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
    return result


def read_current_account(codex_home: Path) -> dict:
    """Return only the current status, email, and plan for display."""
    result = read_account_identity(codex_home)
    result.pop("key", None)
    return result
