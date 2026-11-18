"""Lifecycle registry: map entry context → canonical candidate_id.

Freqtrade ``confirm_trade_entry`` does not receive TradeCandidate metadata.
Populate stores IDs here; confirm/risk looks them up so telemetry stays linked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
from strategies.mtf_sniper.core.contracts import TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide


@dataclass(frozen=True)
class _EntryKey:
    pair: str
    side: str
    entry_tag: str


class CandidateRegistry:
    """Bounded in-memory map from (pair, side, tag) → latest candidate_id."""

    def __init__(self, *, maxlen_per_key: int = 8) -> None:
        self._maxlen = max(1, int(maxlen_per_key))
        # key → list of (candle_index, utc_ts, candidate_id) newest last
        self._store: Dict[_EntryKey, list[Tuple[int, datetime, str]]] = {}

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
        # Prefer latest candidate whose candle time is <= confirm time
        for _idx, ts, cid in reversed(bucket):
            if ts <= ct:
                return cid
        return bucket[-1][2]

    def clear(self) -> None:
        self._store.clear()
