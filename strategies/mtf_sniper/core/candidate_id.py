"""Stable candidate identity helpers shared by fast-path and builder path."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Union

import pandas as pd

from strategies.mtf_sniper.core.enums import TradeSide

# Persist on Trade via set_custom_data — survives bot restart (unlike RAM registry).
CANDIDATE_ID_CUSTOM_DATA_KEY = "mtf_candidate_id"
# Explicit sentinel when correlation cannot be reconstructed (never invent a
# non-canonical timestamp ID).
CANDIDATE_ID_MISSING = "candidate_id_missing"

_VALID_SIDES = frozenset({"long", "short"})


@dataclass(frozen=True)
class ParsedCandidateId:
    pair: str
    side: str
    epoch: int
    candle_index: int


def normalize_utc(ts: Union[datetime, pd.Timestamp, Any]) -> datetime:
    """Normalize timestamps to timezone-aware UTC datetimes."""
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if not isinstance(ts, datetime):
        ts = pd.Timestamp(ts).to_pydatetime()
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def make_candidate_id(
    pair: str,
    side: Union[TradeSide, str],
    timestamp: Union[datetime, pd.Timestamp, Any],
    candle_index: int,
) -> str:
    """
    Canonical candidate_id:

        ``{pair}_{side}_{utc_epoch}_{candle_index}``

    ``candle_index`` must be a non-negative integer.
    """
    idx = int(candle_index)
    if idx < 0:
        raise ValueError(f"candle_index must be >= 0, got {candle_index!r}")
    side_v = side.value if isinstance(side, TradeSide) else str(side).lower()
    ts = normalize_utc(timestamp)
    epoch = int(ts.timestamp())
    return f"{pair}_{side_v}_{epoch}_{idx}"


def parse_candidate_id(candidate_id: str) -> Optional[ParsedCandidateId]:
    """
    Parse ``{pair}_{side}_{utc_epoch}_{candle_index}``.

    ``pair`` may contain underscores; ``side`` must be ``long`` or ``short``.
    ``candle_index`` must be a non-negative integer (negative indexes are rejected).
    """
    if not candidate_id or candidate_id == CANDIDATE_ID_MISSING:
        return None
    parts = str(candidate_id).rsplit("_", 2)
    if len(parts) != 3:
        return None
    prefix, epoch_s, idx_s = parts
    # Reject negatives: isdigit() is False for "-1"
    if not epoch_s.isdigit() or not idx_s.isdigit():
        return None
    # Split ``{pair}_{side}`` from the right — side is always the last token.
    pair_side = prefix.rsplit("_", 1)
    if len(pair_side) != 2:
        return None
    pair, side = pair_side
    side_l = side.lower()
    if side_l not in _VALID_SIDES:
        return None
    if not pair or "/" not in pair:
        # Require a market-style pair (e.g. BTC/USDT or BTC/USDT:USDT).
        return None
    return ParsedCandidateId(
        pair=pair,
        side=side_l,
        epoch=int(epoch_s),
        candle_index=int(idx_s),
    )


def is_canonical_candidate_id(candidate_id: str) -> bool:
    """True when ID matches ``{pair}_{side}_{utc_epoch}_{candle_index}`` with valid side."""
    return parse_candidate_id(candidate_id) is not None


def candidate_id_matches_fields(
    candidate_id: str,
    *,
    pair: str,
    side: Union[TradeSide, str],
    timestamp: Union[datetime, pd.Timestamp, Any],
    candle_index: int,
) -> bool:
    """True when ``candidate_id`` is canonical and matches the given identity fields."""
    parsed = parse_candidate_id(candidate_id)
    if parsed is None:
        return False
    side_l = side.value if isinstance(side, TradeSide) else str(side).lower()
    if parsed.pair != str(pair) or parsed.side != side_l:
        return False
    if int(candle_index) < 0 or parsed.candle_index != int(candle_index):
        return False
    epoch = int(normalize_utc(timestamp).timestamp())
    return parsed.epoch == epoch


def candidate_id_matches_trade(
    candidate_id: str,
    *,
    pair: Optional[str] = None,
    side: Optional[Union[TradeSide, str]] = None,
) -> bool:
    """
    Canonical parse plus optional pair/side context checks.

    When ``pair`` / ``side`` are provided, both must match the parsed ID.
    """
    parsed = parse_candidate_id(candidate_id)
    if parsed is None:
        return False
    if pair is not None and parsed.pair != str(pair):
        return False
    if side is not None:
        side_l = side.value if isinstance(side, TradeSide) else str(side).lower()
        if parsed.side != side_l:
            return False
    return True
