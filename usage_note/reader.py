"""Read local Codex usage metadata without storing conversation content.

The public ``input`` amount excludes cache reads but includes cache writes.
``cache_write`` and ``reasoning`` are subsets of input and output respectively.
Counter state belongs to each physical rollout, not session_meta.session_id:
pagination and subagents can share the latter while keeping separate counters.
"""

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from usage_note.timebase import BEIJING_TZ
import json
import heapq
import hashlib
from pathlib import Path
import re
from typing import Any

from .quotas import rate_limit_id
from .output import OutputEvent, output_days, output_event


_ROOT_TYPE = re.compile(rb'"type"\s*:\s*"(session_meta|turn_context|event_msg)"')
_TOKEN_TYPE = re.compile(rb'"type"\s*:\s*"(token_count|item_completed|task_complete)"')
_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
           "cache_write_input_tokens", "reasoning_output_tokens")
# User-defined reporting scope; this is not a statement about provider billing.
EXCLUDED_MODELS = frozenset({"codex-auto-review"})
_WARNINGS = {
    "read_error": "部分用量文件暂时无法读取，已保留可读数据",
    "malformed": "已跳过格式无效的用量元数据",
    "unknown_model": "部分记录缺少模型，列为 unknown",
    "inherited_baseline": "分页首条含历史累计基线，仅计本次增量",
    "missing_history": "首条累计大于本次用量，仅计本次；更早历史可能不完整",
    "missing_snapshots": "累计增量含未单独记录的请求，按当前记录时间和模型归集",
    "missing_total": "部分记录缺少累计值，按单次用量计数",
    "invalid_usage": "已跳过字段无效或缓存超过输入的用量记录",
    "naive_time": "部分记录未标明时区，按 UTC 解释",
    "invalid_output": "部分文件产出记录不完整，仅统计可确认的记录",
}


def _vector(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, dict):
        return None
    result = []
    for key in _FIELDS:
        number = value.get(key, 0)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ValueError("Invalid token value")
        result.append(number)
    if result[1] + result[3] > result[0] or result[4] > result[2]:
        raise ValueError("Invalid token subset")
    return tuple(result)


def _timestamp(value: Any, warnings: Counter) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Missing event time")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        warnings["naive_time"] += 1
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


@dataclass
class _UsageEvent:
    at: datetime
    model: str
    tokens: tuple[int, ...]
    key: tuple
    request_input: int
    service_tier: str | None
    effort: str | None
    context_window: int | None
    counter_start: tuple[int, ...] | None
    counter_end: tuple[int, ...] | None
    chain: tuple


@dataclass
class _FileState:
    signature: tuple | None = None
    inode: int = 0
    offset: int = 0
    boundary: bytes = b""
    boundary_size: int = 0
    model: str = "unknown"
    turn_id: str | None = None
    session_id: str | None = None
    cwd: str = ""
    history_base: bool = False
    service_tier: str | None = None
    effort: str | None = None
    previous: tuple[int, ...] | None = None
    counter_epoch: int = 0
    events: list[_UsageEvent] = field(default_factory=list)
    output_events: list[OutputEvent] = field(default_factory=list)
    warnings: Counter = field(default_factory=Counter)
    latest_event: datetime | None = None
    rate_at: datetime | None = None
    rate: dict | None = None
    rates_by_id: dict[str, tuple[datetime, dict]] = field(default_factory=dict)


