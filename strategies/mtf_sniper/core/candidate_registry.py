"""Lifecycle registry: map entry context → canonical candidate_id.

Freqtrade callbacks do not receive TradeCandidate metadata. Populate stores
IDs here; confirm / entry-fill / exit-fill look them up so telemetry stays linked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
from strategies.mtf_sniper.core.contracts import TradeCandidate


@dataclass(frozen=True)
class _EntryKey:
    pair: str
    side: str
    entry_tag: str


class CandidateRegistry:
    """Bounded maps: (pair, side, tag) → candidate_id, and trade_id → candidate_id."""

    def __init__(self, *, maxlen_per_key: int = 8, maxlen_trades: int = 256) -> None:
        self._maxlen = max(1, int(maxlen_per_key))
        self._maxlen_trades = max(1, int(maxlen_trades))
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
    ) -> Optional[str]:
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
        for _idx, ts, cid in reversed(bucket):
            if ts <= ct:
                return cid
        return bucket[-1][2]

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
