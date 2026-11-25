"""
Exit policy helpers for confirm_trade_exit.

Active role: confirm_exit_allowed gates ROI exits for strong trend-runners.
Position exit management is unified in risk/stoploss_ladder.py (use_exit_signal = False).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any


ALWAYS_ALLOW_EXIT_REASONS = frozenset(
    ("stop_loss", "trailing_stop_loss", "emergency_exit", "force_exit")
)


def resolve_market_row(
    dataframe: Any,
    current_time: datetime,
) -> Mapping[str, Any] | None:
    """Pick the latest analyzed bar at or before current_time."""
    if dataframe is None or getattr(dataframe, "empty", True):
        return None
    dates = dataframe["date"]
    # Exact match first (common in backtesting candle loop).
    exact = dates == current_time
    if getattr(exact, "any", lambda: False)():
        return dataframe.loc[exact].iloc[-1]
    # Avoid full DataFrame copies on every candle: boolean index → last True pos.
    up_to = (dates <= current_time).to_numpy().nonzero()[0]
    if len(up_to) == 0:
        return None
    return dataframe.iloc[int(up_to[-1])]


def confirm_exit_allowed(
    *,
    trade: Any,
    exit_reason: str,
    rate: float,
    row: Mapping[str, Any] | None,
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
