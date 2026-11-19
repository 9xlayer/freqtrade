"""Stable candidate identity helpers shared by fast-path and builder path."""
from __future__ import annotations

import numbers
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


def normalize_utc(ts: Union[datetime, pd.Timestamp]) -> datetime:
    """
    Normalize ``datetime`` / ``pd.Timestamp`` to timezone-aware UTC.

    Rejects bool / int / float / str and other coerced forms — callers must
    pass an explicit temporal type so identity is not ambiguous.
    """
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if not isinstance(ts, datetime):
        raise ValueError(
            f"timestamp must be datetime or pandas.Timestamp, got {type(ts).__name__}={ts!r}"
        )
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _canonical_side(side: Union[TradeSide, str]) -> str:
    """
    Return canonical side token.

    Accepts only ``TradeSide`` or exact str ``long``/``short``. Never coerces
    other objects via ``str()`` (a custom ``__str__`` returning ``'short'``
    must still be rejected).
    """
    if isinstance(side, TradeSide):
        side_v = side.value
    elif isinstance(side, str):
        side_v = side
    else:
        raise ValueError(
            f"side must be TradeSide or str 'long'/'short', "
            f"got {type(side).__name__}={side!r}"
        )
    if side_v not in _VALID_SIDES:
        raise ValueError(
            f"side must be exactly 'long' or 'short' (lowercase), got {side!r}"
        )
    return side_v


def _canonical_pair(pair: str) -> str:
    if not isinstance(pair, str):
        raise ValueError(f"pair must be str, got {type(pair).__name__}={pair!r}")
    p = pair.strip()
    if not p or "/" not in p:
        raise ValueError(f"pair must be a market symbol containing '/', got {pair!r}")
    return p


def _require_nonneg_int(value: Any, name: str) -> int:
    """
    Accept only integral values (incl. numpy integers). Reject bool, float, str.

    Never silently truncate floats (``int(12.9)``) or treat ``True`` as ``1``.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise ValueError(
            f"{name} must be a non-negative integer, got {type(value).__name__}={value!r}"
        )
    idx = int(value)
    if idx < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return idx


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
    timestamp: Union[datetime, pd.Timestamp],
    candle_index: int,
) -> str:
    """
    Canonical candidate_id:

        ``{pair}_{side}_{utc_epoch}_{candle_index}``

    Validates pair, side (exact ``long``/``short``), timestamp type, and
    non-negative integral ``candle_index``. Does not change the format.
    """
    pair_v = _canonical_pair(pair)
    side_v = _canonical_side(side)
    idx = _require_nonneg_int(candle_index, "candle_index")
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
    if not isinstance(candidate_id, str):
        return None
    parts = candidate_id.rsplit("_", 2)
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
    remade = f"{parsed.pair}_{parsed.side}_{parsed.epoch}_{parsed.candle_index}"
    return remade == str(candidate_id)


def candidate_id_matches_fields(
    candidate_id: str,
    *,
    pair: str,
    side: Union[TradeSide, str],
    timestamp: Union[datetime, pd.Timestamp],
    candle_index: int,
) -> bool:
    """True when ``candidate_id`` is canonical and matches the given identity fields."""
    parsed = parse_candidate_id(candidate_id)
    if parsed is None:
        return False
    try:
        side_l = _canonical_side(side)
        pair_v = _canonical_pair(pair)
        idx = _require_nonneg_int(candle_index, "candle_index")
        epoch = int(normalize_utc(timestamp).timestamp())
    except ValueError:
        return False
    if parsed.pair != pair_v or parsed.side != side_l:
        return False
    if parsed.candle_index != idx:
        return False
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
