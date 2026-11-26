"""Weekly macro regime context sensor (SensorType.CONTEXT)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor


REGIME_BULL = "bull_trending"
REGIME_CHOP = "chop"
REGIME_BEAR_RELIEF = "bear_relief"
REGIME_CYCLE_TOP = "cycle_top"
REGIME_BEAR = "bear_trending"
REGIME_UNKNOWN = "unknown"


class MarketRegimeSensor(BaseSensor):
    """Emits factual 1W macro regime context on each ready bar (never decides entries)."""

    name: str = "market_regime"
    sensor_type: SensorType = SensorType.CONTEXT
    side: TradeSide | None = None
    min_history: int = 100

    def _ready_mask(self, dataframe: pd.DataFrame) -> np.ndarray:
        n = len(dataframe)
        if n < self.min_history or "market_regime_1w" not in dataframe.columns:
            return np.zeros(n, dtype=bool)
        col = dataframe["market_regime_1w"]
        arr = (col.notna() & (col.astype(str) != REGIME_UNKNOWN)).to_numpy(
            dtype=bool, na_value=False
        ).copy()
        if self.min_history > 0:
            arr[: self.min_history] = False
        return arr

    def scan_mask(self, dataframe: pd.DataFrame, pair: str) -> np.ndarray:
        _ = pair
        return self._ready_mask(dataframe)

    @staticmethod
    def _scalar_float(arr: np.ndarray | None, idx: int, default: float) -> float:
        if arr is None:
            return default
        val = arr[idx]
        return float(val) if val is not None and pd.notna(val) else default

    def scan_mask_features(
        self, dataframe: pd.DataFrame, indices: np.ndarray
    ) -> dict[int, dict[str, Any]]:
        if "market_regime_1w" not in dataframe.columns or len(indices) == 0:
            return {}
        cols = dataframe.columns
        reg_arr = dataframe["market_regime_1w"].to_numpy()
        mh_arr = dataframe["macdhist_1w"].to_numpy() if "macdhist_1w" in cols else None
        adx_arr = dataframe["adx_1w"].to_numpy() if "adx_1w" in cols else None
        rsi_arr = dataframe["rsi_1w"].to_numpy() if "rsi_1w" in cols else None
        bear_arr = dataframe["is_bear_wave_1w"].to_numpy() if "is_bear_wave_1w" in cols else None
        bull_arr = dataframe["is_bull_wave_1w"].to_numpy() if "is_bull_wave_1w" in cols else None

        out: dict[int, dict[str, Any]] = {}
        for idx in indices:
            i = int(idx)
            raw_regime = reg_arr[i]
            regime = (
                REGIME_UNKNOWN
                if raw_regime is None or pd.isna(raw_regime)
                else raw_regime if isinstance(raw_regime, str) else f"{raw_regime}"
            )
            out[i] = {
                "regime_1w": regime,
                "macdhist_1w": self._scalar_float(mh_arr, i, 0.0),
                "adx_1w": self._scalar_float(adx_arr, i, 0.0),
                "rsi_1w": self._scalar_float(rsi_arr, i, 50.0),
                "is_bear_wave_1w": bool(bear_arr[i]) if bear_arr is not None else False,
                "is_bull_wave_1w": bool(bull_arr[i]) if bull_arr is not None else False,
            }
        return out

    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: int | None = None,
    ) -> list[TriggerEvent]:
        mask = self._ready_mask(dataframe)
        if not mask.any():
            return []

        if candle_index is None:
            hit_indices = np.flatnonzero(mask)
        else:
            valid = self._resolve_indices(dataframe, candle_index)
            hit_indices = np.asarray([i for i in valid if mask[i]], dtype=int)
        if len(hit_indices) == 0:
            return []

        features = self.scan_mask_features(dataframe, hit_indices)
        events: list[TriggerEvent] = []
        for idx in hit_indices:
            i = int(idx)
            metrics = features.get(i)
            if not metrics:
                continue
            events.append(
                TriggerEvent(
                    sensor_name=self.name,
                    sensor_type=self.sensor_type,
                    side=None,
                    pair=pair,
                    timestamp=self._timestamp_at(dataframe, i),
                    candle_index=i,
                    raw_metrics=metrics,
                )
            )
        return events
