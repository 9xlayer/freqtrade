"""Shared market gates (series masks and numeric guards)."""

from __future__ import annotations

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
