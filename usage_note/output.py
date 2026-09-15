"""Reduce built-in Codex completion events to content-free output metadata."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import ntpath

from usage_note.periods import period_bounds


@dataclass(frozen=True)
class OutputEvent:
    at: datetime
    key: tuple
    added: int = 0
    deleted: int = 0
    files: frozenset[str] = frozenset()
    turns: int = 0


def _file_key(path, cwd):
    normalized = ntpath.normcase(ntpath.normpath(ntpath.join(cwd, path)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _change_lines(change):
    kind = change.get("type")
    if kind in ("add", "delete"):
        content = change.get("content")
        if not isinstance(content, str):
            raise ValueError("Missing file change content")
        count = len(content.splitlines())
        return (count, 0) if kind == "add" else (0, count)
    if kind != "update" or not isinstance(change.get("unified_diff"), str):
        raise ValueError("Unsupported file change")
    added = deleted = 0
    in_hunk = False
    for line in change["unified_diff"].splitlines():
        if line.startswith("@@"):
            in_hunk = True
        elif in_hunk:
            # A source line beginning with ++ or -- is still a changed line.
            added += line.startswith("+")
            deleted += line.startswith("-")
    return added, deleted


def output_event(payload, at, turn_id, cwd):
    """Only completed built-in items count; never infer success from commands."""
    if payload.get("type") == "task_complete":
        turn = payload.get("turn_id") or turn_id
        if not turn:
            return None
        return OutputEvent(at, ("turn", turn), turns=1)
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "FileChange":
        return None
    if item.get("status") != "completed" or not item.get("id"):
        return None
    changes = item.get("changes")
    if not isinstance(changes, dict) or not changes:
        return None
    added = deleted = 0
    files = set()
    for path, change in changes.items():
        if not isinstance(path, str) or not isinstance(change, dict):
            raise ValueError("Invalid file change")
        plus, minus = _change_lines(change)
        added += plus
        deleted += minus
        files.add(_file_key(path, cwd))
    # Item IDs and turn IDs are copied with rollout history, including forks.
    return OutputEvent(at, ("file", item["id"]), added, deleted, frozenset(files))


def output_days(events, tz):
    days = {}
    seen = set()
    for event in sorted(events, key=lambda event: event.at):
        if event.key in seen:
            continue
        seen.add(event.key)
        key = event.at.astimezone(tz).date().isoformat()
        row = days.setdefault(key, {"added": 0, "deleted": 0, "files": set(), "turns": 0})
        row["added"] += event.added
        row["deleted"] += event.deleted
        row["files"].update(event.files)
        row["turns"] += event.turns
    return {day: {**row, "files": sorted(row["files"])} for day, row in sorted(days.items())}


def period_output(data, selected, mode):
    start, end = (bound.isoformat() for bound in period_bounds(selected, mode))
    result = {"added": 0, "deleted": 0, "files": 0, "turns": 0, "recorded": False}
    files = set()
    for day, row in data.get("output_days", {}).items():
        if start <= day < end:
            result["recorded"] = True
            for key in ("added", "deleted", "turns"):
                result[key] += row[key]
            files.update(row["files"])
    result["files"] = len(files)
    return result
