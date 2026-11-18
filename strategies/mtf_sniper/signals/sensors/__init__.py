"""Independent pluggable market sensors."""
from strategies.mtf_sniper.signals.sensors.anomaly_volume_surge import VolumeSurgeSensor
from strategies.mtf_sniper.signals.sensors.anomaly_volatility import VolatilityExpansionSensor
from strategies.mtf_sniper.signals.sensors.anomaly_waterfall import WaterfallDropSensor
from strategies.mtf_sniper.signals.sensors.pattern_a80_confluence import A80PlusSensor
from strategies.mtf_sniper.signals.sensors.pattern_swing_breakout import SwingBreakoutSensor

__all__ = [
    "VolumeSurgeSensor",
    "VolatilityExpansionSensor",
    "WaterfallDropSensor",
    "A80PlusSensor",
    "SwingBreakoutSensor",
]
