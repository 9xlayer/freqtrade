"""Dynamic custom stoploss ladder (parity with active StrategyMTFSniper)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from freqtrade.strategy import stoploss_from_open


def _ladder_stoploss_for_profit(
    current_profit: float, is_short: bool, lev: float
) -> float | None:
    if current_profit >= 1.20:
        return -0.20
    if current_profit >= 0.80:
        return -0.25
    if current_profit >= 0.50:
        return stoploss_from_open(0.25, current_profit, is_short=is_short, leverage=lev)
    if current_profit >= 0.30:
        return stoploss_from_open(0.10, current_profit, is_short=is_short, leverage=lev)
    if current_profit >= 0.18:
        return stoploss_from_open(0.01, current_profit, is_short=is_short, leverage=lev)
    return None


def resolve_custom_stoploss(
    *,
    trade: Any,
    current_time: datetime,
    current_profit: float,
    pair_cfg: Mapping[str, Any],
    default_stoploss: float,
) -> float:
    lev = trade.leverage if (trade and trade.leverage) else 10.0
    is_short = bool(trade.is_short)

    ladder_sl = _ladder_stoploss_for_profit(current_profit, is_short, lev)
    if ladder_sl is not None:
        return ladder_sl

    duration_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
    if duration_hours >= 24 and current_profit < 0.0:
        sl_rel = stoploss_from_open(-0.05, current_profit, is_short=is_short, leverage=lev)
        return max(sl_rel, 0.01)

    base_sl = pair_cfg.get("stoploss", default_stoploss)
    sl_rel = stoploss_from_open(base_sl, current_profit, is_short=is_short, leverage=lev)
    return max(sl_rel, 0.01)
