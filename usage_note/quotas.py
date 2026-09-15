"""Present independent quota snapshots without inferring missing account data."""

import math

# User-selected display scope, shared with account reset ordering.
HIDDEN_QUOTA_IDS = frozenset({"codex_bengalfox", "base_model_inference"})


# Verified in local token_count.rate_limits metadata. Other IDs remain distinct.
_KNOWN_TITLES = {
    "codex": "通用额度",
    "codex_bengalfox": "GPT-5.3-Codex-Spark",
}


def rate_limit_id(rate: dict) -> str:
    value = rate.get("limit_id")
    return value.strip() if isinstance(value, str) and value.strip() else "unclassified"


def _window_title(window: dict, slot: str) -> str:
    minutes = window.get("window_minutes")
    if (isinstance(minutes, bool) or not isinstance(minutes, (int, float))
            or not math.isfinite(minutes) or minutes <= 0):
        return f"{'主' if slot == 'primary' else '次'}窗口（时长未知）"
    if minutes == 10080:
        return "每周"
    if minutes % 1440 == 0:
        return f"{minutes / 1440:g} 天"
    if minutes % 60 == 0:
        return f"{minutes / 60:g} 小时"
    return f"{minutes:g} 分钟"


def quota_groups(data: dict) -> list[dict]:
    """Return each quota's own timestamp and actually reported windows.

    No percentage or absent window is synthesized. Legacy single-snapshot
    reports are accepted, with the same strict limit-ID classification.
    """
    snapshots = data.get("latest_rate_limits_by_id")
    if not isinstance(snapshots, dict) or not snapshots:
        rate = data.get("latest_rate_limits")
        snapshots = ({rate_limit_id(rate): {
            "at": data.get("latest_rate_limits_at"), "rate_limits": rate,
        }} if isinstance(rate, dict) and rate else {})
    groups = []
    for identity, snapshot in snapshots.items():
        if not isinstance(identity, str) or not isinstance(snapshot, dict):
            continue
        rate = snapshot.get("rate_limits")
        if not isinstance(rate, dict):
            continue
        if identity in _KNOWN_TITLES:
            title = _KNOWN_TITLES[identity]
        elif identity == "unclassified":
            title = "未分类额度"
        else:
            name = rate.get("limit_name")
            detail = f"{name} ({identity})" if isinstance(name, str) and name.strip() else identity
            title = f"其他额度 · {detail}"
        windows = []
        for slot in ("primary", "secondary"):
            window = rate.get(slot)
            if isinstance(window, dict):
                windows.append({"title": _window_title(window, slot), "window": window})
        groups.append({"id": identity, "title": title, "at": snapshot.get("at"),
                       "windows": windows})
    order = {"codex": 0, "codex_bengalfox": 1, "unclassified": 3}
    return sorted(groups, key=lambda group: (order.get(group["id"], 2), group["id"]))
