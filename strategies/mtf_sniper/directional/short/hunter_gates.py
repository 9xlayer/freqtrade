"""
Short Hunter (waterfall_collapse) acceptance gates.

Extracted from hunter_merge for parity with long/hunter_gates.py.
Defaults preserve the live short-hunter behavior that contributes positively
in baseline backtests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ShortHunterGateConfig:
    min_confidence: float = 0.85
    max_lower_wick_ratio: float = 0.285
    max_close_pos_ratio: float = 0.35
    max_dist_ema21_below: float = 0.015  # allow up to 1.5% below EMA21
    min_rsi: float = 24.0
    floor_dist_min: float = 0.035


DEFAULT_SHORT_HUNTER_GATES = ShortHunterGateConfig()


def evaluate_short_hunter_waterfall(
    row: Mapping[str, Any],
    *,
    confidence: float,
    vol_safe: bool,
    sideway_safe: bool,
    config: ShortHunterGateConfig | None = None,
) -> tuple[bool, list[str]]:
    """
    Return (accepted, rejection_reasons).

    Bear-macro / anti-bull checks are done by the caller before invoking this.
    """
    cfg = config or DEFAULT_SHORT_HUNTER_GATES
    reasons: list[str] = []

    if confidence < cfg.min_confidence:
        reasons.append("CONF_BELOW_THRESHOLD")
    if not vol_safe:
        reasons.append("VOLATILITY_UNSAFE")
    if not sideway_safe:
        reasons.append("SIDEWAY_UNSAFE")

    lower_wick = float(row.get("lower_wick_ratio", 0.0) or 0.0)
    close_pos = float(row.get("close_pos_ratio", 0.0) or 0.0)
    if lower_wick > cfg.max_lower_wick_ratio:
        reasons.append("LOWER_WICK_REJECTION")
    if close_pos > cfg.max_close_pos_ratio:
        reasons.append("CLOSE_POS_WEAK")

    close = float(row.get("close", 0.0) or 0.0)
    ema_21 = row.get("ema_21")
    if ema_21 is not None and float(ema_21) > 0:
        dist_ema21 = (close - float(ema_21)) / float(ema_21)
        if dist_ema21 < -cfg.max_dist_ema21_below:
            reasons.append("EMA21_EXHAUSTED")
    else:
        reasons.append("EMA21_MISSING")

    rsi = float(row.get("rsi", 50.0) or 50.0)
    if rsi < cfg.min_rsi:
        reasons.append("RSI_OVERSOLD")

    sw_low_20d = row.get("swing_low_20d_1d", 0.0)
    if sw_low_20d and float(sw_low_20d) > 0:
        floor_dist = (close - float(sw_low_20d)) / float(sw_low_20d)
        floor_safe = (floor_dist >= cfg.floor_dist_min) or (floor_dist < 0.0)
        if not floor_safe:
            reasons.append("FLOOR_UNSAFE")
    else:
        reasons.append("FLOOR_MISSING")

    return (len(reasons) == 0, reasons)
