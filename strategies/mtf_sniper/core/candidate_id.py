"""Stable candidate identity helpers shared by fast-path and builder path."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Union

import pandas as pd

from strategies.mtf_sniper.core.enums import TradeSide

# Persist on Trade via set_custom_data — survives bot restart (unlike RAM registry).
CANDIDATE_ID_CUSTOM_DATA_KEY = "mtf_candidate_id"
# Explicit sentinel when correlation cannot be reconstructed (never invent a
# non-canonical timestamp ID).
CANDIDATE_ID_MISSING = "candidate_id_missing"


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
    """
    side_v = side.value if isinstance(side, TradeSide) else str(side)
    ts = normalize_utc(timestamp)
    epoch = int(ts.timestamp())
    return f"{pair}_{side_v}_{epoch}_{int(candle_index)}"


def is_canonical_candidate_id(candidate_id: str) -> bool:
    """True when ID matches ``{pair}_{side}_{utc_epoch}_{candle_index}``."""
    if not candidate_id or candidate_id == CANDIDATE_ID_MISSING:
        return False
    parts = str(candidate_id).rsplit("_", 2)
    if len(parts) < 3:
        return False
    epoch_s, idx_s = parts[-2], parts[-1]
    return epoch_s.isdigit() and idx_s.lstrip("-").isdigit()
