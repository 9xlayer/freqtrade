"""Lifecycle registry: map entry context → canonical candidate_id.

Freqtrade callbacks do not receive TradeCandidate metadata. Populate stores
IDs here; confirm / entry-fill / exit-fill look them up so telemetry stays linked.

Lookup rules (when ``current_time`` is provided):
- Never return a candidate with timestamp **after** ``current_time``.
- Never fall back to the newest entry if none match.
- Optionally enforce a max age window so an old same-tag signal is not reused.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from strategies.mtf_sniper.core.candidate_id import (
    _canonical_pair,
    _canonical_side,
    _require_nonneg_int,
    candidate_id_matches_fields,
    make_candidate_id,
    normalize_utc,
)
from strategies.mtf_sniper.core.contracts import TradeCandidate

logger = logging.getLogger(__name__)

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
        self._maxlen = max(1, maxlen_per_key)
        self._maxlen_trades = max(1, maxlen_trades)
        self._lookup_max_age = lookup_max_age
        # key → list of (candle_index, utc_ts, candidate_id) newest last
        self._store: Dict[_EntryKey, list[Tuple[int, datetime, str]]] = {}
        # trade_id → candidate_id (bound on entry fill, cleared on exit record)
        self._by_trade: Dict[int, str] = {}
        self._trade_order: list[int] = []

    def remember(self, candidate: Any, entry_tag: str) -> None:
        """
        Store a candidate_id for (pair, side, entry_tag).

        Fail-closed: malformed identity never raises into the entry pipeline and
        never writes the registry. When identity fields are valid but a provided
        ``candidate_id`` mismatches, store the canonical ID rebuilt from fields.
        """
        try:
            if candidate is None:
                logger.warning("[CandidateRegistry] Refusing remember: candidate is None")
                return

            pair = _canonical_pair(getattr(candidate, "pair", None))
            side = _canonical_side(getattr(candidate, "side", None))
            ts = normalize_utc(getattr(candidate, "timestamp", None))
            idx = _require_nonneg_int(
                getattr(candidate, "candle_index", None), "candle_index"
            )
            expected = make_candidate_id(pair, side, ts, idx)

            raw_id = getattr(candidate, "candidate_id", None)
            if raw_id is None or raw_id == "":
                cid = expected
            elif not isinstance(raw_id, str):
                logger.warning(
                    f"[CandidateRegistry] Ignoring non-str candidate_id="
                    f"{type(raw_id).__name__}={raw_id!r}; storing canonical {expected!r}"
                )
                cid = expected
            else:
                provided = raw_id.strip()
                if provided and not candidate_id_matches_fields(
                    provided,
                    pair=pair,
                    side=side,
                    timestamp=ts,
                    candle_index=idx,
                ):
                    logger.warning(
                        f"[CandidateRegistry] Ignoring mismatched candidate_id="
                        f"{provided!r}; storing canonical {expected!r}"
                    )
                    cid = expected
                else:
                    cid = provided or expected

            # Only after full validation — never build key from untrusted fields.
            key = _EntryKey(
                pair=pair,
                side=side,
                entry_tag=str(entry_tag or ""),
            )
            bucket = self._store.setdefault(key, [])
            bucket.append((idx, ts, cid))
            if len(bucket) > self._maxlen:
                del bucket[0 : len(bucket) - self._maxlen]
        except Exception as exc:
            logger.warning(
                f"[CandidateRegistry] Refusing remember (invalid identity): {exc}"
            )
            return

    def lookup(
        self,
        *,
        pair: str,
        side: str,
        entry_tag: Optional[str],
        current_time: Optional[datetime] = None,
        max_age: Optional[timedelta] = None,
        allow_unbounded: bool = False,
    ) -> Optional[str]:
        """
        Return the newest candidate with ``timestamp <= current_time`` inside
        the age window (default ``DEFAULT_LOOKUP_MAX_AGE`` = 6h).

        Semantics:
        - ``current_time`` **required** for age-bounded lookup (production path).
        - If ``current_time`` is ``None`` and ``allow_unbounded`` is False →
          return ``None`` (fail-closed; never silently return a possibly-stale ID).
        - If ``allow_unbounded=True`` with ``current_time is None`` → return the
          newest stored ID for diagnostics/tests only (no max-age filter).
        - Never returns a future candidate when ``current_time`` is set.
        """
        key = _EntryKey(
            pair=pair,
            side=side.lower(),
            entry_tag=str(entry_tag or ""),
        )
        bucket = self._store.get(key)
        if not bucket:
            return None
        if current_time is None:
            if allow_unbounded:
                return bucket[-1][2]
            logger.warning(
                "[CandidateRegistry] lookup without current_time refused "
                f"(pair={pair} side={side} tag={entry_tag!r}); "
                "pass current_time or allow_unbounded=True"
            )
            return None

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
        tid = trade_id
        if tid <= 0 or not candidate_id:
            return
        if tid not in self._by_trade:
            self._trade_order.append(tid)
        self._by_trade[tid] = candidate_id
        while len(self._trade_order) > self._maxlen_trades:
            old = self._trade_order.pop(0)
            self._by_trade.pop(old, None)

    def lookup_trade(self, trade_id: int) -> Optional[str]:
        return self._by_trade.get(trade_id)

    def unbind_trade(self, trade_id: int) -> None:
        tid = trade_id
        self._by_trade.pop(tid, None)
        if tid in self._trade_order:
            self._trade_order.remove(tid)

    def clear(self) -> None:
        self._store.clear()
        self._by_trade.clear()
        self._trade_order.clear()
