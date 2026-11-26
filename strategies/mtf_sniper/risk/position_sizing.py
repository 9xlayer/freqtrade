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


def resolve_counter_1w_scale(
    side: str,
    row: Mapping[str, Any] | None,
    *,
    counter_1w_scale: float | None = None,
) -> float:
    """Scale down Long stake during weekly bear waves (cycle_top / bear_trending)."""
    if counter_1w_scale is None or row is None:
        return 1.0
    side_l = (side or "").strip().lower()
    if side_l != "long":
        return 1.0
    scale = normalize_counter_1d_scale(counter_1w_scale, default=1.0)
    regime_1w = str(row.get("market_regime_1w") or "").strip().lower()
    if regime_1w in ("cycle_top", "bear_trending"):
        return scale
    bear_wave = _finite_or_none(row, "is_bear_wave_1w")
    if bear_wave == 1.0:
        return scale
    return 1.0


def resolve_leverage(max_leverage: float | None, target: float = DEFAULT_LEVERAGE) -> float:
    if max_leverage:
        return min(target, float(max_leverage))
    return target


def resolve_risk_cap_stake(
    wallet_equity: float | None,
    stoploss_pct: float | None,
    risk_per_trade_pct: float | None,
) -> float | None:
    """Return max margin stake capping hard-SL loss at ``risk_per_trade_pct`` of equity."""
    if wallet_equity is None or stoploss_pct is None or risk_per_trade_pct is None:
        return None
    try:
        eq = float(wallet_equity)
        sl = abs(float(stoploss_pct))
        risk = float(risk_per_trade_pct)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(eq) and math.isfinite(sl) and math.isfinite(risk)):
        return None
    if eq <= 0.0 or sl <= 0.0 or not (0.0 < risk < 1.0):
        return None
    return (eq * risk) / sl


def resolve_stake_amount(
    *,
    proposed_stake: float,
    max_stake: float,
    entry_tag: str | None,
    enable_dynamic_sizing: bool,
    counter_trend_scale: float = 1.0,
    tag_multipliers: Mapping[str, float] | tuple[tuple[str, float], ...] | None = None,
    wallet_equity: float | None = None,
    stoploss_pct: float | None = None,
    risk_per_trade_pct: float | None = None,
) -> float:
    """
    Scale proposed_stake by tag multipliers and counter-1D trend scale (when dynamic
    sizing is enabled). When risk_per_trade_pct is active, enforce a hard budget
    ceiling (`risk_cap * min(1.0, multiplier) * trend_scale`) so multipliers > 1.0
    never breach the risk budget while downward scaling stays effective.
    """
    risk_cap = resolve_risk_cap_stake(wallet_equity, stoploss_pct, risk_per_trade_pct)
    if not enable_dynamic_sizing:
        base_stake = min(proposed_stake, risk_cap) if risk_cap is not None else proposed_stake
        return min(base_stake, max_stake)

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
    scaled = proposed_stake * multiplier * trend_scale
    if risk_cap is not None:
        budget_cap = risk_cap * min(1.0, multiplier) * trend_scale
        scaled = min(scaled, budget_cap)
    return min(scaled, max_stake)
