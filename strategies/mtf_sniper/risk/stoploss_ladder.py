"""Dynamic custom stoploss ladder (parity with active StrategyMTFSniper)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from freqtrade.strategy import stoploss_from_open


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

    if not is_short:
        if current_profit >= 1.20:
            return -0.20
        if current_profit >= 0.80:
            return -0.30
        if current_profit >= 0.50:
            return stoploss_from_open(0.25, current_profit, is_short=False, leverage=lev)
        if current_profit >= 0.35:
            return stoploss_from_open(0.10, current_profit, is_short=False, leverage=lev)
        if current_profit >= 0.20:
            return stoploss_from_open(0.02, current_profit, is_short=False, leverage=lev)
        if current_profit >= 0.14:
            return stoploss_from_open(0.01, current_profit, is_short=False, leverage=lev)
    else:
        if current_profit >= 1.20:
            return -0.20
        if current_profit >= 0.80:
            return -0.30
        if current_profit >= 0.50:
            return stoploss_from_open(0.25, current_profit, is_short=True, leverage=lev)
        if current_profit >= 0.35:
            return stoploss_from_open(0.10, current_profit, is_short=True, leverage=lev)
        if current_profit >= 0.20:
            return stoploss_from_open(0.02, current_profit, is_short=True, leverage=lev)
        if current_profit >= 0.14:
            return stoploss_from_open(0.01, current_profit, is_short=True, leverage=lev)

    duration_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
    if duration_hours >= 24 and current_profit < 0.0:
        sl_rel = stoploss_from_open(-0.05, current_profit, is_short=is_short, leverage=lev)
        return max(sl_rel, 0.01)

    base_sl = pair_cfg.get("stoploss", default_stoploss)
    sl_rel = stoploss_from_open(base_sl, current_profit, is_short=is_short, leverage=lev)
    return max(sl_rel, 0.01)
