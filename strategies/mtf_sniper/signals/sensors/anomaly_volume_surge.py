"""Volume surge anomaly sensor (vol >= 4x SMA20 & range >= 2.2x ATR)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor


class VolumeSurgeSensor(BaseSensor):
    """Detect abnormal volume with substantial candle range (MEGA_SURGE parity)."""

    name = "volume_surge"
    sensor_type = SensorType.ANOMALY
    min_history = 100

    vol_ratio_min: float = 4.0
    range_atr_min: float = 2.2

    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: int | None = None,
    ) -> list[TriggerEvent]:
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
        range_ratio = ((dataframe["high"] - dataframe["low"]) / atr_safe).fillna(0.0)

        is_red = dataframe["close"] < dataframe["open"]
        is_green = dataframe["close"] > dataframe["open"]
        is_surge = (vol_ratio >= self.vol_ratio_min) & (range_ratio >= self.range_atr_min)

        events: list[TriggerEvent] = []
        for i in indices:
            if not bool(is_surge.iloc[i]):
                continue
            if bool(is_red.iloc[i]):
                side = TradeSide.SHORT
            elif bool(is_green.iloc[i]):
                side = TradeSide.LONG
            else:
                continue
            events.append(
                self._make_event(
                    side=side,
                    pair=pair,
                    timestamp=self._timestamp_at(dataframe, i),
                    candle_index=i,
                    raw_metrics={
                        "vol_ratio": float(vol_ratio.iloc[i]),
                        "range_atr_ratio": float(range_ratio.iloc[i]),
                        "close": float(dataframe.iloc[i]["close"]),
                        "volume": float(dataframe.iloc[i]["volume"]),
                    },
                )
            )
        return events
