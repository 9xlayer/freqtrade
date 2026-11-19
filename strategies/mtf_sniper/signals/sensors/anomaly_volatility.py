"""Volatility expansion anomaly sensor (range >= 2.2x ATR)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor


class VolatilityExpansionSensor(BaseSensor):
    """
    Detect candles with anomalous range expansion relative to ATR.

    Direction is inferred from candle body (green -> LONG, red -> SHORT).
    Requires BREAKOUT_24H-style volume floor (vol≥2.5x) so range-only noise
    does not soft-escalate into Hunter.
    """

    name = "volatility_expansion"
    sensor_type = SensorType.ANOMALY
    min_history = 100

    range_atr_min: float = 2.2
    vol_ratio_min: float = 2.5
    # Exclude pure volume-surge candles to reduce duplicate blips when both fire
    exclude_if_volume_surge: bool = False
    vol_ratio_surge: float = 4.0

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

        atr_safe = (
            dataframe["atr"].replace(0, np.nan)
            if "atr" in dataframe.columns
            else pd.Series(np.nan, index=dataframe.index)
        )
        range_ratio = ((dataframe["high"] - dataframe["low"]) / atr_safe).fillna(0.0)

        vol_sma = dataframe["volume"].rolling(20, min_periods=1).mean().replace(0, np.nan)
        vol_ratio = (dataframe["volume"] / vol_sma).fillna(0.0)

        is_red = dataframe["close"] < dataframe["open"]
        is_green = dataframe["close"] > dataframe["open"]
        is_expansion = (range_ratio >= self.range_atr_min) & (vol_ratio >= self.vol_ratio_min)

        if self.exclude_if_volume_surge:
            is_expansion = is_expansion & (vol_ratio < self.vol_ratio_surge)

        events: list[TriggerEvent] = []
        for i in indices:
            if not bool(is_expansion.iloc[i]):
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
                        "range_atr_ratio": float(range_ratio.iloc[i]),
                        "vol_ratio": float(vol_ratio.iloc[i]),
                        "close": float(dataframe.iloc[i]["close"]),
                    },
                )
            )
        return events
