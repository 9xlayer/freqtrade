"""Long setup identifiers and tag helpers."""
from __future__ import annotations

from enum import Enum
from typing import Any, Tuple

import pandas as pd
from pandas import DataFrame


class LongSetup(str, Enum):
    A80_PLUS = "long_A80plus"
    BREAKOUT_EXPANSION = "jev_hunter_breakout_expansion"
    SWING_BREAKOUT = "long_swing_breakout"
    UNKNOWN = "long_unknown"


def resolve_long_setup(primary_sensor: str, raw_features: dict) -> LongSetup:
    """Map sensor lineage to a stable entry_tag-compatible setup id."""
    if primary_sensor == "a80_plus" or "long_score" in raw_features:
        score = float(raw_features.get("long_score", 0.0) or 0.0)
        if score >= 80.0 or primary_sensor == "a80_plus":
            return LongSetup.A80_PLUS
    if primary_sensor in ("volume_surge", "volatility_expansion", "swing_breakout"):
        # Hunter-style breakout lineage; final tag applied after JEV in P2/P3
        if primary_sensor == "swing_breakout":
            return LongSetup.SWING_BREAKOUT
        return LongSetup.BREAKOUT_EXPANSION
    return LongSetup.UNKNOWN


def quality_grade_from_score(score: float) -> str:
    if score >= 0.90:
        return "A+"
    if score >= 0.80:
        return "A"
    if score >= 0.65:
        return "B"
    return "REJECT"



def build_raw_sniper_long_mask(
    strategy: Any,
    dataframe: DataFrame,
    pair_cfg: dict,
    *,
    data_ready: pd.Series,
    cond_volatility_safe: pd.Series,
    cond_sideway_safe: pd.Series,
) -> Tuple[pd.Series, pd.Series]:
    """Return (raw_long_mask, pdh_safe_mask) matching active StrategyMTFSniper."""
    l_max_dist = pair_cfg.get("long_max_dist_ema21", strategy.long_max_dist_ema21.value)
    l_body_ratio = pair_cfg.get("long_candle_body_ratio_min", strategy.long_candle_body_ratio_min.value)

    is_green = dataframe["close"] > dataframe["open"]
    is_decisive_green = (
        is_green
        & ((dataframe["body"] / dataframe["total_range"]) >= l_body_ratio)
        & (dataframe["body"] >= dataframe["atr"] * 0.5)
    )
    l_lookback = strategy.long_lookback_resistance.value
    swing_high = dataframe["high"].shift(1).rolling(l_lookback).max()
    is_breakout_major = (dataframe["close"] > swing_high) & (dataframe["close"].shift(1) <= swing_high)
    is_above_emas = (dataframe["close"] > dataframe["ema_10"]) & (dataframe["close"] > dataframe["ema_21"])

    cond_1h_long_pos = dataframe["close_1h"] >= (dataframe["ema_50_1h"] * strategy.long_loc_tolerance_1h.value)
    cond_1h_long_exhaustion = dataframe["rsi_min_24_1h"] <= strategy.long_max_1h_rsi_exhaustion.value
    cond_1h_long = cond_1h_long_pos & cond_1h_long_exhaustion

    cond_4h_long = (
        (dataframe["macdhist_4h"] > 0)
        if "macdhist_4h" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )

    cond_1d_long = (
        (dataframe["close"] > dataframe["ema_50_1d"])
        & ((dataframe["macdhist_1d"] > 0) if strategy.long_block_1d_macd.value else True)
    ) if ("ema_50_1d" in dataframe.columns and "macdhist_1d" in dataframe.columns) else pd.Series(
        False, index=dataframe.index
    )

    is_in_macro_bear = (
        (dataframe["close"] < dataframe["ema_200_1d"]) | (dataframe["close"] < dataframe["ema_50_1d"])
        if ("ema_50_1d" in dataframe.columns and "ema_200_1d" in dataframe.columns)
        else pd.Series(False, index=dataframe.index)
    )
    cond_macro_safe_long = ~is_in_macro_bear & (
        (dataframe["is_macro_bear_market_1d"].fillna(0) == 0)
        if "is_macro_bear_market_1d" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )

    cond_overextended_safe = (
        dataframe["dist_from_ema_200_1d"] <= strategy.long_overextended_dist_max.value
        if "dist_from_ema_200_1d" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )

    cond_not_chasing_long = (
        ((dataframe["close"] - dataframe["ema_21"]) / dataframe["ema_21"] <= (l_max_dist / 100.0))
        & (dataframe["rsi"] <= strategy.long_max_rsi_entry.value)
    )

    cond_candle_shape_long = (
        (dataframe["upper_wick_ratio"] <= strategy.max_rejection_wick_ratio.value)
        & (dataframe["close_pos_ratio"] >= strategy.min_close_pos_ratio_long.value)
        if (
            strategy.filter_wick_rejection.value
            and "upper_wick_ratio" in dataframe.columns
            and "close_pos_ratio" in dataframe.columns
        )
        else pd.Series(True, index=dataframe.index)
    )

    if "pdh_1d" in dataframe.columns and strategy.filter_pdh_ceiling.value:
        cond_pdh_safe_long = ~(
            (dataframe["close"] < dataframe["pdh_1d"])
            & (dataframe["close"] >= dataframe["pdh_1d"] * 0.997)
        )
    elif not strategy.filter_pdh_ceiling.value:
        cond_pdh_safe_long = pd.Series(True, index=dataframe.index)
    else:
        cond_pdh_safe_long = pd.Series(False, index=dataframe.index)

    cond_score_long = (
        (dataframe["long_score"] >= 80)
        if "long_score" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )

    raw = (
        is_decisive_green
        & is_breakout_major
        & is_above_emas
        & cond_1h_long
        & cond_4h_long
        & cond_1d_long
        & cond_macro_safe_long
        & cond_overextended_safe
        & cond_sideway_safe
        & cond_not_chasing_long
        & cond_volatility_safe
        & cond_candle_shape_long
        & cond_pdh_safe_long
        & cond_score_long
        & (dataframe["volume"] > 0)
        & data_ready
    )
    return raw, cond_pdh_safe_long


def tag_long_sniper(dataframe: DataFrame, raw_mask: pd.Series) -> pd.Series:
    tags = pd.Series("", index=dataframe.index)
    for idx in dataframe.index[raw_mask]:
        tags.loc[idx] = LongSetup.A80_PLUS.value
    return tags
