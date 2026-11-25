"""Waterfall drop anomaly sensor (acute breakdown through swing support)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.market.data_guards import (
    data_ready_mask,
    finite_series_mask,
    missing_informative_columns,
)
from strategies.mtf_sniper.signals.base_sensor import BaseSensor
from strategies.mtf_sniper.signals.momentum_radar import MomentumRadar


class WaterfallDropSensor(BaseSensor):
    """
    Detect sudden dump through recent swing low with elevated volume pressure.

    Short-only sensor. Wakeups aligned to MomentumRadar MEGA_SURGE_SHORT
    (vol>=4x, range>=2.2x ATR) plus decisive red breakdown through swing low.
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

        events: list[TriggerEvent] = []
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
                        "swing_low": float(swing_low.iloc[i])
                        if pd.notna(swing_low.iloc[i])
                        else None,
                        "close": float(dataframe.iloc[i]["close"]),
                    },
                )
            )
        return events


_WATERFALL_FINITE_COLS = (
    "swing_low_20d_1d",
    "ema_50_4h",
    "bb_width_4h",
    "ema_200_1d",
    "rsi",
    "ema_21",
    "lower_wick_ratio",
    "close_pos_ratio",
    "rel_vol",
)


class WaterfallMomentumSensor(BaseSensor):
    """
    Live Waterfall sub-system sensor (1-to-1 parity with MomentumRadar short scan).

    Applies directional 12-bar cooldown via MomentumRadar and filters out bars
    with missing or non-finite required indicators.
    """

    name = "waterfall_momentum"
    sensor_type = SensorType.ANOMALY
    side = TradeSide.SHORT
    min_history = 100
    cooldown_bars: int = 12

    def _compute_triggers(self, dataframe: pd.DataFrame) -> dict[int, str]:
        if len(dataframe) < self.min_history:
            return {}
        radar_cands = MomentumRadar.find_candidates(
            dataframe, cooldown_bars=self.cooldown_bars, directional_cooldown=True
        )
        if not missing_informative_columns(dataframe):
            ready = (
                data_ready_mask(dataframe)
                & finite_series_mask(dataframe, _WATERFALL_FINITE_COLS)
            ).to_numpy(dtype=bool, na_value=False)
        else:
            ready = finite_series_mask(dataframe, _WATERFALL_FINITE_COLS).to_numpy(
                dtype=bool, na_value=False
            )
        return {
            pos: event_type
            for pos, event_type in radar_cands
            if "SHORT" in event_type and pos < len(ready) and ready[pos]
        }

    def scan_mask(self, dataframe: pd.DataFrame, pair: str) -> np.ndarray:
        _ = pair
        mask = np.zeros(len(dataframe), dtype=bool)
        for pos in self._compute_triggers(dataframe):
            mask[pos] = True
        return mask

    def scan_mask_features(
        self, dataframe: pd.DataFrame, indices: np.ndarray
    ) -> dict[int, dict[str, Any]]:
        triggers = self._compute_triggers(dataframe)
        out: dict[int, dict[str, Any]] = {}
        for idx in indices:
            i = int(idx)
            event_type = triggers.get(i)
            if event_type is None:
                continue
            row = dataframe.iloc[i]
            out[i] = {
                "setup_name": "waterfall_collapse",
                "signal_source": "momentum_radar",
                "event_trigger": event_type,
                "short_score": float(row.get("short_score", 0.0) or 0.0),
            }
        return out

    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: int | None = None,
    ) -> list[TriggerEvent]:
        triggers = self._compute_triggers(dataframe)
        if not triggers:
            return []
        if candle_index is not None:
            positions = [candle_index] if candle_index in triggers else []
        else:
            positions = sorted(triggers)
        events: list[TriggerEvent] = []
        for i in positions:
            row = dataframe.iloc[i]
            events.append(
                self._make_event(
                    side=TradeSide.SHORT,
                    pair=pair,
                    timestamp=self._timestamp_at(dataframe, i),
                    candle_index=i,
                    raw_metrics={
                        "setup_name": "waterfall_collapse",
                        "signal_source": "momentum_radar",
                        "event_trigger": triggers[i],
                        "short_score": float(row.get("short_score", 0.0) or 0.0),
                    },
                )
            )
        return events
