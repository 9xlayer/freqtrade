"""A80+ multi-indicator confluence pattern sensor."""

from __future__ import annotations

import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor


class A80PlusSensor(BaseSensor):
    """
    Detect A80+ confluence setups from precomputed long_score / short_score.

    Sensor-only: requires score >= 80 and a decisive same-direction candle body.
    Macro / wick / PDH policy filters belong in Directional Engine (P2).
    """

    name = "a80_plus"
    sensor_type = SensorType.PATTERN
    min_history = 100

    score_threshold: float = 80.0
    body_ratio_min: float = 0.55
    body_atr_min: float = 0.5

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

        has_long = "long_score" in dataframe.columns
        has_short = "short_score" in dataframe.columns
        if not has_long and not has_short:
            return []

        total_range = (dataframe["high"] - dataframe["low"]).clip(lower=1e-12)
        body = (dataframe["close"] - dataframe["open"]).abs()
        body_ratio = body / total_range
        atr = (
            dataframe["atr"]
            if "atr" in dataframe.columns
            else pd.Series(1.0, index=dataframe.index)
        )

        is_green = dataframe["close"] > dataframe["open"]
        is_red = dataframe["close"] < dataframe["open"]
        decisive = (body_ratio >= self.body_ratio_min) & (body >= atr * self.body_atr_min)

        events: list[TriggerEvent] = []
        for i in indices:
            if not bool(decisive.iloc[i]):
                continue

            if has_long and bool(is_green.iloc[i]):
                score = float(dataframe.iloc[i]["long_score"])
                if score >= self.score_threshold:
                    events.append(
                        self._make_event(
                            side=TradeSide.LONG,
                            pair=pair,
                            timestamp=self._timestamp_at(dataframe, i),
                            candle_index=i,
                            raw_metrics={
                                "long_score": score,
                                "body_ratio": float(body_ratio.iloc[i]),
                                "close": float(dataframe.iloc[i]["close"]),
                            },
                        )
                    )

            if has_short and bool(is_red.iloc[i]):
                score = float(dataframe.iloc[i]["short_score"])
                if score >= self.score_threshold:
                    events.append(
                        self._make_event(
                            side=TradeSide.SHORT,
                            pair=pair,
                            timestamp=self._timestamp_at(dataframe, i),
                            candle_index=i,
                            raw_metrics={
                                "short_score": score,
                                "body_ratio": float(body_ratio.iloc[i]),
                                "close": float(dataframe.iloc[i]["close"]),
                            },
                        )
                    )
        return events
