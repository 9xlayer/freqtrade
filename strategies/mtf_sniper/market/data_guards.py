"""Pure DataFrame readiness and finite-numeric guard helpers (Market layer)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame


REQUIRED_INFORMATIVE_COLS = [
    "close_1h",
    "ema_50_1h",
    "macdhist_1h",
    "macdhist_prev_1h",
    "rsi_max_24_1h",
    "rsi_min_24_1h",
    "atr_pct",
    "macdhist_4h",
    "ema_50_1d",
    "macdhist_1d",
    "pdh_1d",
]


def missing_informative_columns(dataframe: DataFrame) -> list[str]:
    return [c for c in REQUIRED_INFORMATIVE_COLS if c not in dataframe.columns]


def data_ready_mask(dataframe: DataFrame) -> pd.Series:
    return dataframe[REQUIRED_INFORMATIVE_COLS].notna().all(axis=1)


def finite_series_mask(dataframe: DataFrame, cols: Sequence[str]) -> pd.Series:
    """True on bars where every present column in ``cols`` holds a finite number."""
    arr = np.ones(len(dataframe), dtype=bool)
    for col in cols:
        if col in dataframe.columns:
            vals = pd.to_numeric(dataframe[col], errors="coerce").to_numpy(
                dtype=float, na_value=np.nan
            )
            arr &= np.isfinite(vals)
    return pd.Series(arr, index=dataframe.index)


def has_invalid_numeric(row: Mapping[str, Any], keys: Sequence[str]) -> bool:
    """True when any present non-None field in ``keys`` is non-finite (NaN/Inf/bad)."""
    for key in keys:
        if key not in row:
            continue
        raw = row[key]
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return True
        if not math.isfinite(val):
            return True
    return False


def read_finite_float(row: Mapping[str, Any], key: str, default: float) -> float:
    """Return finite float value from ``row[key]``, or ``default`` when absent/None/invalid."""
    raw = row.get(key)
    if raw is None:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if math.isfinite(val) else default
