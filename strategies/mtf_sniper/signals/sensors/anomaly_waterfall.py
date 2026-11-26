"""Waterfall momentum anomaly sensor (MomentumRadar collapse)."""

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


_WATERFALL_TRIGGER_CACHE_ATTR = "_mtf_waterfall_triggers_v1"

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

    def _df_cache_token(self, dataframe: pd.DataFrame) -> tuple[Any, ...]:
        n = len(dataframe)
        if n and "date" in dataframe.columns:
            return (self.cooldown_bars, n, dataframe["date"].iloc[-1], dataframe["date"].iloc[0])
        return (self.cooldown_bars, n, id(dataframe))

    def _compute_triggers(self, dataframe: pd.DataFrame) -> dict[int, str]:
        if len(dataframe) < self.min_history:
            return {}
        token = self._df_cache_token(dataframe)
        store: dict[tuple[Any, ...], dict[int, str]] = dataframe.attrs.setdefault(
            _WATERFALL_TRIGGER_CACHE_ATTR, {}
        )
        cached = store.get(token)
        if cached is not None:
            return cached

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
        triggers = {
            pos: event_type
            for pos, event_type in radar_cands
            if "SHORT" in event_type and pos < len(ready) and ready[pos]
        }
        store[token] = triggers
        return triggers

    def scan_mask(self, dataframe: pd.DataFrame, pair: str) -> np.ndarray:
        _ = pair
        mask = np.zeros(len(dataframe), dtype=bool)
        for pos in self._compute_triggers(dataframe):
            mask[pos] = True
        return mask

    @staticmethod
    def _waterfall_features(
        event_type: str, scores: np.ndarray | None, pos: int
    ) -> dict[str, Any]:
        raw_score = scores[pos] if scores is not None else 0.0
        return {
            "setup_name": "waterfall_collapse",
            "signal_source": "momentum_radar",
            "event_trigger": event_type,
            "short_score": float(raw_score or 0.0),
        }

    def scan_mask_features(
        self, dataframe: pd.DataFrame, indices: np.ndarray
    ) -> dict[int, dict[str, Any]]:
        triggers = self._compute_triggers(dataframe)
        if not triggers or len(indices) == 0:
            return {}
        scores = dataframe["short_score"].to_numpy() if "short_score" in dataframe.columns else None
        out: dict[int, dict[str, Any]] = {}
        for idx in indices:
            i = int(idx)
            event_type = triggers.get(i)
            if event_type is not None:
                out[i] = self._waterfall_features(event_type, scores, i)
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
        scores = dataframe["short_score"].to_numpy() if "short_score" in dataframe.columns else None
        events: list[TriggerEvent] = []
        for i in positions:
            events.append(
                self._make_event(
                    side=TradeSide.SHORT,
                    pair=pair,
                    timestamp=self._timestamp_at(dataframe, i),
                    candle_index=i,
                    raw_metrics=self._waterfall_features(triggers[i], scores, i),
                )
            )
        return events
