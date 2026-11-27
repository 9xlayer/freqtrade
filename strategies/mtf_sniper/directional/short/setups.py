"""Short setup identifiers and tag helpers."""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame

from strategies.mtf_sniper.signals.sniper_part_masks import (
    SHORT_SNIPER_PART_NAMES,
    build_short_sniper_parts,
)


__all__ = [
    "SHORT_SNIPER_PART_NAMES",
    "ShortSetup",
    "build_raw_sniper_short_mask",
    "build_short_sniper_parts",
    "quality_grade_from_score",
    "resolve_short_setup",
    "tag_short_sniper",
]


class ShortSetup(Enum):
    A80_PLUS = "short_A80_plus"
    B65_PLUS = "short_B65_plus"
    C_SUB65 = "short_C_sub65"
    WATERFALL_COLLAPSE = "waterfall_collapse"
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

    if primary_sensor in ("waterfall_drop", "waterfall_momentum"):
        return ShortSetup.WATERFALL_COLLAPSE
    if primary_sensor == "a80_plus":
        return ShortSetup.A80_PLUS
    if primary_sensor == "swing_breakout":
        return ShortSetup.SWING_BREAKOUT
    if primary_sensor in ("volume_surge", "volatility_expansion"):
        return ShortSetup.WATERFALL_COLLAPSE

    if candidate_includes_sensor(primary_sensor, raw_features, "volume_surge") or (
        candidate_includes_sensor(primary_sensor, raw_features, "volatility_expansion")
        or candidate_includes_sensor(primary_sensor, raw_features, "waterfall_drop")
        or candidate_includes_sensor(primary_sensor, raw_features, "waterfall_momentum")
    ):
        return ShortSetup.WATERFALL_COLLAPSE

    if "short_score" in raw_features and not primary_sensor.startswith("composite_"):
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
    arr = np.full(len(dataframe), "", dtype=object)
    positions = np.flatnonzero(raw_mask.to_numpy(dtype=bool, na_value=False))
    scores = dataframe["short_score"].to_numpy() if "short_score" in dataframe.columns else None
    for pos in positions:
        sc = float(scores[pos]) if scores is not None else 0.0
        if sc >= 80:
            arr[pos] = ShortSetup.A80_PLUS.value
        elif sc >= 65:
            arr[pos] = ShortSetup.B65_PLUS.value
        else:
            arr[pos] = ShortSetup.C_SUB65.value
    return pd.Series(arr, index=dataframe.index)