def _covered_events(events: list[_UsageEvent]) -> tuple[list[_UsageEvent], int]:
    """Union observed counter intervals only when copied snapshots link chains.

    A full rollout can span requests omitted by a partial fork. Keeping either
    just the longest or just the shortest duplicate delta loses information:
    the union retains uncovered portions and charges overlapping portions once.
    Each boundary is an actual or last_usage-derived cumulative vector, so
    input/cache/output are subtracted exactly, never allocated by a ratio.
    """
    parents: dict[tuple, tuple] = {}
    snapshot_chain: dict[tuple, tuple] = {}
    interval_keys: set[tuple] = set()

    def find(chain: tuple) -> tuple:
        parents.setdefault(chain, chain)
        if parents[chain] != chain:
            parents[chain] = find(parents[chain])
        return parents[chain]

    for event in events:
        if event.counter_start is None:
            continue
        interval_keys.add(event.key)
        root = find(event.chain)
        if event.key in snapshot_chain:
            parents[root] = find(snapshot_chain[event.key])
        else:
            snapshot_chain[event.key] = event.chain

    groups: dict[tuple, list[_UsageEvent]] = {}
    resolved: dict[tuple, _UsageEvent] = {}
    for event in events:
        if event.counter_start is not None:
            groups.setdefault(find(event.chain), []).append(event)
        elif event.key not in interval_keys:
            resolved.setdefault(event.key, event)

    for group in groups.values():
        points: dict[int, tuple[int, ...]] = {}
        starts: dict[int, list[tuple]] = {}
        for index, event in enumerate(group):
            start, end = event.counter_start, event.counter_end
            left, right = start[0] + start[2], end[0] + end[2]
            points[left], points[right] = start, end
            # The shortest covering interval supplies the closest observation
            # time/model. Other portions of longer intervals remain covered.
            starts.setdefault(left, []).append((right - left, index, right, event))
        boundaries = sorted(points)
        active: list[tuple] = []
        for left, right in zip(boundaries, boundaries[1:]):
            for item in starts.get(left, ()):
                heapq.heappush(active, item)
            while active and active[0][2] <= left:
                heapq.heappop(active)
            if not active:
                continue
            owner = active[0][3]
            delta = tuple(now - old for now, old in zip(points[right], points[left]))
            previous = resolved.get(owner.key)
            if previous is not None:
                delta = tuple(old + new for old, new in zip(previous.tokens, delta))
            resolved[owner.key] = replace(owner, tokens=delta)
    duplicates = len(events) - len({event.key for event in events})
    return list(resolved.values()), duplicates


def _add_event(models: dict[str, dict], event: _UsageEvent) -> None:
    """Use the same contribution and metadata rules for every time grain."""
    row = models.setdefault(event.model, {
        "input": 0, "cached": 0, "output": 0, "cache_write": 0,
        "reasoning": 0, "requests": 0, "max_request_input": 0,
        "service_tiers": set(), "reasoning_efforts": set(),
        "context_windows": set(),
    })
    raw_input, cached, output, cache_write, reasoning = event.tokens
    row["input"] += raw_input - cached
    row["cached"] += cached
    row["output"] += output
    row["cache_write"] += cache_write
    row["reasoning"] += reasoning
    row["requests"] += 1
    row["max_request_input"] = max(row["max_request_input"], event.request_input)
    if event.service_tier:
        row["service_tiers"].add(event.service_tier)
    if event.effort:
        row["reasoning_efforts"].add(event.effort)
    if event.context_window:
        row["context_windows"].add(event.context_window)


