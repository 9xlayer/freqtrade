"""Streak circuit breaker, directional lock, and distress guard."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

HEAVY_LOSS_ROE = -0.10
CIRCUIT_BREAKER_STREAK = 3
CIRCUIT_BREAKER_PAUSE_SEC = 3 * 86400
DISTRESS_ROE = -0.08


def filter_strategy_trades(trades: Sequence[Any], strategy_name: str) -> List[Any]:
    return [t for t in trades if getattr(t, "strategy", None) == strategy_name]


def circuit_breaker_blocks(
    closed_trades: Sequence[Any],
    *,
    strategy_name: str,
    current_time: datetime,
    heavy_loss_roe: float = HEAVY_LOSS_ROE,
    streak: int = CIRCUIT_BREAKER_STREAK,
    pause_sec: int = CIRCUIT_BREAKER_PAUSE_SEC,
) -> bool:
    """
    Return True if new entries must be blocked.

    Only trades with matching strategy name count (isolates cross-strategy contamination).
    """
    strat_closed = filter_strategy_trades(closed_trades, strategy_name)
    untracked = [t for t in closed_trades if getattr(t, "strategy", None) is None]
    if untracked:
        logger.debug(
            f"[{strategy_name} Circuit Breaker] Ignored {len(untracked)} untracked trades "
            f"with strategy=None"
        )
    if len(strat_closed) < streak:
        return False

    recent = sorted(
        strat_closed,
        key=lambda t: t.close_date.timestamp() if t.close_date else 0.0,
        reverse=True,
    )
    consecutive = 0
    for t in recent:
        profit = t.close_profit if t.close_profit is not None else 0.0
        if profit <= heavy_loss_roe:
            consecutive += 1
        else:
            break

    if consecutive < streak:
        return False

    last_loss_date = recent[0].close_date
    if not last_loss_date:
        return False
    if (current_time.timestamp() - last_loss_date.timestamp()) < pause_sec:
        logger.warning(
            f"[Circuit Breaker] Triggered: {consecutive} consecutive losses. "
            f"Trading paused for 72h until {last_loss_date.timestamp() + pause_sec}."
        )
        return True
    return False


def directional_lock_blocks(open_trades: Sequence[Any], side: str) -> bool:
    """Block if any open trade has the opposite side."""
    for ot in open_trades:
        ot_side = "short" if ot.is_short else "long"
        if ot_side != side:
            return True
    return False


def distress_guard_blocks(
    open_trades: Sequence[Any],
    *,
    pair: str,
    current_time: datetime,
    get_price: Callable[[str, datetime], Optional[float]],
    distress_roe: float = DISTRESS_ROE,
) -> bool:
    """
    Block new entries when another open pair is in distress (< distress_roe).

    `get_price(pair, current_time)` returns the latest close or None.
    """
    for ot in open_trades:
        if ot.pair == pair:
            continue
        price = get_price(ot.pair, current_time)
        if price is None:
            continue
        profit_ratio = ot.calc_profit_ratio(price)
        if profit_ratio < distress_roe:
            return True
    return False
