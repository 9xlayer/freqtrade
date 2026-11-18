"""
Long Hunter (breakout_expansion) acceptance gates.

Stricter than the legacy merge thresholds: evidence showed ~91 trades / −702 USDT
with many losers having MFE < 1% (chase / no follow-through).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class LongHunterGateConfig:
    min_confidence: float = 0.90
    max_upper_wick_ratio: float = 0.20
    min_close_pos_ratio: float = 0.72
    max_dist_ema21: float = 0.008  # 0.8% — reject chase
    max_rsi: float = 65.0
    max_dist_ema200_1d: float = 0.45
    min_body_ratio: float = 0.55
    require_htf_bull_momentum: bool = True
    min_long_score: float = 50.0  # soft floor when score column present


DEFAULT_LONG_HUNTER_GATES = LongHunterGateConfig()


def evaluate_long_hunter_breakout(
    row: Mapping[str, Any],
    *,
    confidence: float,
    pdh_safe: bool,
    vol_safe: bool,
    sideway_safe: bool,
    config: Optional[LongHunterGateConfig] = None,
) -> tuple[bool, list[str]]:
    """
    Return (accepted, rejection_reasons).

    Macro bull / enable_hunter_long are checked by the caller before invoking this.
    """
    cfg = config or DEFAULT_LONG_HUNTER_GATES
    reasons: list[str] = []

    if confidence < cfg.min_confidence:
        reasons.append("CONF_BELOW_THRESHOLD")

    if not vol_safe:
        reasons.append("VOLATILITY_UNSAFE")
    if not sideway_safe:
        reasons.append("SIDEWAY_UNSAFE")
    if not pdh_safe:
        reasons.append("PDH_CEILING")

    upper_wick = float(row.get("upper_wick_ratio", 0.0) or 0.0)
    close_pos = float(row.get("close_pos_ratio", 0.0) or 0.0)
    if upper_wick > cfg.max_upper_wick_ratio:
        reasons.append("UPPER_WICK_REJECTION")
    if close_pos < cfg.min_close_pos_ratio:
        reasons.append("CLOSE_POS_WEAK")

    close = float(row.get("close", 0.0) or 0.0)
    open_ = float(row.get("open", close) or close)
    high = float(row.get("high", close) or close)
    low = float(row.get("low", close) or close)
    total_range = max(high - low, 1e-12)
    body_ratio = abs(close - open_) / total_range
    if close <= open_:
        reasons.append("NOT_BULLISH_CANDLE")
    if body_ratio < cfg.min_body_ratio:
        reasons.append("BODY_TOO_SMALL")

    ema_21 = row.get("ema_21")
    if ema_21 is not None and float(ema_21) > 0:
        dist_ema21 = (close - float(ema_21)) / float(ema_21)
        if dist_ema21 > cfg.max_dist_ema21:
            reasons.append("EMA21_CHASE")
        if close < float(ema_21):
            reasons.append("BELOW_EMA21")
    else:
        reasons.append("EMA21_MISSING")

    rsi = float(row.get("rsi", 50.0) or 50.0)
    if rsi > cfg.max_rsi:
        reasons.append("RSI_OVEREXTENDED")

    if "dist_from_ema_200_1d" in row and row.get("dist_from_ema_200_1d") is not None:
        if float(row["dist_from_ema_200_1d"]) > cfg.max_dist_ema200_1d:
            reasons.append("EMA200_1D_OVEREXTENDED")

    if cfg.require_htf_bull_momentum:
        macd_4h = float(row.get("macdhist_4h", 0.0) or 0.0)
        bull_wave_4h = float(row.get("is_bull_wave_4h", 0) or 0) == 1
        if not (macd_4h > 0 or bull_wave_4h):
            reasons.append("HTF_MOMENTUM_WEAK")

    if "long_score" in row and row.get("long_score") is not None:
        if float(row["long_score"]) < cfg.min_long_score:
            reasons.append("LONG_SCORE_FLOOR")

    return (len(reasons) == 0, reasons)
