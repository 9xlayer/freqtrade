"""
Named composite recipes for experiments (not live sniper path).

Live sniper combination lives in ``directional.setup_composer`` (4a).
Keep ``CompositeSensor`` here for ad-hoc anomaly combos / tests.
"""

from __future__ import annotations

from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor
from strategies.mtf_sniper.signals.composite_sensor import CombineMode, CompositeSensor, compose
from strategies.mtf_sniper.signals.sensors.anomaly_volatility import VolatilityExpansionSensor
from strategies.mtf_sniper.signals.sensors.anomaly_volume_surge import VolumeSurgeSensor
from strategies.mtf_sniper.signals.sensors.anomaly_waterfall import WaterfallDropSensor


def atomic_anomaly_sensors() -> list[BaseSensor]:
    """Unchanged atomic Hunter wakeups."""
    return [
        VolumeSurgeSensor(),
        VolatilityExpansionSensor(),
        WaterfallDropSensor(),
    ]


def example_hunter_strict_short() -> CompositeSensor:
    """
    Example extension (not on live by default): surge AND waterfall on same bar.
    """
    return compose(
        "composite_hunter_strict_short",
        VolumeSurgeSensor(),
        WaterfallDropSensor(),
        mode=CombineMode.ALL,
        sensor_type=SensorType.PATTERN,
        side=TradeSide.SHORT,
    )
