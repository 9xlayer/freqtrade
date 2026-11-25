"""Stake and leverage helpers (parity with active StrategyMTFSniper)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


DEFAULT_LEVERAGE = 10.0
COUNTER_1D_TREND_STAKE_SCALE = 0.50

_TAG_MULTIPLIERS = (
    ("long_A80_plus", 1.10),
    ("long_A80plus", 1.10),
    ("short_A80_plus", 1.10),
    ("short_A80plus", 1.10),
    ("short_B65_plus", 1.00),
    ("short_B65plus", 1.00),
    ("short_C_sub65", 0.85),
    ("jev_hunter", 1.00),
)
DEFAULT_TAG_MULTIPLIERS: dict[str, float] = dict(_TAG_MULTIPLIERS)


def normalize_counter_1d_scale(
    value: Any,
    default: float = COUNTER_1D_TREND_STAKE_SCALE,
) -> float:
    """Normalize counter-1D stake scale into (0, 1], falling back when invalid."""
    if value is None:
        return default
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(scale) or scale <= 0.0:
        return default
    return min(1.0, scale)


def _finite_or_none(row: Mapping[str, Any], key: str) -> float | None:
    raw = row.get(key)
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) else None


def resolve_counter_1d_scale(
    side: str,
    row: Mapping[str, Any] | None,
    *,
    counter_scale: float = COUNTER_1D_TREND_STAKE_SCALE,
) -> float:
    """Reduce stake on counter-1D bounce Longs or counter-macro-bull Shorts."""
    if row is None:
        return 1.0
    scale = normalize_counter_1d_scale(counter_scale)
    side_l = (side or "").strip().lower()
    if side_l == "long":
        macd_1d = _finite_or_none(row, "macdhist_1d")
        if macd_1d is not None and macd_1d <= 0.0:
            return scale
        ema50_1d = _finite_or_none(row, "ema_50_1d")
        ema200_1d = _finite_or_none(row, "ema_200_1d")
        if (
            ema50_1d is not None
            and ema200_1d is not None
            and ema200_1d > 0.0
            and ema50_1d <= ema200_1d
        ):
            return scale
        return 1.0
    if side_l == "short":
        macro_bull = _finite_or_none(row, "is_macro_bull_run_1d")
        if macro_bull == 1.0:
            return scale
    return 1.0


def resolve_leverage(max_leverage: float | None, target: float = DEFAULT_LEVERAGE) -> float:
    if max_leverage:
        return min(target, float(max_leverage))
    return target


def resolve_stake_amount(
    *,
    proposed_stake: float,
    max_stake: float,
    entry_tag: str | None,
    enable_dynamic_sizing: bool,
    counter_trend_scale: float = 1.0,
    tag_multipliers: Mapping[str, float] | tuple[tuple[str, float], ...] | None = None,
) -> float:
    """
    Flat 1.0x when dynamic sizing is off. When enabled, apply tag multipliers and
    counter-1D trend scaling (in (0, 1]), then cap at max_stake.
    """
    if not enable_dynamic_sizing:
        return min(proposed_stake, max_stake)

    multiplier = 1.0
    pairs = (
        tuple(tag_multipliers.items())
        if isinstance(tag_multipliers, Mapping)
        else (tag_multipliers if tag_multipliers is not None else _TAG_MULTIPLIERS)
    )
    if entry_tag:
        for needle, mult in pairs:
            if needle in entry_tag:
                multiplier = float(mult)
                break
    trend_scale = normalize_counter_1d_scale(counter_trend_scale, default=1.0)
    return min(proposed_stake * multiplier * trend_scale, max_stake)
