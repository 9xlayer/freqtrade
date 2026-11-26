"""Independent pluggable market sensors."""

from strategies.mtf_sniper.signals.sensors.anomaly_volatility import VolatilityExpansionSensor
from strategies.mtf_sniper.signals.sensors.anomaly_volume_surge import VolumeSurgeSensor
from strategies.mtf_sniper.signals.sensors.anomaly_waterfall import (
    WaterfallDropSensor,
    WaterfallMomentumSensor,
)
from strategies.mtf_sniper.signals.sensors.market_regime import MarketRegimeSensor
from strategies.mtf_sniper.signals.sensors.pattern_a80_confluence import A80PlusSensor
from strategies.mtf_sniper.signals.sensors.pattern_sniper_parts import (
    LegacySniperPartSensor,
    long_sniper_part_sensors,
    short_sniper_part_sensors,
)
from strategies.mtf_sniper.signals.sensors.pattern_swing_breakout import SwingBreakoutSensor


__all__ = [
    "VolumeSurgeSensor",
    "VolatilityExpansionSensor",
    "WaterfallDropSensor",
    "WaterfallMomentumSensor",
    "MarketRegimeSensor",
    "LegacySniperPartSensor",
    "short_sniper_part_sensors",
    "long_sniper_part_sensors",
    "A80PlusSensor",
    "SwingBreakoutSensor",
]
