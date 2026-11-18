"""Lifecycle registry: map entry context → canonical candidate_id.

Freqtrade callbacks do not receive TradeCandidate metadata. Populate stores
IDs here; confirm / entry-fill / exit-fill look them up so telemetry stays linked.

Lookup rules (when ``current_time`` is provided):
- Never return a candidate with timestamp **after** ``current_time``.
- Never fall back to the newest entry if none match.
- Optionally enforce a max age window so an old same-tag signal is not reused.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
from strategies.mtf_sniper.core.contracts import TradeCandidate

# Default skew for confirm/reconstruct: allow small clock/candle lag, reject stale.
DEFAULT_LOOKUP_MAX_AGE = timedelta(hours=6)


@dataclass(frozen=True)
class _EntryKey:
    pair: str
    side: str
    entry_tag: str


class CandidateRegistry:
    """Bounded maps: (pair, side, tag) → candidate_id, and trade_id → candidate_id."""

    def __init__(
        self,
        *,
        maxlen_per_key: int = 8,
        maxlen_trades: int = 256,
        lookup_max_age: timedelta = DEFAULT_LOOKUP_MAX_AGE,
    ) -> None:
        self._maxlen = max(1, int(maxlen_per_key))
        self._maxlen_trades = max(1, int(maxlen_trades))
        self._lookup_max_age = lookup_max_age
        # key → list of (candle_index, utc_ts, candidate_id) newest last
        self._store: Dict[_EntryKey, list[Tuple[int, datetime, str]]] = {}
        # trade_id → candidate_id (bound on entry fill, cleared on exit record)
        self._by_trade: Dict[int, str] = {}
        self._trade_order: list[int] = []

    def remember(self, candidate: TradeCandidate, entry_tag: str) -> None:
        tag = str(entry_tag or "")
        key = _EntryKey(
            pair=str(candidate.pair),
            side=str(candidate.side.value).lower(),
            entry_tag=tag,
        )
        ts = normalize_utc(candidate.timestamp)
        cid = candidate.candidate_id or make_candidate_id(
            candidate.pair, candidate.side, ts, candidate.candle_index
        )
        bucket = self._store.setdefault(key, [])
        bucket.append((int(candidate.candle_index), ts, cid))
        if len(bucket) > self._maxlen:
            del bucket[0 : len(bucket) - self._maxlen]

    def lookup(
        self,
        *,
        pair: str,
        side: str,
        entry_tag: Optional[str],
        current_time: Optional[datetime] = None,
        max_age: Optional[timedelta] = None,
    ) -> Optional[str]:
        """
        Return the newest candidate with ``timestamp <= current_time`` inside
        the age window. Returns ``None`` when nothing matches (no future fallback).
        """
        key = _EntryKey(
            pair=str(pair),
            side=str(side).lower(),
            entry_tag=str(entry_tag or ""),
        )
        bucket = self._store.get(key)
        if not bucket:
            return None
        if current_time is None:
            return bucket[-1][2]

        ct = normalize_utc(current_time)
        age_limit = self._lookup_max_age if max_age is None else max_age
        for _idx, ts, cid in reversed(bucket):
            if ts > ct:
                continue  # never return a future candidate
            if age_limit is not None and (ct - ts) > age_limit:
                continue  # too old for this callback
            return cid
        return None

    def bind_trade(self, trade_id: int, candidate_id: str) -> None:
        tid = int(trade_id)
        if tid <= 0 or not candidate_id:
            return
        if tid not in self._by_trade:
            self._trade_order.append(tid)
        self._by_trade[tid] = str(candidate_id)
        while len(self._trade_order) > self._maxlen_trades:
            old = self._trade_order.pop(0)
            self._by_trade.pop(old, None)

    def lookup_trade(self, trade_id: int) -> Optional[str]:
        return self._by_trade.get(int(trade_id))

    def unbind_trade(self, trade_id: int) -> None:
        tid = int(trade_id)
        self._by_trade.pop(tid, None)
        try:
            self._trade_order.remove(tid)
        except ValueError:
            pass

    def clear(self) -> None:
        self._store.clear()
        self._by_trade.clear()
        self._trade_order.clear()
