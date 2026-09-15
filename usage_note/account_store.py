"""Persist only attributed quota metadata; never credentials or raw account IDs."""

from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path
import re
from .quotas import HIDDEN_QUOTA_IDS


def _number(value):
    try:
        return value if (isinstance(value, (int, float)) and not isinstance(value, bool)
                         and math.isfinite(value)) else None
    except OverflowError:
        return None


def _clean_snapshot(value):
    if not isinstance(value, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("key", ""))):
        raise ValueError("账号快照缺少有效身份")
    at = value.get("at")
    try:
        if datetime.fromisoformat(at).tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("账号快照时间无效") from None
    result = {"key": value["key"], "at": at, "limits": {}}
    for key in ("email", "plan"):
        text = value.get(key)
        if isinstance(text, str):
            result[key] = text[:320]
    limits = value.get("limits")
    if not isinstance(limits, dict):
        raise ValueError("账号额度格式无效")
    for identity, snapshot in limits.items():
        if not isinstance(identity, str) or not isinstance(snapshot, dict):
            continue
        rate = snapshot.get("rate_limits")
        if not isinstance(rate, dict):
            continue
        clean = {"limit_id": identity[:120]}
        name = rate.get("limit_name")
        if isinstance(name, str):
            clean["limit_name"] = name[:120]
        for slot in ("primary", "secondary"):
            window = rate.get(slot)
            if not isinstance(window, dict):
                continue
            clean[slot] = {key: _number(window.get(key))
                           for key in ("window_minutes", "used_percent", "resets_at")}
        result["limits"][identity[:120]] = {"at": at, "rate_limits": clean}
    return result


def earliest_reset(limits):
    times = []
    for identity, snapshot in limits.items():
        if identity in HIDDEN_QUOTA_IDS:
            continue
        for slot in ("primary", "secondary"):
            window = snapshot.get("rate_limits", {}).get(slot) or {}
            value = _number(window.get("resets_at"))
            if value is not None and 0 < value < 253402300800:
                times.append(value)
    return min(times, default=None)


class AccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.warnings = []
        self._accounts = {}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("version") != 1:
                raise ValueError
            for value in document["accounts"]:
                clean = _clean_snapshot(value)
                self._accounts[clean["key"]] = clean
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            self.warnings.append("部分账号快照未能读取，已保留可读账号")

    def remember(self, snapshot):
        clean = _clean_snapshot(snapshot)
        updated = {**self._accounts, clean["key"]: clean}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"version": 1, "accounts": list(updated.values())},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self._accounts = updated

    def pages(self, current: dict, now: datetime):
        key = current.get("key") or "current-unavailable"
        active = deepcopy(self._accounts.get(key, {"key": key, "limits": {}, "at": None}))
        for field in ("email", "plan", "status"):
            if field == "plan" and active.get("plan"):
                continue
            if field in current:
                active[field] = current[field]
        active["current"] = True
        others = [dict(deepcopy(value), current=False) for identity, value in self._accounts.items()
                  if identity != key]
        for page in [active, *others]:
            page["next_reset"] = earliest_reset(page["limits"])
            page["reset_due"] = page["next_reset"] is not None and page["next_reset"] <= now.timestamp()
        others.sort(key=lambda page: (page["next_reset"] if page["next_reset"] is not None else math.inf,
                                     page["key"]))
        return [active, *others]
