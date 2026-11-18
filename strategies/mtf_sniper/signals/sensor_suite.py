"""Flat pluggable registry that runs all market sensors."""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.signals.base_sensor import BaseSensor
from strategies.mtf_sniper.signals.sensors import (
    A80PlusSensor,
    SwingBreakoutSensor,
    VolatilityExpansionSensor,
    VolumeSurgeSensor,
    WaterfallDropSensor,
)


def default_sensors() -> List[BaseSensor]:
    """Live SensorSuite catalog (anomaly + pattern)."""
    return [
        VolumeSurgeSensor(),
        VolatilityExpansionSensor(),
        WaterfallDropSensor(),
        A80PlusSensor(),
        SwingBreakoutSensor(),
    ]


class SensorSuite:
    """
    Flat catalog of independent BaseSensor instances.

    Sensors are executed in registration order; results are concatenated.
    """

    def __init__(self, sensors: Optional[Sequence[BaseSensor]] = None) -> None:
        self._sensors: List[BaseSensor] = list(sensors) if sensors is not None else default_sensors()

    @classmethod
    def experimental(cls) -> "SensorSuite":
        """Alias for the live catalog (historical name)."""
        return cls(default_sensors())

    @classmethod
    def live(cls) -> "SensorSuite":
        return cls(default_sensors())

    @property
    def sensors(self) -> List[BaseSensor]:
        return list(self._sensors)

    def register(self, sensor: BaseSensor) -> None:
        self._sensors.append(sensor)

    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: Optional[int] = None,
    ) -> List[TriggerEvent]:
        events: List[TriggerEvent] = []
        for sensor in self._sensors:
            events.extend(sensor.scan(dataframe, pair, candle_index=candle_index))
        return events

    def scan_many(
        self,
        frames: Iterable[tuple[str, pd.DataFrame]],
        *,
        candle_index: Optional[int] = None,
    ) -> List[TriggerEvent]:
        events: List[TriggerEvent] = []
        for pair, dataframe in frames:
            events.extend(self.scan(dataframe, pair, candle_index=candle_index))
        return events
