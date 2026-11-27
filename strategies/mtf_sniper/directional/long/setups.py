"""Long setup identifiers and tag helpers."""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame

from strategies.mtf_sniper.signals.sniper_part_masks import (
    LONG_SNIPER_PART_NAMES,
    build_long_sniper_parts,
)


__all__ = [
    "LONG_SNIPER_PART_NAMES",
    "LongSetup",
    "build_long_sniper_parts",
    "build_raw_sniper_long_mask",
    "quality_grade_from_score",
    "resolve_long_setup",
    "tag_long_sniper",
]


class LongSetup(str, Enum):
    A80_PLUS = "long_A80_plus"
    BREAKOUT_EXPANSION = "jev_hunter_breakout_expansion"
    SWING_BREAKOUT = "long_swing_breakout"
    UNKNOWN = "long_unknown"


def resolve_long_setup(primary_sensor: str, raw_features: dict) -> LongSetup:
    """Map sensor lineage to a stable entry_tag-compatible setup id."""
    from strategies.mtf_sniper.signals.composite_sensor import (
        candidate_includes_sensor,
        is_legacy_sniper_pass,
    )

    if is_legacy_sniper_pass(primary_sensor, raw_features, "long"):
        return LongSetup.A80_PLUS
    if primary_sensor == "a80_plus":
        return LongSetup.A80_PLUS
    if primary_sensor == "swing_breakout":
        return LongSetup.SWING_BREAKOUT
    if primary_sensor in ("volume_surge", "volatility_expansion"):
        return LongSetup.BREAKOUT_EXPANSION
    if candidate_includes_sensor(primary_sensor, raw_features, "volume_surge") or (
        candidate_includes_sensor(primary_sensor, raw_features, "volatility_expansion")
    ):
        return LongSetup.BREAKOUT_EXPANSION
    if "long_score" in raw_features and primary_sensor not in (
        "volume_surge",
        "volatility_expansion",
        "waterfall_drop",
        "waterfall_momentum",
    ):
        score = float(raw_features.get("long_score", 0.0) or 0.0)
        if score >= 80.0:
            return LongSetup.A80_PLUS
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
) -> tuple[pd.Series, pd.Series]:
    """Full legacy long mask == AND of ``build_long_sniper_parts``."""
    parts, cond_pdh_safe_long = build_long_sniper_parts(
        strategy,
        dataframe,
        pair_cfg,
        data_ready=data_ready,
        cond_volatility_safe=cond_volatility_safe,
        cond_sideway_safe=cond_sideway_safe,
    )
    mask = parts[LONG_SNIPER_PART_NAMES[0]]
    for name in LONG_SNIPER_PART_NAMES[1:]:
        mask = mask & parts[name]
    return mask, cond_pdh_safe_long


def tag_long_sniper(dataframe: DataFrame, raw_mask: pd.Series) -> pd.Series:
    arr = np.full(len(dataframe), "", dtype=object)
    positions = np.flatnonzero(raw_mask.to_numpy(dtype=bool, na_value=False))
    if len(positions):
        arr[positions] = LongSetup.A80_PLUS.value
    return pd.Series(arr, index=dataframe.index)
