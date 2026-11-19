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


def _canonical_side(side: Union[TradeSide, str]) -> str:
    """Return canonical side token; reject anything except exact ``long``/``short``."""
    if isinstance(side, TradeSide):
        side_v = side.value
    else:
        side_v = str(side)
    if side_v not in _VALID_SIDES:
        raise ValueError(
            f"side must be exactly 'long' or 'short' (lowercase), got {side!r}"
        )
    return side_v


def _canonical_pair(pair: str) -> str:
    p = str(pair).strip()
    if not p or "/" not in p:
        raise ValueError(f"pair must be a market symbol containing '/', got {pair!r}")
    return p


def _is_canonical_int_token(token: str) -> bool:
    """Digits only, no leading zeros (except the value ``0`` itself)."""
    if not token.isdigit():
        return False
    if len(token) > 1 and token[0] == "0":
        return False
    return True


def make_candidate_id(
    pair: str,
    side: Union[TradeSide, str],
    timestamp: Union[datetime, pd.Timestamp, Any],
    candle_index: int,
) -> str:
    """
    Canonical candidate_id:

        ``{pair}_{side}_{utc_epoch}_{candle_index}``

    Validates pair (must contain ``/``), side (exact ``long``/``short``), and
    non-negative ``candle_index``. Does not change the format.
    """
    pair_v = _canonical_pair(pair)
    side_v = _canonical_side(side)
    idx = int(candle_index)
    if idx < 0:
        raise ValueError(f"candle_index must be >= 0, got {candle_index!r}")
    ts = normalize_utc(timestamp)
    epoch = int(ts.timestamp())
    return f"{pair_v}_{side_v}_{epoch}_{idx}"


def parse_candidate_id(candidate_id: str) -> Optional[ParsedCandidateId]:
    """
    Parse the **single** canonical representation:

        ``{pair}_{side}_{utc_epoch}_{candle_index}``

    Rejects: missing sentinel, uppercase side, leading-zero epoch/index,
    negative index, non-market pair, non-digit tokens.
    """
    if not candidate_id or candidate_id == CANDIDATE_ID_MISSING:
        return None
    parts = str(candidate_id).rsplit("_", 2)
    if len(parts) != 3:
        return None
    prefix, epoch_s, idx_s = parts
    if not _is_canonical_int_token(epoch_s) or not _is_canonical_int_token(idx_s):
        return None
    pair_side = prefix.rsplit("_", 1)
    if len(pair_side) != 2:
        return None
    pair, side = pair_side
    # Exact lowercase only — do not casefold (rejects LONG/Short).
    if side not in _VALID_SIDES:
        return None
    if not pair or "/" not in pair:
        return None
    return ParsedCandidateId(
        pair=pair,
        side=side,
        epoch=int(epoch_s),
        candle_index=int(idx_s),
    )


def is_canonical_candidate_id(candidate_id: str) -> bool:
    """True when ID is the unique canonical representation (round-trip stable)."""
    parsed = parse_candidate_id(candidate_id)
    if parsed is None:
        return False
    # Round-trip: remake from parsed fields must equal the original string.
    remade = (
        f"{parsed.pair}_{parsed.side}_{parsed.epoch}_{parsed.candle_index}"
    )
    return remade == str(candidate_id)


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
    try:
        side_l = _canonical_side(side)
        pair_v = _canonical_pair(pair)
    except ValueError:
        return False
    if parsed.pair != pair_v or parsed.side != side_l:
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
    if pair is not None:
        try:
            if parsed.pair != _canonical_pair(pair):
                return False
        except ValueError:
            return False
    if side is not None:
        try:
            if parsed.side != _canonical_side(side):
                return False
        except ValueError:
            return False
    return True
