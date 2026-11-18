"""Flat pluggable registry that runs all market sensors."""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence

import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.signals.base_sensor import BaseSensor
from strategies.mtf_sniper.signals.composite_catalog import atomic_anomaly_sensors
from strategies.mtf_sniper.signals.sensors import (
    A80PlusSensor,
    SwingBreakoutSensor,
)
from strategies.mtf_sniper.signals.sensors.pattern_sniper_parts import (
    long_sniper_part_sensors,
    short_sniper_part_sensors,
)


def hunter_sensors() -> List[BaseSensor]:
    """Anomaly wakeups only (atomic)."""
    return atomic_anomaly_sensors()


def live_sensors(strategy: Any) -> List[BaseSensor]:
    """
    Live catalog: sniper part facts only (deterministic phase).

    Anomaly wakeups are intentionally omitted so they never reach Builder/Policy.
    Sniper formation happens in Directional SetupComposer (4a).
    """
    return [
        *short_sniper_part_sensors(strategy),
        *long_sniper_part_sensors(strategy),
    ]


def full_sensors() -> List[BaseSensor]:
    """Diagnostics only — anomalies + thin patterns (not live EntryPipeline)."""
    return [
        *atomic_anomaly_sensors(),
        A80PlusSensor(),
        SwingBreakoutSensor(),
    ]


class SensorSuite:
    """
    Flat catalog of independent BaseSensor instances (atomic or composite).

    Sensors are executed in registration order; results are concatenated.
    """

    def __init__(self, sensors: Optional[Sequence[BaseSensor]] = None) -> None:
        self._sensors: List[BaseSensor] = list(sensors) if sensors is not None else hunter_sensors()

    @classmethod
    def live(cls, strategy: Any) -> "SensorSuite":
        return cls(live_sensors(strategy))

    @classmethod
    def hunter(cls) -> "SensorSuite":
        return cls(hunter_sensors())

    @classmethod
    def full(cls) -> "SensorSuite":
        return cls(full_sensors())

    @classmethod
    def experimental(cls, strategy: Optional[Any] = None) -> "SensorSuite":
        if strategy is not None:
            return cls.live(strategy)
        return cls.hunter()

    @classmethod
    def from_sensors(cls, *sensors: BaseSensor) -> "SensorSuite":
        """Ad-hoc suite (tests / one-off composites)."""
        return cls(list(sensors))

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