class UsageReader:
    """Incremental JSONL reader. Call scan serially from one worker thread.

    Defaults to fixed UTC+8. Tests and exports may override ``tz`` explicitly;
    ``tz=None`` opts into the computer's local timezone and daylight saving rules.
    Cached state contains usage metadata only and is never written to disk.
    """

    def __init__(self, codex_home: Path, *, tz: tzinfo | None = BEIJING_TZ):
        self.codex_home = Path(codex_home)
        self.tz = tz
        self._files: dict[Path, _FileState] = {}

    def scan(self) -> dict:
        paths: set[Path] = set()
        warnings: Counter = Counter()
        for folder in ("sessions", "archived_sessions"):
            try:
                paths.update((self.codex_home / folder).rglob("*.jsonl"))
            except OSError:
                warnings["read_error"] += 1
        self._files = {path: state for path, state in self._files.items() if path in paths}
        bytes_read = 0
        for path in sorted(paths):
            try:
                bytes_read += self._read_file(path)
            except OSError:
                warnings["read_error"] += 1

        days: dict[str, dict[str, dict]] = {}
        hours: dict[str, dict[str, dict]] = {}
        recorded_events: list[_UsageEvent] = []
        latest_event = None
        latest_rate_at = None
        latest_rate = None
        latest_rates_by_id: dict[str, tuple[datetime, dict]] = {}
        for state in self._files.values():
            warnings.update(state.warnings)
            if state.latest_event and (latest_event is None or state.latest_event > latest_event):
                latest_event = state.latest_event
            if state.rate_at and (latest_rate_at is None or state.rate_at > latest_rate_at):
                latest_rate_at, latest_rate = state.rate_at, state.rate
            for identity, snapshot in state.rates_by_id.items():
                previous = latest_rates_by_id.get(identity)
                if previous is None or snapshot[0] > previous[0]:
                    latest_rates_by_id[identity] = snapshot
            recorded_events.extend(state.events)
        selected, duplicates = _covered_events(recorded_events)
        for event in selected:
            # Filter after resolving counters so an excluded request cannot leak
            # into the next visible model's cumulative delta.
            if event.model in EXCLUDED_MODELS:
                continue
            local_at = event.at.astimezone(self.tz)
            day = local_at.date().isoformat()
            # Keep the event's offset when truncating its wall-clock hour so
            # daylight-saving repeats cannot collapse into one bucket.
            hour = local_at.replace(minute=0, second=0, microsecond=0,
                                    tzinfo=timezone(local_at.utcoffset())).isoformat()
            _add_event(days.setdefault(day, {}), event)
            _add_event(hours.setdefault(hour, {}), event)
        for buckets in (days, hours):
            for models in buckets.values():
                for row in models.values():
                    for key in ("service_tiers", "reasoning_efforts", "context_windows"):
                        row[key] = sorted(row[key])
        return {
            "days": dict(sorted(days.items())),
            "output_days": output_days(
                (event for state in self._files.values() for event in state.output_events), self.tz),
            "hours": dict(sorted(hours.items(), key=lambda item: datetime.fromisoformat(item[0]))),
            "latest_rate_limits": latest_rate,
            "latest_rate_limits_at": latest_rate_at.isoformat() if latest_rate_at else None,
            "latest_rate_limits_by_id": {
                identity: {"at": at.isoformat(), "rate_limits": rate}
                for identity, (at, rate) in sorted(latest_rates_by_id.items())
            },
            "latest_event_at": latest_event.isoformat() if latest_event else None,
            "files_scanned": len(paths),
            "bytes_read": bytes_read,
            "duplicate_events": duplicates,
            "warnings": [f"{_WARNINGS.get(code, code)}（{count}）"
                         for code, count in sorted(warnings.items()) if count],
        }

    def _read_file(self, path: Path) -> int:
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
        state = self._files.get(path)
        if state and state.signature == signature:
            return 0
        bytes_read = 0
        # Python's normal read mode permits simultaneous Codex append writes on
        # Windows. Close promptly so archived sessions can be moved afterward.
        with path.open("rb") as stream:
            # A changed file that did not grow can have edits anywhere, even
            # when its final completion record is identical. Only growing
            # rollouts qualify for the append fast path.
            restart = (state is None or stat.st_size < state.offset
                       or stat.st_ino != state.inode
                       or (state.signature is not None and stat.st_size <= state.signature[0]))
            if state and not restart and state.boundary:
                stream.seek(state.offset - state.boundary_size)
                probe = stream.read(state.boundary_size)
                bytes_read += len(probe)
                restart = hashlib.sha256(probe).digest() != state.boundary
            if restart:
                state = _FileState(inode=stat.st_ino)
                self._files[path] = state
            stream.seek(state.offset)
            while True:
                line = stream.readline()
                bytes_read += len(line)
                if not line or not line.endswith(b"\n"):
                    # Leave offset before the incomplete line for the next poll.
                    break
                state.offset = stream.tell()
                state.boundary_size = min(64, len(line))
                state.boundary = hashlib.sha256(line[-64:]).digest()
                root_match = _ROOT_TYPE.search(line[:180])
                if not root_match:
                    continue
                root_type = root_match.group(1)
                if root_type == b"event_msg" and not _TOKEN_TYPE.search(line[:240]):
                    continue
                if root_type == b"event_msg" and b'"item_completed"' in line[:240] and b'"FileChange"' not in line[:1000]:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        self._consume(obj, state, path)
                except (ValueError, TypeError, KeyError, AttributeError):
                    # Never include line text: it can contain private material.
                    state.warnings["malformed"] += 1
            state.signature = signature
        return bytes_read

    def _consume(self, obj: dict, state: _FileState, path: Path) -> None:
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            return
        root_type = obj.get("type")
        if root_type == "session_meta":
            state.session_id = payload.get("id") or payload.get("session_id")
            state.cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
            state.history_base = bool(payload.get("history_base") or payload.get("forked_from_id"))
            return
        if root_type == "turn_context":
            model = payload.get("model")
            state.model = model if isinstance(model, str) and model else "unknown"
            state.turn_id = payload.get("turn_id")
            state.service_tier = payload.get("service_tier")
            state.effort = payload.get("effort")
            if isinstance(payload.get("cwd"), str):
                state.cwd = payload["cwd"]
            return
        if root_type == "event_msg" and payload.get("type") in ("item_completed", "task_complete"):
            if state.model not in EXCLUDED_MODELS:
                try:
                    at = _timestamp(obj.get("timestamp"), state.warnings)
                    event = output_event(payload, at, state.turn_id, state.cwd)
                    if event is not None:
                        state.output_events.append(event)
                except (ValueError, TypeError, KeyError):
                    state.warnings["invalid_output"] += 1
            return
        if root_type != "event_msg" or payload.get("type") != "token_count":
            return
        at = _timestamp(obj.get("timestamp"), state.warnings)
        if state.latest_event is None or at > state.latest_event:
            state.latest_event = at
        rate = payload.get("rate_limits")
        if isinstance(rate, dict) and rate:
            if state.rate_at is None or at > state.rate_at:
                state.rate_at, state.rate = at, rate
            identity = rate_limit_id(rate)
            previous_rate = state.rates_by_id.get(identity)
            if previous_rate is None or at > previous_rate[0]:
                state.rates_by_id[identity] = at, rate
        info = payload.get("info")
        if not isinstance(info, dict):
            return
        try:
            total = _vector(info.get("total_token_usage"))
            last = _vector(info.get("last_token_usage"))
        except ValueError:
            state.warnings["invalid_usage"] += 1
            return
        previous = state.previous
        counter_end = total
        if total is not None:
            state.previous = total
            if total == previous:
                return
            if total[0] + total[2] == 0:
                state.counter_epoch += 1
                return
            if previous is None:
                delta = last if last is not None else total
                if last is not None and total != last:
                    key = "inherited_baseline" if state.history_base else "missing_history"
                    state.warnings[key] += 1
            elif total == last or any(now < old for now, old in zip(total, previous)):
                # A restart can have a larger total than the previous process.
                # total == last identifies its fresh accumulator as well.
                delta = last if last is not None else total
                state.counter_epoch += 1
            else:
                delta = tuple(now - old for now, old in zip(total, previous))
                if last is not None and delta != last:
                    state.warnings["missing_snapshots"] += 1
        elif last is not None:
            delta = last
            state.warnings["missing_total"] += 1
            if previous is not None:
                # This last-only request has already been charged. Advance the
                # known baseline so a resumed cumulative snapshot cannot charge
                # the same interval again.
                counter_end = tuple(old + new for old, new in zip(previous, last))
                state.previous = counter_end
        else:
            return
        if delta[0] + delta[2] == 0:
            return
        if delta[1] + delta[3] > delta[0] or delta[4] > delta[2]:
            state.warnings["invalid_usage"] += 1
            return
        if state.model == "unknown":
            state.warnings["unknown_model"] += 1
        # The copied turn id, timestamp and exact snapshot identify fork history.
        # Equal counts in independent turns are distinct billable requests.
        scope = ("turn", state.turn_id) if state.turn_id else ("file", str(path))
        key = (scope, at, state.model, total, last)
        service_tier = info.get("service_tier") or state.service_tier
        service_tier = service_tier if isinstance(service_tier, str) else None
        effort = state.effort if isinstance(state.effort, str) else None
        context_window = info.get("model_context_window")
        if not isinstance(context_window, int) or isinstance(context_window, bool):
            context_window = None
        counter_start = (tuple(now - used for now, used in zip(counter_end, delta))
                         if counter_end is not None else None)
        state.events.append(_UsageEvent(at, state.model, delta, key,
                            last[0] if last else delta[0], service_tier, effort,
                            context_window, counter_start, counter_end,
                            (str(path), state.counter_epoch)))
