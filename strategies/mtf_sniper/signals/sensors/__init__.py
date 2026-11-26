"""Independent pluggable market sensors."""

from strategies.mtf_sniper.signals.sensors.anomaly_waterfall import WaterfallMomentumSensor
from strategies.mtf_sniper.signals.sensors.market_regime import MarketRegimeSensor
from strategies.mtf_sniper.signals.sensors.pattern_sniper_parts import (
    LegacySniperPartSensor,
    long_sniper_part_sensors,
    short_sniper_part_sensors,
)


__all__ = [
    "WaterfallMomentumSensor",
    "MarketRegimeSensor",
    "LegacySniperPartSensor",
    "short_sniper_part_sensors",
    "long_sniper_part_sensors",
]
