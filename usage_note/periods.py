"""Local calendar ranges and lossless usage buckets for the desktop charts."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from usage_note.timebase import BEIJING_TZ

MODES = ("day", "week", "month")
COUNTS = ("input", "cached", "output", "cache_write", "reasoning", "requests")
METADATA = ("service_tiers", "reasoning_efforts", "context_windows")


def period_bounds(anchor: date, mode: str) -> tuple[date, date]:
    """Return a half-open natural calendar interval, with Monday week starts."""
    if mode == "day":
        return anchor, anchor + timedelta(days=1)
    if mode == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=7)
    if mode == "month":
        start = anchor.replace(day=1)
        return start, move_period(start, "month", 1)
    raise ValueError(f"Unknown period: {mode}")


def move_period(anchor: date, mode: str, amount: int) -> date:
    if mode in ("day", "week"):
        return anchor + timedelta(days=amount * (7 if mode == "week" else 1))
    if mode == "month":
        year, month0 = divmod(anchor.year * 12 + anchor.month - 1 + amount, 12)
        month = month0 + 1
        return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))
    raise ValueError(f"Unknown period: {mode}")


def merge_models(groups) -> dict:
    """Merge additive counts, maximum context size and distinct metadata."""
    merged = {}
    for models in groups:
        for model, values in models.items():
            row = merged.setdefault(model, {
                **dict.fromkeys(COUNTS, 0), "max_request_input": 0,
                **{key: set() for key in METADATA},
            })
            for key in COUNTS:
                row[key] += values.get(key, 0)
            row["max_request_input"] = max(row["max_request_input"], values.get("max_request_input", 0))
            for key in METADATA:
                row[key].update(values.get(key, ()))
    for row in merged.values():
        for key in METADATA:
            row[key] = sorted(row[key])
    return merged


def recent_bounds(mode, *, now=None, tz=BEIJING_TZ):
    """Return a half-open interval of 24 hours, 7 days or 30 elapsed days."""
    if mode not in MODES:
        raise ValueError(f"Unknown period: {mode}")
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = end - timedelta(days={"day": 1, "week": 7, "month": 30}[mode])
    return start.astimezone(tz), end.astimezone(tz)


def period_models(data: dict, anchor: date, mode: str, *, recent=False, now=None, tz=BEIJING_TZ) -> dict:
    if recent:
        start, end = (at.astimezone(timezone.utc) for at in recent_bounds(mode, now=now, tz=tz))
        return merge_models(models for key, models in data.get("instants", {}).items()
                            if start <= datetime.fromisoformat(key).astimezone(timezone.utc) < end)
    start, end = (d.isoformat() for d in period_bounds(anchor, mode))
    return merge_models(models for day, models in data.get("days", {}).items()
                        if start <= day < end)


def local_midnight(day: date, tz=BEIJING_TZ) -> datetime:
    naive = datetime.combine(day, time.min)
    # Resolve the OS local offset at each boundary, not today's fixed offset.
    return naive.astimezone() if tz is None else naive.replace(tzinfo=tz)


def period_buckets(data: dict, anchor: date, mode: str, *, now=None, tz=BEIJING_TZ, recent=False) -> list[dict]:
    """Fill elapsed local hours/days, including the unfinished current bucket.

    Hourly stepping occurs in UTC: spring gaps disappear and fall-back hours
    remain distinct. Future intervals are not represented as measured zeros.
    """
    if recent:
        return _recent_buckets(data, mode, now=now, tz=tz)
    start, end = period_bounds(anchor, mode)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = []
    if mode == "month":
        day = start
        while day < end:
            at = local_midnight(day, tz)
            until = local_midnight(day + timedelta(days=1), tz)
            if at.astimezone(timezone.utc) > now:
                break
            result.append({"at": at, "end": until,
                           "models": data.get("days", {}).get(day.isoformat(), {}),
                           "current": now < until.astimezone(timezone.utc)})
            day += timedelta(days=1)
        return result

    by_instant = {}
    for key, models in data.get("hours", {}).items():
        instant = datetime.fromisoformat(key).astimezone(timezone.utc)
        by_instant.setdefault(instant, []).append(models)
    instant = local_midnight(start, tz).astimezone(timezone.utc)
    stop = local_midnight(end, tz).astimezone(timezone.utc)
    while instant < stop and instant <= now:
        until = instant + timedelta(hours=1)
        result.append({"at": instant.astimezone(tz), "end": until.astimezone(tz),
                       "models": merge_models(by_instant.get(instant, ())),
                       "current": now < until})
        instant = until
    return result


def _recent_buckets(data, mode, *, now, tz):
    start, end = (at.astimezone(timezone.utc) for at in recent_bounds(mode, now=now, tz=tz))
    local_start = start.astimezone(tz)
    instant = (local_midnight(local_start.date(), tz) if mode == "month" else
               local_start.replace(minute=0, second=0, microsecond=0)).astimezone(timezone.utc)
    groups = {}
    for key, models in data.get("instants", {}).items():
        at = datetime.fromisoformat(key).astimezone(timezone.utc)
        if start <= at < end:
            local = at.astimezone(tz)
            bucket = (local_midnight(local.date(), tz) if mode == "month" else
                      local.replace(minute=0, second=0, microsecond=0)).astimezone(timezone.utc)
            groups.setdefault(bucket, []).append(models)
    result = []
    while instant < end:
        until = (local_midnight(instant.astimezone(tz).date() + timedelta(days=1), tz)
                 .astimezone(timezone.utc) if mode == "month" else instant + timedelta(hours=1))
        result.append({"at": max(start, instant).astimezone(tz), "end": min(end, until).astimezone(tz),
                       "models": merge_models(groups.get(instant, ())), "current": until > end})
        instant = until
    return result
