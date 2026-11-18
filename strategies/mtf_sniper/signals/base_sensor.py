"""Abstract base class for all market sensors."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide


class BaseSensor(ABC):
    """
    High-recall detector that emits TriggerEvent blips only.

    Invariants:
    - NEVER decide trade entry
    - NEVER compute position size
    - NEVER check portfolio / risk limits
    """

    name: str = "base"
    sensor_type: SensorType = SensorType.ANOMALY
    # Minimum bars of history required before scanning begins
    min_history: int = 100

    @abstractmethod
    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: Optional[int] = None,
    ) -> List[TriggerEvent]:
        """
        Scan dataframe and return zero or more TriggerEvents.

        If candle_index is provided, only that bar is evaluated.
        Otherwise the full series (from min_history onward) is scanned.
        """

    def _resolve_indices(
        self,
        dataframe: pd.DataFrame,
        candle_index: Optional[int],
    ) -> List[int]:
        n = len(dataframe)
        if n < self.min_history:
            return []
        if candle_index is not None:
            if candle_index < self.min_history or candle_index >= n:
                return []
            return [candle_index]
        return list(range(self.min_history, n))

    def _timestamp_at(self, dataframe: pd.DataFrame, idx: int) -> datetime:
        if "date" in dataframe.columns:
            value = dataframe.iloc[idx]["date"]
            if isinstance(value, datetime):
                return value
            return pd.Timestamp(value).to_pydatetime()
        # Fall back to index value when date column is absent (unit tests)
        index_val = dataframe.index[idx]
        if isinstance(index_val, (pd.Timestamp, datetime)):
            return pd.Timestamp(index_val).to_pydatetime()
        return datetime.utcfromtimestamp(0)

    def _make_event(
        self,
        *,
        side: TradeSide,
        pair: str,
        timestamp: datetime,
        candle_index: int,
        raw_metrics: Dict[str, Any],
    ) -> TriggerEvent:
        return TriggerEvent(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            side=side,
            pair=pair,
            timestamp=timestamp,
            candle_index=candle_index,
            raw_metrics=raw_metrics,
        )
