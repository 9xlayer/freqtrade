"""Streak circuit breaker, directional lock, and distress guard."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any


logger = logging.getLogger(__name__)

HEAVY_LOSS_ROE = -0.10
CIRCUIT_BREAKER_STREAK = 3
CIRCUIT_BREAKER_PAUSE_SEC = 3 * 86400
DISTRESS_ROE = -0.08


def _to_utc_timestamp(dt: datetime | None) -> float | None:
    """Convert naive (assumed UTC in Freqtrade) or aware datetime to UTC epoch seconds."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).timestamp()
    return dt.astimezone(UTC).timestamp()


def _trade_close_dt(trade: Any) -> datetime | None:
    return getattr(trade, "close_date_utc", None) or getattr(trade, "close_date", None)


def _trade_open_ts(trade: Any) -> float | None:
    dt = getattr(trade, "open_date_utc", None) or getattr(trade, "open_date", None)
    return _to_utc_timestamp(dt)


def _is_correlated_loss(
    trade: Any,
    prev_loss: tuple[str, bool, float | None] | None,
    *,
    window_sec: int = 3600,
) -> bool:
    """True when another pair on the same side opened in the same entry wave."""
    if prev_loss is None:
        return False
    prev_pair, prev_short, prev_open_ts = prev_loss
    pair = str(getattr(trade, "pair", "") or "")
    open_ts = _trade_open_ts(trade)
    if not pair or not prev_pair or pair == prev_pair:
        return False
    if open_ts is None or prev_open_ts is None:
        return False
    is_short = bool(getattr(trade, "is_short", False))
    return is_short == prev_short and abs(open_ts - prev_open_ts) <= window_sec


def _compute_pause_until(
    strat_closed: Sequence[Any],
    *,
    heavy_loss_roe: float,
    streak: int,
    pause_sec: int,
) -> tuple[float, int]:
    """Scan closed trades chronologically, resetting streak after each CB pause."""
    chronological = sorted(
        strat_closed,
        key=lambda t: _to_utc_timestamp(_trade_close_dt(t)) or 0.0,
    )
    consecutive = 0
    triggered_streak = 0
    pause_until = 0.0
    prev_loss: tuple[str, bool, float | None] | None = None

    for t in chronological:
        close_ts = _to_utc_timestamp(_trade_close_dt(t))
        if close_ts is None or t.close_profit is None:
            continue
        try:
            profit = float(t.close_profit)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(profit):
            continue
        if profit > heavy_loss_roe:
            consecutive = 0
            prev_loss = None
            continue
        if _is_correlated_loss(t, prev_loss):
            continue
        prev_loss = (
            str(getattr(t, "pair", "") or ""),
            bool(getattr(t, "is_short", False)),
            _trade_open_ts(t),
        )
        consecutive += 1
        if consecutive >= streak:
            pause_until = close_ts + pause_sec
            triggered_streak = consecutive
            consecutive = 0
            prev_loss = None
    return pause_until, triggered_streak


def filter_strategy_trades(trades: Sequence[Any], strategy_name: str) -> list[Any]:
    """Match strategy trades, including backtest LocalTrade where strategy defaults to ''."""
    out: list[Any] = []
    for t in trades:
        strat = getattr(t, "strategy", None)
        if strat == strategy_name or (strat == "" and type(t).__name__ == "LocalTrade"):
            out.append(t)
    return out


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

    Only trades with matching strategy name count (isolates cross-strategy contamination),
    plus single-strategy backtesting ``LocalTrade`` instances whose ``strategy`` is ``""``.
    Streak resets after triggering a pause window, and simultaneous cross-pair entries on
    the same side count as one correlated loss event.
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

    now_ts = _to_utc_timestamp(current_time)
    if now_ts is None:
        return False

    pause_until, triggered_streak = _compute_pause_until(
        strat_closed,
        heavy_loss_roe=heavy_loss_roe,
        streak=streak,
        pause_sec=pause_sec,
    )
    if pause_until > 0.0 and now_ts < pause_until:
        logger.warning(
            f"[Circuit Breaker] Triggered: {triggered_streak} consecutive losses. "
            f"Trading paused until {pause_until}."
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
    get_price: Callable[[str, datetime], float | None],
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
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price_f) or price_f <= 0.0:
            continue
        profit_ratio = ot.calc_profit_ratio(price_f)
        if profit_ratio < distress_roe:
            return True
    return False
