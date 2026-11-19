"""24-hour swing high/low breakout pattern sensor."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor


class SwingBreakoutSensor(BaseSensor):
    """Detect close breaking prior 96-bar (24h on 15m) high/low with volume confirmation."""

    name = "swing_breakout"
    sensor_type = SensorType.PATTERN
    min_history = 100

    lookback_bars: int = 96
    vol_ratio_min: float = 2.5

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

        low_n = dataframe["low"].rolling(self.lookback_bars).min().shift(1)
        high_n = dataframe["high"].rolling(self.lookback_bars).max().shift(1)

        is_break_short = (dataframe["close"] < low_n) & (vol_ratio >= self.vol_ratio_min)
        is_break_long = (dataframe["close"] > high_n) & (vol_ratio >= self.vol_ratio_min)

        events: list[TriggerEvent] = []
        for i in indices:
            side: TradeSide | None = None
            if bool(is_break_short.iloc[i]):
                side = TradeSide.SHORT
            elif bool(is_break_long.iloc[i]):
                side = TradeSide.LONG
            if side is None:
                continue
            events.append(
                self._make_event(
                    side=side,
                    pair=pair,
                    timestamp=self._timestamp_at(dataframe, i),
                    candle_index=i,
                    raw_metrics={
                        "vol_ratio": float(vol_ratio.iloc[i]),
                        "prior_low": float(low_n.iloc[i]) if pd.notna(low_n.iloc[i]) else None,
                        "prior_high": float(high_n.iloc[i]) if pd.notna(high_n.iloc[i]) else None,
                        "close": float(dataframe.iloc[i]["close"]),
                        "lookback_bars": self.lookback_bars,
                    },
                )
            )
        return events
