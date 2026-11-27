"""Atomic boolean Sniper part-mask builders owned by the Signals layer."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pandas import DataFrame


SHORT_SNIPER_PART_NAMES = (
    "short_structure",
    "short_mtf",
    "short_macro_floor",
    "short_safety",
)

LONG_SNIPER_PART_NAMES = (
    "long_structure",
    "long_mtf",
    "long_macro",
    "long_safety",
)


def build_short_sniper_parts(
    strategy: Any,
    dataframe: DataFrame,
    pair: str,
    pair_cfg: dict,
    *,
    data_ready: pd.Series,
    cond_volatility_safe: pd.Series,
    cond_sideway_safe: pd.Series,
) -> dict[str, pd.Series]:
    """Compute named boolean part masks for the Short Sniper setup."""
    s_max_dist = pair_cfg.get("short_max_dist_ema21", strategy.short_max_dist_ema21.value)
    s_body_ratio = pair_cfg.get(
        "short_candle_body_ratio_min", strategy.short_candle_body_ratio_min.value
    )

    is_red = dataframe["close"] < dataframe["open"]
    is_decisive_red = (
        is_red
        & ((dataframe["body"] / dataframe["total_range"]) >= s_body_ratio)
        & (dataframe["body"] >= dataframe["atr"] * 0.5)
    )
    is_breakdown_major = (dataframe["close"] < dataframe["swing_low"]) & (
        dataframe["close"].shift(1) >= dataframe["swing_low"]
    )
    is_below_emas = (dataframe["close"] < dataframe["ema_10"]) & (
        dataframe["close"] < dataframe["ema_21"]
    )

    cond_candle_shape_short = (
        (dataframe["lower_wick_ratio"] <= strategy.max_rejection_wick_ratio.value)
        & (dataframe["close_pos_ratio"] <= strategy.max_close_pos_ratio_short.value)
        if (
            strategy.filter_wick_rejection.value
            and "lower_wick_ratio" in dataframe.columns
            and "close_pos_ratio" in dataframe.columns
        )
        else pd.Series(True, index=dataframe.index)
    )

    cond_1h_short_pos = (
        dataframe["close_1h"] >= (dataframe["ema_50_1h"] * strategy.short_loc_tolerance_1h.value)
    ) & (dataframe["close_1h"] <= (dataframe["ema_50_1h"] * 1.025))
    cond_1h_short_macd = ((dataframe["macdhist_1h"] - dataframe["macdhist_prev_1h"]) < 0) | (
        dataframe["macdhist_1h"] < 0
    )
    cond_1h_short_exhaustion = (
        dataframe["rsi_max_24_1h"] >= strategy.short_min_1h_rsi_exhaustion.value
    )
    cond_1h_short = cond_1h_short_pos & cond_1h_short_macd & cond_1h_short_exhaustion

    cond_4h_short = (
        (dataframe["is_bull_wave_4h"].fillna(0) == 0)
        if strategy.block_4h_counter_wave.value
        else pd.Series(True, index=dataframe.index)
    )
    if (
        pair.startswith("ETH")
        and "minus_di_4h" in dataframe.columns
        and "plus_di_4h" in dataframe.columns
    ):
        cond_4h_di_short = dataframe["minus_di_4h"] > dataframe["plus_di_4h"]
    else:
        cond_4h_di_short = pd.Series(True, index=dataframe.index)

    cond_1d_short = (
        (dataframe["is_bull_wave_1d"].fillna(0) == 0)
        if (strategy.block_1d_counter_wave.value and "is_bull_wave_1d" in dataframe.columns)
        else pd.Series(True, index=dataframe.index)
    )

    is_in_macro_bull = (
        (dataframe["close"] > dataframe["ema_50_1d"])
        & (dataframe["close"] > dataframe["ema_200_1d"])
        if ("ema_50_1d" in dataframe.columns and "ema_200_1d" in dataframe.columns)
        else pd.Series(False, index=dataframe.index)
    )
    macro_bull_guard = (
        ~is_in_macro_bull
        if getattr(strategy, "block_macro_bull_shorts", False)
        else pd.Series(True, index=dataframe.index)
    )
    cond_macro_safe_short = macro_bull_guard & (
        (dataframe["is_macro_bull_run_1d"].fillna(0) == 0)
        if "is_macro_bull_run_1d" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )
    cond_floor_safe = (
        ((dataframe["close"] - dataframe["swing_low_20d_1d"]) / dataframe["swing_low_20d_1d"])
        >= 0.035
        if "swing_low_20d_1d" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )
    cond_capitulation_safe = (
        (dataframe["is_deep_capitulation_1d"].fillna(0) == 0)
        if "is_deep_capitulation_1d" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )

    cond_not_chasing_short = (
        (dataframe["ema_21"] - dataframe["close"]) / dataframe["close"] <= (s_max_dist / 100.0)
    ) & (dataframe["rsi"] >= strategy.short_min_rsi_entry.value)

    return {
        "short_structure": (
            is_decisive_red
            & is_breakdown_major
            & is_below_emas
            & cond_candle_shape_short
            & (dataframe["volume"] > 0)
        ),
        "short_mtf": cond_1h_short & cond_4h_short & cond_4h_di_short & cond_1d_short,
        "short_macro_floor": cond_macro_safe_short & cond_floor_safe & cond_capitulation_safe,
        "short_safety": (
            cond_sideway_safe & cond_volatility_safe & cond_not_chasing_short & data_ready
        ),
    }


def build_long_sniper_parts(
    strategy: Any,
    dataframe: DataFrame,
    pair_cfg: dict,
    *,
    data_ready: pd.Series,
    cond_volatility_safe: pd.Series,
    cond_sideway_safe: pd.Series,
) -> tuple[dict[str, pd.Series], pd.Series]:
    """Compute named boolean part masks and PDH safety mask for the Long Sniper setup."""
    l_max_dist = pair_cfg.get("long_max_dist_ema21", strategy.long_max_dist_ema21.value)
    l_body_ratio = pair_cfg.get(
        "long_candle_body_ratio_min", strategy.long_candle_body_ratio_min.value
    )

    is_green = dataframe["close"] > dataframe["open"]
    is_decisive_green = (
        is_green
        & ((dataframe["body"] / dataframe["total_range"]) >= l_body_ratio)
        & (dataframe["body"] >= dataframe["atr"] * 0.5)
    )
    l_lookback = strategy.long_lookback_resistance.value
    swing_high = dataframe["high"].shift(1).rolling(l_lookback).max()
    is_breakout_major = (dataframe["close"] > swing_high) & (
        dataframe["close"].shift(1) <= swing_high
    )
    is_above_emas = (dataframe["close"] > dataframe["ema_10"]) & (
        dataframe["close"] > dataframe["ema_21"]
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

    cond_1h_long_pos = dataframe["close_1h"] >= (
        dataframe["ema_50_1h"] * strategy.long_loc_tolerance_1h.value
    )
    cond_1h_long_exhaustion = (
        dataframe["rsi_min_24_1h"] <= strategy.long_max_1h_rsi_exhaustion.value
    )
    cond_1h_long = cond_1h_long_pos & cond_1h_long_exhaustion

    cond_4h_long = (
        (dataframe["macdhist_4h"] > 0)
        if "macdhist_4h" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )

    cond_1d_long = (
        (
            (dataframe["close"] > dataframe["ema_50_1d"])
            & ((dataframe["macdhist_1d"] > 0) if strategy.long_block_1d_macd.value else True)
        )
        if ("ema_50_1d" in dataframe.columns and "macdhist_1d" in dataframe.columns)
        else pd.Series(False, index=dataframe.index)
    )

    is_in_macro_bear = (
        (dataframe["close"] < dataframe["ema_200_1d"])
        | (dataframe["close"] < dataframe["ema_50_1d"])
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
        (dataframe["close"] - dataframe["ema_21"]) / dataframe["ema_21"] <= (l_max_dist / 100.0)
    ) & (dataframe["rsi"] <= strategy.long_max_rsi_entry.value)

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

    parts = {
        "long_structure": (
            is_decisive_green
            & is_breakout_major
            & is_above_emas
            & cond_candle_shape_long
            & (dataframe["volume"] > 0)
        ),
        "long_mtf": cond_1h_long & cond_4h_long & cond_1d_long,
        "long_macro": cond_macro_safe_long & cond_overextended_safe,
        "long_safety": (
            cond_sideway_safe
            & cond_volatility_safe
            & cond_not_chasing_long
            & cond_pdh_safe_long
            & cond_score_long
            & data_ready
        ),
    }
    return parts, cond_pdh_safe_long
