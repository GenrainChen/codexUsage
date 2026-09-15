"""The note's calendar and display clock are fixed to Beijing time."""

from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8), "UTC+08:00")
TIMEZONE_LABEL = "北京时间 · UTC+8"


def now():
    return datetime.now(BEIJING_TZ)


def today():
    return now().date()
