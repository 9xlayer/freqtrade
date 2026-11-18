"""Shared market gates used by sniper long/short mask builders."""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pandas as pd
from pandas import DataFrame

REQUIRED_INFORMATIVE_COLS = [
    "close_1h", "ema_50_1h", "macdhist_1h", "macdhist_prev_1h",
    "rsi_max_24_1h", "rsi_min_24_1h", "atr_pct",
    "macdhist_4h", "ema_50_1d", "macdhist_1d", "pdh_1d",
]


def missing_informative_columns(dataframe: DataFrame) -> List[str]:
    return [c for c in REQUIRED_INFORMATIVE_COLS if c not in dataframe.columns]


def data_ready_mask(dataframe: DataFrame) -> pd.Series:
    return dataframe[REQUIRED_INFORMATIVE_COLS].notna().all(axis=1)


def volatility_safe_mask(strategy: Any, dataframe: DataFrame) -> pd.Series:
    if strategy.block_high_vol.value and "atr_pct" in dataframe.columns:
        return dataframe["atr_pct"] < strategy.high_vol_pct.value
    return pd.Series(True, index=dataframe.index)


def sideway_safe_mask(strategy: Any, dataframe: DataFrame) -> pd.Series:
    is_chop_squeeze = pd.Series(False, index=dataframe.index)
    if "bb_width_4h" in dataframe.columns:
        is_chop_squeeze = is_chop_squeeze | (dataframe["bb_width_4h"] < 0.02)
    if "adx_4h" in dataframe.columns:
        is_chop_squeeze = is_chop_squeeze | (dataframe["adx_4h"] < strategy.min_adx_4h.value)
    if "bb_width_1d" in dataframe.columns and "range_24h" in dataframe.columns:
        is_chop_squeeze = is_chop_squeeze | (
            (dataframe["bb_width_1d"] < 0.10) & (dataframe["range_24h"] < 0.022)
        )
    return ~is_chop_squeeze
