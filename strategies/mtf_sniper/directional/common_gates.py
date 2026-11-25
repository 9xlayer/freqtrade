"""Shared market gates (series masks + row helpers for Hunter post-AI accept)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd
from pandas import DataFrame

from strategies.mtf_sniper.market.data_guards import (
    REQUIRED_INFORMATIVE_COLS,
    data_ready_mask,
    finite_series_mask,
    has_invalid_numeric,
    missing_informative_columns,
    read_finite_float,
)


__all__ = [
    "DEFAULT_MARKET_SAFETY",
    "MarketSafetyConfig",
    "REQUIRED_INFORMATIVE_COLS",
    "data_ready_mask",
    "finite_series_mask",
    "has_invalid_numeric",
    "is_volatility_safe_row",
    "missing_informative_columns",
    "read_finite_float",
    "sideway_safe_mask",
    "volatility_safe_mask",
]


@dataclass(frozen=True)
class MarketSafetyConfig:
    """Parity with legacy active StrategyMTFSniper vol / chop filters."""

    block_high_vol: bool = True
    high_vol_pct: float = 90.0
    min_adx_4h: float = 18.0
    bb_width_4h_chop: float = 0.02
    bb_width_1d_chop: float = 0.10
    range_24h_chop_max: float = 0.022


DEFAULT_MARKET_SAFETY = MarketSafetyConfig()


def volatility_safe_mask(strategy: Any, dataframe: DataFrame) -> pd.Series:
    if strategy.block_high_vol.value and "atr_pct" in dataframe.columns:
        return dataframe["atr_pct"] < strategy.high_vol_pct.value
    return pd.Series(True, index=dataframe.index)


def sideway_safe_mask(strategy: Any, dataframe: DataFrame) -> pd.Series:
    cfg = MarketSafetyConfig(
        min_adx_4h=float(strategy.min_adx_4h.value),
    )
    is_chop_squeeze = pd.Series(False, index=dataframe.index)
    if "bb_width_4h" in dataframe.columns:
        is_chop_squeeze = is_chop_squeeze | (dataframe["bb_width_4h"] < cfg.bb_width_4h_chop)
    if "adx_4h" in dataframe.columns:
        is_chop_squeeze = is_chop_squeeze | (dataframe["adx_4h"] < cfg.min_adx_4h)
    if "bb_width_1d" in dataframe.columns and "range_24h" in dataframe.columns:
        is_chop_squeeze = is_chop_squeeze | (
            (dataframe["bb_width_1d"] < cfg.bb_width_1d_chop)
            & (dataframe["range_24h"] < cfg.range_24h_chop_max)
        )
    return ~is_chop_squeeze


def is_volatility_safe_row(
    row: Mapping[str, Any],
    config: MarketSafetyConfig | None = None,
) -> bool:
    cfg = config or DEFAULT_MARKET_SAFETY
    if not cfg.block_high_vol:
        return True
    atr_pct = row.get("atr_pct")
    if atr_pct is None:
        return True
    return float(atr_pct) < cfg.high_vol_pct


def is_sideway_safe_row(
    row: Mapping[str, Any],
    config: MarketSafetyConfig | None = None,
) -> bool:
    cfg = config or DEFAULT_MARKET_SAFETY
    if (
        "bb_width_4h" in row
        and row.get("bb_width_4h") is not None
        and float(row["bb_width_4h"]) < cfg.bb_width_4h_chop
    ):
        return False
    if (
        "adx_4h" in row
        and row.get("adx_4h") is not None
        and float(row["adx_4h"]) < cfg.min_adx_4h
    ):
        return False
    return not (
        row.get("bb_width_1d") is not None
        and row.get("range_24h") is not None
        and float(row["bb_width_1d"]) < cfg.bb_width_1d_chop
        and float(row["range_24h"]) < cfg.range_24h_chop_max
    )


def is_short_hunter_macro_ok(row: Mapping[str, Any]) -> bool:
    """Legacy: short Hunter only in clear bear macro, never in bull run."""
    close = float(row.get("close", 0.0) or 0.0)
    ema_50_1d = row.get("ema_50_1d")
    ema_fallback_high = close * 2.0 if close > 0 else 0.0
    ema_50 = float(ema_50_1d) if ema_50_1d is not None else ema_fallback_high

    is_bear = (
        float(row.get("is_macro_bear_market_1d", 0) or 0) == 1
        or close < ema_50
        or float(row.get("is_bear_wave_4h", 0) or 0) == 1
        or float(row.get("macdhist_4h", 0) or 0) < 0
    )
    is_bull = (
        float(row.get("is_macro_bull_run_1d", 0) or 0) == 1
        or (ema_50_1d is not None and close > float(ema_50_1d))
        or float(row.get("is_bull_wave_4h", 0) or 0) == 1
    )
    if float(row.get("is_macro_bull_run_1d", 0) or 0) == 1:
        return False
    return is_bear and not is_bull


def is_long_hunter_macro_ok(row: Mapping[str, Any]) -> bool:
    """Legacy: long Hunter only above EMA50/200 1D and not macro-bear."""
    close = float(row.get("close", 0.0) or 0.0)
    ema_50 = row.get("ema_50_1d")
    ema_200 = row.get("ema_200_1d")
    if ema_50 is None or ema_200 is None:
        return False
    if close <= float(ema_50) or close <= float(ema_200):
        return False
    return float(row.get("is_macro_bear_market_1d", 0) or 0) != 1


def is_pdh_safe_long_row(row: Mapping[str, Any]) -> bool:
    """Reject long chase into PDH ceiling (±0.3%)."""
    pdh = row.get("pdh_1d")
    if pdh is None:
        return False
    close = float(row.get("close", 0.0) or 0.0)
    pdh_f = float(pdh)
    if pdh_f <= 0:
        return False
    return not (close < pdh_f and close >= pdh_f * 0.997)
