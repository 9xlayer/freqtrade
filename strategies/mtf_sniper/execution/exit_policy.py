"""
Exit policy helpers for custom_exit and confirm_trade_exit.

Pure rules over a market row + trade state. Strategy only resolves the row.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional


ALWAYS_ALLOW_EXIT_REASONS = frozenset(
    ("stop_loss", "trailing_stop_loss", "emergency_exit", "force_exit")
)


def resolve_market_row(
    dataframe: Any,
    current_time: datetime,
) -> Optional[Mapping[str, Any]]:
    """Pick the latest analyzed bar at or before current_time."""
    if dataframe is None or getattr(dataframe, "empty", True):
        return None
    row = dataframe.loc[dataframe["date"] == current_time]
    if row.empty:
        matched = dataframe.loc[dataframe["date"] <= current_time]
        if matched.empty:
            return None
        return matched.iloc[-1]
    return row.iloc[0]


def evaluate_custom_exit(
    *,
    trade: Any,
    current_time: datetime,
    current_profit: float,
    row: Mapping[str, Any],
) -> Optional[str]:
    """
    Return an exit reason string or None.

    Rules (parity with baseline):
    1. After >= 3h and ROE < -8%: structure break + HTF reversal
    2. ROE >= 35%: HTF trend-runner broken vs EMA21 1H
    3. Hold >= 24h and ROE < 35%: timeout when price loses EMA21 or profit < 1.5%
    """
    duration_minutes = (current_time - trade.open_date_utc).total_seconds() / 60
    is_short = bool(trade.is_short)

    if duration_minutes >= 180 and current_profit < -0.08:
        if is_short:
            structure_broken = row.get("close", 0) > row.get("swing_high_12", 0)
            htf_reversal = (row.get("close_1h", 0) > row.get("ema_50_1h", 0)) and (
                row.get("macdhist_1h", 0) > 0
            )
            if structure_broken and htf_reversal:
                return "exit_short_trend_invalidated"
        else:
            structure_broken = row.get("close", 0) < row.get("swing_low_12", 0)
            htf_reversal = (row.get("close_1h", 0) < row.get("ema_50_1h", 0)) and (
                row.get("macdhist_1h", 0) < 0
            )
            if structure_broken and htf_reversal:
                return "exit_long_trend_invalidated"

    if current_profit >= 0.35:
        e21_1h = row.get("ema_21_1h", 0)
        c_1h = row.get("close_1h", 0)
        if e21_1h > 0 and c_1h > 0:
            if is_short and c_1h > e21_1h:
                return "exit_short_htf_runner_broken"
            if (not is_short) and c_1h < e21_1h:
                return "exit_long_htf_runner_broken"

    if duration_minutes >= 24 * 60 and current_profit < 0.35:
        close = row.get("close", 0)
        ema21 = row.get("ema_21", 0)
        if is_short and ((close > ema21) or (current_profit < 0.015)):
            return "exit_timeout_24h"
        if (not is_short) and ((close < ema21) or (current_profit < 0.015)):
            return "exit_timeout_24h"

    return None


def confirm_exit_allowed(
    *,
    trade: Any,
    exit_reason: str,
    rate: float,
    row: Optional[Mapping[str, Any]],
) -> bool:
    """
    Gate ROI exits for strong trend-runners; never block hard exits.

    When exit_reason == "roi" and ROE >= 50%, defer take-profit while 1H
    structure still favors the position.
    """
    if exit_reason in ALWAYS_ALLOW_EXIT_REASONS:
        return True

    if exit_reason != "roi":
        return True

    if row is None:
        return True

    current_profit = trade.calc_profit_ratio(rate)
    if current_profit < 0.50:
        return True

    c_1h = row.get("close_1h", 0)
    e21_1h = row.get("ema_21_1h", 0)
    if e21_1h <= 0:
        return True

    if trade.is_short:
        if c_1h < e21_1h:
            return False
    else:
        if c_1h > e21_1h:
            return False

    return True
