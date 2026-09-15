"""Read account-owned quotas through a short-lived local Codex app-server.

The client performs only initialization and account reads. No conversation is
started, no account is changed, and no reset credit is redeemed. Returned data
is a display-field whitelist; credentials, raw account IDs and RPC responses
are neither logged nor retained.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from .account import _display_text, read_account_identity


class AccountLimitsError(RuntimeError):
    """A safe user-facing quota read failure, without backend response text."""


_READ_FAILED = "当前账号额度读取失败，请稍后刷新。"
_IDENTITY_CHANGED = "账号信息发生变化或无法核对，已丢弃本次额度。"
_TIMED_OUT = "当前账号额度读取超时，请稍后刷新。"


def _find_codex() -> Path:
    for command in ("codex.exe", "codex"):
        found = shutil.which(command)
        if found:
            return Path(found)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        candidates = []
        try:
            for path in base.glob("*/codex.exe"):
                if path.is_file():
                    candidates.append((path.stat().st_mtime_ns, str(path), path))
        except OSError:
            candidates = []
        if candidates:
            return max(candidates)[2]
    raise AccountLimitsError("未找到本机 Codex，暂时无法读取账号额度。")


class _StdioClient:
    def __init__(self, executable: Path, codex_home: Path, deadline: float):
        self.deadline = deadline
        self.next_id = 0
        self.messages = queue.Queue()
        environment = dict(os.environ, CODEX_HOME=str(codex_home))
        self.process = subprocess.Popen(
            [str(executable), "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.reader = threading.Thread(target=self._read,
                                       name=f"codex-note-quota-{self.process.pid}", daemon=True)
        try:
            self.reader.start()
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(message, dict):
                    self.messages.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self.messages.put(None)

    def notify(self, method: str, params: dict | None = None):
        message = {"method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def _write(self, message: dict):
        if time.monotonic() >= self.deadline:
            raise AccountLimitsError(_TIMED_OUT)
        self.process.stdin.write(json.dumps(message, ensure_ascii=True) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        self.next_id += 1
        request_id = self.next_id
        self._write({"id": request_id, "method": method, "params": params})
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise AccountLimitsError(_TIMED_OUT)
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise AccountLimitsError(_TIMED_OUT) from None
            if message is None:
                raise AccountLimitsError(_READ_FAILED)
            # Notifications, unrelated replies and server requests are not
            # responses to this read; no server action is approved here.
            if "method" in message or message.get("id") != request_id:
                continue
            result = message.get("result")
            if "error" in message or not isinstance(result, dict):
                raise AccountLimitsError(_READ_FAILED)
            return result

    def close(self):
        # Closing stdin requests EOF; terminate also unblocks a reader when the
        # service is stuck on a network request. No server is reused across
        # calls, so a later fetch always reloads the current login.
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            except ProcessLookupError:
                self.process.wait()
        if self.reader.ident is not None:
            self.reader.join()
        self.process.stdout.close()


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _window(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {"used_percent": _number(value.get("usedPercent")),
            "window_minutes": _number(value.get("windowDurationMins")),
            "resets_at": _number(value.get("resetsAt"))}


def _normalize_limits(response: dict, at: str) -> dict:
    rates = response.get("rateLimitsByLimitId")
    if not isinstance(rates, dict) or not rates:
        rate = response.get("rateLimits")
        if not isinstance(rate, dict) or not rate:
            return {}
        identity = _display_text(rate.get("limitId")) or "unclassified"
        rates = {identity: rate}
    result = {}
    for key, rate in rates.items():
        identity = _display_text(key)
        if not identity or not isinstance(rate, dict):
            continue
        normalized = {"limit_id": identity,
                      "primary": _window(rate.get("primary")),
                      "secondary": _window(rate.get("secondary"))}
        name = _display_text(rate.get("limitName"))
        if name:
            normalized["limit_name"] = name
        result[identity] = {"at": at, "rate_limits": normalized}
    return result


def _rpc_account(response: dict) -> dict:
    account = response.get("account")
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        raise AccountLimitsError(_IDENTITY_CHANGED)
    return {"email": _display_text(account.get("email")),
            "plan": _display_text(account.get("planType"))}


def _same_email(first: object, second: object) -> bool:
    return (not first or not second or
            isinstance(first, str) and isinstance(second, str) and first.casefold() == second.casefold())


def fetch_account_limits(codex_home: Path, *, timeout: float = 15) -> dict:
    """Return verified current-account quota display data or a safe error.

    The total request deadline covers startup and all RPC reads. Account reads
    bracket the quota read, local identity is rechecked afterwards, and the
    quota response's accountId must match the locally selected account ID hash.
    Matching email alone never permits data to move between accounts.
    """
    client = None
    try:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise AccountLimitsError(_TIMED_OUT)
        deadline = time.monotonic() + timeout
        before = read_account_identity(codex_home)
        if before.get("status") != "signed_in" or not before.get("key"):
            raise AccountLimitsError("当前登录账号无法识别，暂时无法读取账号额度。")
        executable = _find_codex()
        client = _StdioClient(executable, codex_home, deadline)
        client.request("initialize", {"clientInfo": {"name": "codex_usage_note", "version": "0.1.0"}})
        client.notify("initialized")
        rpc_before = _rpc_account(client.request("account/read", {"refreshToken": False}))
        response = client.request("account/rateLimits/read", {"excludeResetCreditDetails": True})
        rpc_after = _rpc_account(client.request("account/read", {"refreshToken": False}))
        after = read_account_identity(codex_home)
        raw_id = _display_text(response.get("accountId"))
        response_key = hashlib.sha256(raw_id.encode("utf-8")).hexdigest() if raw_id else None
        if (after.get("status") != "signed_in" or before["key"] != after.get("key")
                or response_key != before["key"]
                or not _same_email(before.get("email"), after.get("email"))
                or not _same_email(rpc_before["email"], rpc_after["email"])
                or not _same_email(before.get("email"), rpc_before["email"])
                or not _same_email(after.get("email"), rpc_after["email"])):
            raise AccountLimitsError(_IDENTITY_CHANGED)
        at = datetime.now(timezone.utc).isoformat()
        return {"key": response_key,
                "email": rpc_after["email"] or after.get("email"),
                "plan": rpc_after["plan"] or after.get("plan"),
                "at": at, "limits": _normalize_limits(response, at)}
    except AccountLimitsError:
        raise
    except Exception:
        raise AccountLimitsError(_READ_FAILED) from None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                raise AccountLimitsError(_READ_FAILED) from None
