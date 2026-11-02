"""Explicit metadata-only checkpoint encoding; never deserialize Python objects."""

from collections import Counter
from datetime import datetime
import math


_STATE_FIELDS = (
    'signature', 'inode', 'offset', 'boundary', 'boundary_size', 'prefix', 'prefix_size',
    'model', 'turn_id', 'session_id', 'cwd', 'history_base', 'service_tier', 'effort',
    'previous', 'counter_epoch', 'warnings', 'latest_event', 'rate_at', 'rate', 'rates_by_id',
)
_EVENT_FIELDS = (
    'at', 'model', 'tokens', 'key', 'request_input', 'service_tier', 'effort',
    'context_window', 'counter_start', 'counter_end', 'chain',
)
_OUTPUT_FIELDS = ('at', 'key', 'added', 'deleted', 'files', 'turns')


def _pack(value):
    if isinstance(value, datetime):
        return {'$time': value.isoformat()}
    if isinstance(value, bytes):
        return {'$bytes': value.hex()}
    if isinstance(value, (tuple, frozenset)):
        return {'$tuple': [_pack(item) for item in value]}
    if isinstance(value, dict):
        return {key: _pack(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_pack(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError('Unsupported checkpoint value')


def _unpack(value):
    if isinstance(value, dict):
        if set(value) == {'$time'}:
            result = datetime.fromisoformat(value['$time'])
            if result.tzinfo is None:
                raise ValueError('Naive checkpoint timestamp')
            return result
        if set(value) == {'$bytes'}:
            return bytes.fromhex(value['$bytes'])
        if set(value) == {'$tuple'}:
            return tuple(_unpack(item) for item in value['$tuple'])
        return {key: _unpack(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unpack(item) for item in value]
    return value


def rate_metadata(rate):
    """Keep known quota metadata, never arbitrary nested payload fields."""
    if not isinstance(rate, dict):
        return None
    result = {key: rate[key] for key in ('limit_id', 'limit_name', 'plan_type')
              if isinstance(rate.get(key), str)}
    for slot in ('primary', 'secondary'):
        if slot in rate and rate[slot] is None:
            result[slot] = None
        if isinstance(rate.get(slot), dict):
            result[slot] = {key: number for key in ('used_percent', 'window_minutes', 'resets_at')
                            if key in rate[slot] and ((number := rate[slot][key]) is None
                            or (isinstance(number, (int, float))
                                and not isinstance(number, bool) and math.isfinite(number)))}
    credits = rate.get('credits')
    if isinstance(credits, dict):
        result['credits'] = {key: credits[key] for key in ('has_credits', 'unlimited')
                             if isinstance(credits.get(key), bool)}
        balance = credits.get('balance')
        if isinstance(balance, (str, int, float)) and not isinstance(balance, bool):
            result['credits']['balance'] = balance
    return result


def encode_state(state):
    data = {name: getattr(state, name) for name in _STATE_FIELDS}
    data['rate'] = rate_metadata(state.rate)
    data['rates_by_id'] = {key: (at, rate_metadata(rate))
                           for key, (at, rate) in state.rates_by_id.items()}
    data['events'] = [{name: getattr(event, name) for name in _EVENT_FIELDS}
                      for event in state.events]
    data['output_events'] = [{name: getattr(event, name) for name in _OUTPUT_FIELDS}
                             for event in state.output_events]
    return _pack(data)


def _integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('Invalid checkpoint count')


def _vector(value, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, tuple) or len(value) != 5:
        raise ValueError('Invalid checkpoint vector')
    for item in value:
        _integer(item)
    if value[1] + value[3] > value[0] or value[4] > value[2]:
        raise ValueError('Invalid checkpoint token subsets')


def decode_state(payload):
    from .reader import _FileState, _UsageEvent
    from .output import OutputEvent

    data = _unpack(payload)
    if not isinstance(data, dict) or set(data) != set(_STATE_FIELDS) | {'events', 'output_events'}:
        raise ValueError('Invalid checkpoint fields')
    for name in ('inode', 'offset', 'boundary_size', 'prefix_size', 'counter_epoch'):
        _integer(data[name])
    for name in ('model', 'cwd'):
        if not isinstance(data[name], str):
            raise ValueError('Invalid checkpoint context')
    for name in ('turn_id', 'session_id', 'service_tier', 'effort'):
        if data[name] is not None and not isinstance(data[name], str):
            raise ValueError('Invalid checkpoint context')
    if type(data['history_base']) is not bool:
        raise ValueError('Invalid history marker')
    signature = data['signature']
    if not isinstance(signature, tuple) or len(signature) != 3:
        raise ValueError('Invalid file signature')
    for number in signature:
        _integer(number)
    if data['offset'] > signature[0] or data['boundary_size'] > min(data['offset'], 64):
        raise ValueError('Invalid checkpoint offset')
    if data['prefix_size'] > min(data['offset'], 4096):
        raise ValueError('Invalid checkpoint prefix')
    for digest in ('prefix', 'boundary'):
        if not isinstance(data[digest], bytes) or len(data[digest]) not in (0, 32):
            raise ValueError('Invalid checkpoint digest')
        if data[digest + '_size'] and len(data[digest]) != 32:
            raise ValueError('Missing checkpoint digest')
    _vector(data['previous'], optional=True)
    for name in ('latest_event', 'rate_at'):
        if data[name] is not None and not isinstance(data[name], datetime):
            raise ValueError('Invalid checkpoint timestamp')
    if not isinstance(data['warnings'], dict):
        raise ValueError('Invalid checkpoint warnings')
    for count in data['warnings'].values():
        _integer(count)
    data['warnings'] = Counter(data['warnings'])
    if data['rate'] is not None and not isinstance(data['rate'], dict):
        raise ValueError('Invalid quota snapshot')
    if not isinstance(data['rates_by_id'], dict):
        raise ValueError('Invalid quota snapshots')
    for identity, value in data['rates_by_id'].items():
        if (not isinstance(identity, str) or not isinstance(value, tuple) or len(value) != 2
                or not isinstance(value[0], datetime) or not isinstance(value[1], dict)):
            raise ValueError('Invalid quota snapshot')
    events = []
    for item in data.pop('events'):
        if set(item) != set(_EVENT_FIELDS) or not isinstance(item['at'], datetime):
            raise ValueError('Invalid usage event')
        _vector(item['tokens'])
        _vector(item['counter_start'], optional=True)
        _vector(item['counter_end'], optional=True)
        _integer(item['request_input'])
        for name in ('key', 'chain'):
            if not isinstance(item[name], tuple):
                raise ValueError('Invalid event identity')
            hash(item[name])
        if not isinstance(item['model'], str):
            raise ValueError('Invalid event model')
        for name in ('service_tier', 'effort'):
            if item[name] is not None and not isinstance(item[name], str):
                raise ValueError('Invalid event setting')
        if item['context_window'] is not None:
            _integer(item['context_window'])
        if (item['counter_start'] is None) != (item['counter_end'] is None):
            raise ValueError('Incomplete counter interval')
        if item['counter_start'] is not None:
            delta = tuple(end - start for start, end in zip(item['counter_start'], item['counter_end']))
            if delta != item['tokens']:
                raise ValueError('Invalid counter interval')
        events.append(_UsageEvent(**item))
    outputs = []
    for item in data.pop('output_events'):
        if set(item) != set(_OUTPUT_FIELDS) or not isinstance(item['at'], datetime):
            raise ValueError('Invalid output event')
        for name in ('added', 'deleted', 'turns'):
            _integer(item[name])
        if not isinstance(item['key'], tuple):
            raise ValueError('Invalid output identity')
        hash(item['key'])
        if not isinstance(item['files'], tuple) or any(
                not isinstance(key, str) or len(key) != 64
                or any(character not in '0123456789abcdef' for character in key)
                for key in item['files']):
            raise ValueError('Invalid file hashes')
        item['files'] = frozenset(item['files'])
        outputs.append(OutputEvent(**item))
    return _FileState(**data, events=events, output_events=outputs)
