"""
Legacy sniper mask as named part sensors (atomic facts).

Each part is a BaseSensor. Directional SetupComposer matches ALL parts
(= old AND-mask) before Policy can ACCEPT an entry.

Performance: ``build_*_sniper_parts`` is cached once per (dataframe, pair, side)
so a suite of 4 short + 4 long parts does not rebuild masks 8× on BT/live scans.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.directional.common_gates import (
    data_ready_mask,
    sideway_safe_mask,
    volatility_safe_mask,
)
from strategies.mtf_sniper.directional.long.setups import (
    LONG_SNIPER_PART_NAMES,
    build_long_sniper_parts,
)
from strategies.mtf_sniper.directional.short.setups import (
    SHORT_SNIPER_PART_NAMES,
    build_short_sniper_parts,
)
from strategies.mtf_sniper.signals.base_sensor import BaseSensor

# Stored on dataframe.attrs for the lifetime of one populate_entry / suite scan
_PARTS_CACHE_ATTR = "_mtf_sniper_part_masks_v1"


def _dataframe_cache_token(dataframe: pd.DataFrame) -> Tuple[Any, ...]:
    n = len(dataframe)
    if n and "date" in dataframe.columns:
        return (n, dataframe["date"].iloc[-1], dataframe["date"].iloc[0])
    return (n, id(dataframe))


def cached_sniper_parts(
    strategy: Any,
    dataframe: pd.DataFrame,
    pair: str,
    side: TradeSide,
) -> Dict[str, pd.Series]:
    """
    Return part masks for ``side``, computing at most once per DF/pair/side/token.
    """
    token = _dataframe_cache_token(dataframe)
    store: Dict = dataframe.attrs.setdefault(_PARTS_CACHE_ATTR, {})
    key = (pair, side.value, token)
    hit = store.get(key)
    if hit is not None:
        return hit

    pair_cfg = strategy._get_pair_params(pair) if hasattr(strategy, "_get_pair_params") else {}
    ready = data_ready_mask(dataframe)
    vol = volatility_safe_mask(strategy, dataframe)
    sideway = sideway_safe_mask(strategy, dataframe)

    if side == TradeSide.SHORT:
        parts = build_short_sniper_parts(
            strategy,
            dataframe,
            pair,
            pair_cfg,
            data_ready=ready,
            cond_volatility_safe=vol,
            cond_sideway_safe=sideway,
        )
    else:
        parts, _pdh = build_long_sniper_parts(
            strategy,
            dataframe,
            pair_cfg,
            data_ready=ready,
            cond_volatility_safe=vol,
            cond_sideway_safe=sideway,
        )

    store[key] = parts
    # Drop other tokens for this pair/side to avoid unbounded attrs growth
    stale = [k for k in store if k[0] == pair and k[1] == side.value and k != key]
    for k in stale:
        del store[k]
    return parts


class LegacySniperPartSensor(BaseSensor):
    """
    One named slice of the legacy sniper AND-mask.

    ``raw_metrics`` include ``legacy_sniper`` / ``part`` for composer + audit.
    """

    sensor_type = SensorType.PATTERN
    min_history = 100

    def __init__(self, strategy: Any, part_name: str, side: TradeSide) -> None:
        if side == TradeSide.SHORT and part_name not in SHORT_SNIPER_PART_NAMES:
            raise ValueError(f"Unknown short sniper part: {part_name}")
        if side == TradeSide.LONG and part_name not in LONG_SNIPER_PART_NAMES:
            raise ValueError(f"Unknown long sniper part: {part_name}")
        self.name = part_name
        self._strategy = strategy
        self._part_name = part_name
        self._side = side

    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: Optional[int] = None,
    ) -> List:
        if len(dataframe) < self.min_history:
            return []

        parts = cached_sniper_parts(self._strategy, dataframe, pair, self._side)
        if self._side == TradeSide.SHORT:
            score_key = "short_score"
            legacy_tag = "short"
        else:
            score_key = "long_score"
            legacy_tag = "long"

        mask = parts[self._part_name]
        # Full-history BT scan: jump only to True bars (avoid O(n) Python iloc probes).
        if candle_index is None:
            arr = mask.to_numpy(dtype=bool, na_value=False)
            hit_indices = np.flatnonzero(arr)
            indices = hit_indices[hit_indices >= self.min_history]
        else:
            indices = self._resolve_indices(dataframe, candle_index)
            indices = [i for i in indices if bool(mask.iloc[i])]
        if len(indices) == 0:
            return []

        events = []
        need_score = self._part_name.endswith("_structure")
        for i in indices:
            i = int(i)
            metrics = {
                "part": self._part_name,
                "legacy_sniper": legacy_tag,
                "mask": f"legacy_and_{legacy_tag}",
            }
            if need_score:
                metrics[score_key] = float(dataframe.iloc[i].get(score_key, 0.0) or 0.0)
            events.append(
                self._make_event(
                    side=self._side,
                    pair=pair,
                    timestamp=self._timestamp_at(dataframe, i),
                    candle_index=i,
                    raw_metrics=metrics,
                )
            )
        return events


def short_sniper_part_sensors(strategy: Any) -> List[LegacySniperPartSensor]:
    return [
        LegacySniperPartSensor(strategy, name, TradeSide.SHORT)
        for name in SHORT_SNIPER_PART_NAMES
    ]


def long_sniper_part_sensors(strategy: Any) -> List[LegacySniperPartSensor]:
    return [
        LegacySniperPartSensor(strategy, name, TradeSide.LONG)
        for name in LONG_SNIPER_PART_NAMES
    ]
