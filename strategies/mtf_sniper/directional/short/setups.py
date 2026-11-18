"""Short setup identifiers and tag helpers."""
from __future__ import annotations

from enum import Enum
from typing import Any

import pandas as pd
from pandas import DataFrame


class ShortSetup(str, Enum):
    A80_PLUS = "short_A80plus"
    B65_PLUS = "short_B65plus"
    C_SUB65 = "short_C_sub65"
    WATERFALL_COLLAPSE = "jev_hunter_waterfall_collapse"
    SWING_BREAKOUT = "short_swing_breakout"
    UNKNOWN = "short_unknown"


def resolve_short_setup(primary_sensor: str, raw_features: dict) -> ShortSetup:
    """Map sensor lineage + short_score to entry_tag-compatible setup id."""
    from strategies.mtf_sniper.signals.composite_sensor import (
        candidate_includes_sensor,
        is_legacy_sniper_pass,
    )

    # 4a complete sniper recipe (or mono mask diagnostics)
    if is_legacy_sniper_pass(primary_sensor, raw_features, "short"):
        score = float(raw_features.get("short_score", 0.0) or 0.0)
        if score >= 80.0:
            return ShortSetup.A80_PLUS
        if score >= 65.0:
            return ShortSetup.B65_PLUS
        return ShortSetup.C_SUB65

    if primary_sensor == "waterfall_drop":
        return ShortSetup.WATERFALL_COLLAPSE
    if primary_sensor == "a80_plus":
        return ShortSetup.A80_PLUS
    if primary_sensor == "swing_breakout":
        return ShortSetup.SWING_BREAKOUT
    if primary_sensor in ("volume_surge", "volatility_expansion"):
        return ShortSetup.WATERFALL_COLLAPSE

    # Hunter-style composites built from anomaly atomics (no sniper mask child)
    if candidate_includes_sensor(primary_sensor, raw_features, "volume_surge") or (
        candidate_includes_sensor(primary_sensor, raw_features, "volatility_expansion")
        or candidate_includes_sensor(primary_sensor, raw_features, "waterfall_drop")
    ):
        return ShortSetup.WATERFALL_COLLAPSE

    if "short_score" in raw_features and not str(primary_sensor).startswith("composite_"):
        score = float(raw_features.get("short_score", 0.0) or 0.0)
        if score >= 80.0:
            return ShortSetup.A80_PLUS
        if score >= 65.0:
            return ShortSetup.B65_PLUS
        return ShortSetup.C_SUB65
    return ShortSetup.UNKNOWN


def quality_grade_from_score(score: float) -> str:
    if score >= 0.90:
        return "A+"
    if score >= 0.80:
        return "A"
    if score >= 0.65:
        return "B"
    return "REJECT"



# Stable part ids — SetupComposer recipe sniper_short = ALL of these
SHORT_SNIPER_PART_NAMES = (
    "short_structure",
    "short_mtf",
    "short_macro_floor",
    "short_safety",
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
    """
    Legacy short AND-mask split into named parts (old StrategyMTFSniper parity).

    Full mask == AND of every part. Parts are independently composable.
    """
    s_max_dist = pair_cfg.get("short_max_dist_ema21", strategy.short_max_dist_ema21.value)
    s_body_ratio = pair_cfg.get("short_candle_body_ratio_min", strategy.short_candle_body_ratio_min.value)

    is_red = dataframe["close"] < dataframe["open"]
    is_decisive_red = (
        is_red
        & ((dataframe["body"] / dataframe["total_range"]) >= s_body_ratio)
        & (dataframe["body"] >= dataframe["atr"] * 0.5)
    )
    is_breakdown_major = (dataframe["close"] < dataframe["swing_low"]) & (
        dataframe["close"].shift(1) >= dataframe["swing_low"]
    )
    is_below_emas = (dataframe["close"] < dataframe["ema_10"]) & (dataframe["close"] < dataframe["ema_21"])

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
        (dataframe["close_1h"] >= (dataframe["ema_50_1h"] * strategy.short_loc_tolerance_1h.value))
        & (dataframe["close_1h"] <= (dataframe["ema_50_1h"] * 1.025))
    )
    cond_1h_short_macd = ((dataframe["macdhist_1h"] - dataframe["macdhist_prev_1h"]) < 0) | (
        dataframe["macdhist_1h"] < 0
    )
    cond_1h_short_exhaustion = dataframe["rsi_max_24_1h"] >= strategy.short_min_1h_rsi_exhaustion.value
    cond_1h_short = cond_1h_short_pos & cond_1h_short_macd & cond_1h_short_exhaustion

    cond_4h_short = (
        (dataframe["is_bull_wave_4h"].fillna(0) == 0)
        if strategy.block_4h_counter_wave.value
        else pd.Series(True, index=dataframe.index)
    )
    if pair.startswith("ETH") and "minus_di_4h" in dataframe.columns and "plus_di_4h" in dataframe.columns:
        cond_4h_di_short = dataframe["minus_di_4h"] > dataframe["plus_di_4h"]
    else:
        cond_4h_di_short = pd.Series(True, index=dataframe.index)

    cond_1d_short = (
        (dataframe["is_bull_wave_1d"].fillna(0) == 0)
        if (strategy.block_1d_counter_wave.value and "is_bull_wave_1d" in dataframe.columns)
        else pd.Series(True, index=dataframe.index)
    )

    is_in_macro_bull = (
        (dataframe["close"] > dataframe["ema_50_1d"]) & (dataframe["close"] > dataframe["ema_200_1d"])
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
        ((dataframe["close"] - dataframe["swing_low_20d_1d"]) / dataframe["swing_low_20d_1d"]) >= 0.035
        if "swing_low_20d_1d" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )
    cond_capitulation_safe = (
        (dataframe["is_deep_capitulation_1d"].fillna(0) == 0)
        if "is_deep_capitulation_1d" in dataframe.columns
        else pd.Series(False, index=dataframe.index)
    )

    cond_not_chasing_short = (
        ((dataframe["ema_21"] - dataframe["close"]) / dataframe["close"] <= (s_max_dist / 100.0))
        & (dataframe["rsi"] >= strategy.short_min_rsi_entry.value)
    )

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


def build_raw_sniper_short_mask(
    strategy: Any,
    dataframe: DataFrame,
    pair: str,
    pair_cfg: dict,
    *,
    data_ready: pd.Series,
    cond_volatility_safe: pd.Series,
    cond_sideway_safe: pd.Series,
) -> pd.Series:
    """Full legacy short mask == AND of ``build_short_sniper_parts``."""
    parts = build_short_sniper_parts(
        strategy,
        dataframe,
        pair,
        pair_cfg,
        data_ready=data_ready,
        cond_volatility_safe=cond_volatility_safe,
        cond_sideway_safe=cond_sideway_safe,
    )
    mask = parts[SHORT_SNIPER_PART_NAMES[0]]
    for name in SHORT_SNIPER_PART_NAMES[1:]:
        mask = mask & parts[name]
    return mask


def tag_short_sniper(dataframe: DataFrame, raw_mask: pd.Series) -> pd.Series:
    tags = pd.Series("", index=dataframe.index)
    for idx in dataframe.index[raw_mask]:
        sc = dataframe.loc[idx, "short_score"]
        if sc >= 80:
            tier = ShortSetup.A80_PLUS.value
        elif sc >= 65:
            tier = ShortSetup.B65_PLUS.value
        else:
            tier = ShortSetup.C_SUB65.value
        tags.loc[idx] = tier
    return tags
