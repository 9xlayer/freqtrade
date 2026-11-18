"""Waterfall drop anomaly sensor (acute breakdown through swing support)."""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor


class WaterfallDropSensor(BaseSensor):
    """
    Detect sudden dump through recent swing low with elevated volume pressure.

    Short-only sensor. Wakeups aligned to MomentumRadar MEGA_SURGE_SHORT
    (vol≥4x, range≥2.2×ATR) plus decisive red breakdown through swing low.
    """

    name = "waterfall_drop"
    sensor_type = SensorType.ANOMALY
    min_history = 100

    swing_lookback: int = 12
    vol_ratio_min: float = 4.0
    range_atr_min: float = 2.2
    body_ratio_min: float = 0.50

    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: Optional[int] = None,
    ) -> List[TriggerEvent]:
        indices = self._resolve_indices(dataframe, candle_index)
        if not indices:
            return []

        vol_sma = dataframe["volume"].rolling(20, min_periods=1).mean().replace(0, np.nan)
        vol_ratio = (dataframe["volume"] / vol_sma).fillna(0.0)

        atr_safe = (
            dataframe["atr"].replace(0, np.nan)
            if "atr" in dataframe.columns
            else pd.Series(np.nan, index=dataframe.index)
        )
        total_range = (dataframe["high"] - dataframe["low"]).clip(lower=1e-12)
        body = (dataframe["close"] - dataframe["open"]).abs()
        body_ratio = body / total_range
        range_ratio = (total_range / atr_safe).fillna(0.0)

        swing_low = dataframe["low"].shift(1).rolling(self.swing_lookback).min()
        is_red = dataframe["close"] < dataframe["open"]
        is_breakdown = (dataframe["close"] < swing_low) & (dataframe["close"].shift(1) >= swing_low)

        mask = (
            is_red
            & is_breakdown
            & (vol_ratio >= self.vol_ratio_min)
            & (range_ratio >= self.range_atr_min)
            & (body_ratio >= self.body_ratio_min)
        )

        events: List[TriggerEvent] = []
        for i in indices:
            if not bool(mask.iloc[i]):
                continue
            events.append(
                self._make_event(
                    side=TradeSide.SHORT,
                    pair=pair,
                    timestamp=self._timestamp_at(dataframe, i),
                    candle_index=i,
                    raw_metrics={
                        "vol_ratio": float(vol_ratio.iloc[i]),
                        "range_atr_ratio": float(range_ratio.iloc[i]),
                        "body_ratio": float(body_ratio.iloc[i]),
                        "swing_low": float(swing_low.iloc[i]) if pd.notna(swing_low.iloc[i]) else None,
                        "close": float(dataframe.iloc[i]["close"]),
                    },
                )
            )
        return events
