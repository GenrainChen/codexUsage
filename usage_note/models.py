"""Model identity helpers for separating Codex speed tiers in reports."""

from __future__ import annotations


FAST_SERVICE_TIERS = frozenset({"fast", "priority"})
FAST_SUFFIX = " [FAST]"
ULTRAFAST_SUFFIX = " [ULTRAFAST]"
SPEED_SUFFIXES = {"fast": FAST_SUFFIX, "ultrafast": ULTRAFAST_SUFFIX}


def is_fast_service_tier(service_tier: str | None) -> bool:
    value = service_tier.strip().lower() if isinstance(service_tier, str) else ""
    return value in FAST_SERVICE_TIERS


def model_key(model: str, service_tier: str | None) -> str:
    """Keep standard model keys compatible and separate each speed tier."""
    name = model if isinstance(model, str) and model else "unknown"
    tier = service_tier.strip().lower() if isinstance(service_tier, str) else ""
    if tier == "ultrafast":
        return f"{name}{ULTRAFAST_SUFFIX}"
    return f"{name}{FAST_SUFFIX}" if is_fast_service_tier(service_tier) else name


def model_service_tier(model_key_value: str) -> str | None:
    if isinstance(model_key_value, str):
        for tier, suffix in SPEED_SUFFIXES.items():
            if model_key_value.endswith(suffix):
                return tier
    return None


def base_model(model_key_value: str) -> str:
    tier = model_service_tier(model_key_value)
    if tier:
        return model_key_value[:-len(SPEED_SUFFIXES[tier])]
    return model_key_value


def model_label(model_key_value: str) -> str:
    name = base_model(model_key_value)
    tier = model_service_tier(model_key_value)
    return f"{name} · {tier.upper()}" if tier else name
