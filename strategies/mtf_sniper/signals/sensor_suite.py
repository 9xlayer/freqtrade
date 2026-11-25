"""Flat pluggable registry that runs all market sensors."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.signals.base_sensor import BaseSensor
from strategies.mtf_sniper.signals.composite_catalog import atomic_anomaly_sensors
from strategies.mtf_sniper.signals.sensors import (
    A80PlusSensor,
    SwingBreakoutSensor,
    WaterfallMomentumSensor,
)
from strategies.mtf_sniper.signals.sensors.pattern_sniper_parts import (
    long_sniper_part_sensors,
    short_sniper_part_sensors,
)


def hunter_sensors() -> list[BaseSensor]:
    """Anomaly wakeups only (atomic)."""
    return atomic_anomaly_sensors()


def live_sensors(
    strategy: Any,
    *,
    include_waterfall: bool = False,
) -> list[BaseSensor]:
    """
    Live catalog: sniper part facts plus optional waterfall momentum sensor.

    Raw anomaly wakeups stay omitted unless explicitly registered by subsystem.
    """
    sensors: list[BaseSensor] = [
        *short_sniper_part_sensors(strategy),
        *long_sniper_part_sensors(strategy),
    ]
    if include_waterfall:
        sensors.append(WaterfallMomentumSensor())
    return sensors


def full_sensors() -> list[BaseSensor]:
    """Diagnostics + registry sensor catalog."""
    return [
        *atomic_anomaly_sensors(),
        WaterfallMomentumSensor(),
        A80PlusSensor(),
        SwingBreakoutSensor(),
    ]


class SensorSuite:
    """
    Flat catalog of independent BaseSensor instances (atomic or composite).

    Sensors are executed in registration order; results are concatenated.
    """

    def __init__(self, sensors: Sequence[BaseSensor] | None = None) -> None:
        self._sensors: list[BaseSensor] = list(sensors) if sensors is not None else hunter_sensors()

    @classmethod
    def live(cls, strategy: Any, *, include_waterfall: bool = False) -> SensorSuite:
        return cls(live_sensors(strategy, include_waterfall=include_waterfall))

    @classmethod
    def hunter(cls) -> SensorSuite:
        return cls(hunter_sensors())

    @classmethod
    def full(cls) -> SensorSuite:
        return cls(full_sensors())

    @classmethod
    def experimental(cls, strategy: Any | None = None) -> SensorSuite:
        if strategy is not None:
            return cls.live(strategy)
        return cls.hunter()

    @classmethod
    def from_sensors(cls, *sensors: BaseSensor) -> SensorSuite:
        """Ad-hoc suite (tests / one-off composites)."""
        return cls(list(sensors))

    @property
    def sensors(self) -> list[BaseSensor]:
        return list(self._sensors)

    def register(self, sensor: BaseSensor) -> None:
        self._sensors.append(sensor)

    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: int | None = None,
    ) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []
        for sensor in self._sensors:
            events.extend(sensor.scan(dataframe, pair, candle_index=candle_index))
        return events

    def scan_many(
        self,
        frames: Iterable[tuple[str, pd.DataFrame]],
        *,
        candle_index: int | None = None,
    ) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []
        for pair, dataframe in frames:
            events.extend(self.scan(dataframe, pair, candle_index=candle_index))
        return events
