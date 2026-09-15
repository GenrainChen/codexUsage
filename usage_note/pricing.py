"""Explicit, source-backed standard API rates. No guessed model aliases."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _price(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('单价必须是非负数字') from None
    if not result.is_finite() or result < 0:
        raise ValueError('单价必须是有限的非负数字')
    return result


class PriceBook:
    def __init__(self, path: Path, overrides_path: Path):
        self.path = Path(path)
        self.overrides_path = Path(overrides_path)
        self.warnings: list[str] = []
        self.catalog = self._load(self.path, required=True)
        self.overrides = self._load(self.overrides_path).get('models', {})
        self.models = {**self.catalog.get('models', {}), **self.overrides}
        self.updated_at = self.catalog.get('updated_at', '')
        self.notes = self.catalog.get('notes', [])

    def _load(self, path: Path, required=False) -> dict:
        if not path.exists() and not required:
            return {}
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or not isinstance(data.get('models', {}), dict):
                raise ValueError('报价文件必须包含 models 对象')
            valid = {}
            for model, rates in data.get('models', {}).items():
                try:
                    if not isinstance(rates, dict):
                        raise ValueError('模型报价必须是对象')
                    for key in ('input', 'cached', 'output'):
                        _price(rates[key])
                    if rates.get('cache_write') is not None:
                        _price(rates['cache_write'])
                    valid[model] = rates
                except (ValueError, KeyError, TypeError):
                    self.warnings.append(f'{model} 报价格式无效，已忽略')
            return {**data, 'models': valid}
        except (OSError, ValueError) as exc:
            self.warnings.append(f'无法读取 {path.name}：{exc}')
            return {}

    def quote(self, model: str, tokens: dict) -> dict:
        rates = self.models.get(model)
        result = dict(input_usd=None, cached_usd=None, output_usd=None,
                      total_usd=None, cache_write_usd=None,
                      cache_write_tokens=max(0, int(tokens.get('cache_write', 0))),
                      known_usd=0.0, input_known_usd=0.0, partial=True, rates=rates,
                      source=rates.get('source', '') if rates else None)
        if rates is None:
            return result
        counts = {key: max(0, int(tokens.get(key, 0))) for key in ('input', 'cached', 'output')}
        writes = min(counts['input'], result['cache_write_tokens'])
        million = Decimal(1_000_000)
        ordinary_cost = Decimal(counts['input'] - writes) * _price(rates['input']) / million
        write_cost = (Decimal(writes) * _price(rates['cache_write']) / million
                      if rates.get('cache_write') is not None else (Decimal(0) if writes == 0 else None))
        input_cost = ordinary_cost + write_cost if write_cost is not None else None
        cached_cost = Decimal(counts['cached']) * _price(rates['cached']) / million
        output_cost = Decimal(counts['output']) * _price(rates['output']) / million
        known = ordinary_cost + (write_cost or Decimal(0)) + cached_cost + output_cost
        result.update(input_usd=float(input_cost) if input_cost is not None else None,
                      cached_usd=float(cached_cost), output_usd=float(output_cost),
                      cache_write_usd=float(write_cost) if write_cost is not None else None,
                      input_known_usd=float(ordinary_cost + (write_cost or Decimal(0))),
                      known_usd=float(known), partial=input_cost is None,
                      total_usd=float(known) if input_cost is not None else None)
        return result

    def set_override(self, model: str, input, cached, output,
                     source: str = '手动设置', cache_write=None) -> None:
        model = model.strip()
        if not model:
            raise ValueError('请输入模型名称')
        rates = {key: float(_price(value)) for key, value in
                 [('input', input), ('cached', cached), ('output', output)]}
        if cache_write is not None and str(cache_write).strip():
            rates['cache_write'] = float(_price(cache_write))
        rates['source'] = str(source).strip() or '手动设置'
        updated = {**self.overrides, model: rates}
        self._save(updated)
        self.overrides = updated
        self.models = {**self.catalog.get('models', {}), **self.overrides}

    def reset_override(self, model: str) -> None:
        updated = {key: value for key, value in self.overrides.items() if key != model}
        self._save(updated)
        self.overrides = updated
        self.models = {**self.catalog.get('models', {}), **updated}

    def _save(self, models: dict) -> None:
        self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.overrides_path.with_suffix('.tmp')
        pending.write_text(json.dumps({'models': models}, ensure_ascii=False, indent=2,
                                      allow_nan=False), encoding='utf-8')
        pending.replace(self.overrides_path)
