"""Unit tests for MTF Sniper P1 Market Sensor Suite & CandidateBuilder."""
from __future__ import annotations

import unittest
from datetime import datetime, UTC

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.enums import RejectionReasonCode, SensorType, TradeSide
from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.signals.candidate_builder import CandidateBuilder
from strategies.mtf_sniper.signals.sensor_suite import SensorSuite
from strategies.mtf_sniper.signals.sensors import (
    A80PlusSensor,
    SwingBreakoutSensor,
    VolatilityExpansionSensor,
    VolumeSurgeSensor,
    WaterfallDropSensor,
)


def _base_df(n: int = 120) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "date": dates,
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 100.0),
            "atr": np.full(n, 1.0),
            "long_score": np.full(n, 50.0),
            "short_score": np.full(n, 50.0),
        }
    )


def _event(
    sensor_name: str,
    sensor_type: SensorType,
    side: TradeSide,
    candle_index: int,
    pair: str = "BTC/USDT:USDT",
) -> TriggerEvent:
    return TriggerEvent(
        sensor_name=sensor_name,
        sensor_type=sensor_type,
        side=side,
        pair=pair,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        candle_index=candle_index,
        raw_metrics={"close": 100.0},
    )


class TestVolumeSurgeSensor(unittest.TestCase):
    def test_insufficient_history(self):
        df = _base_df(50)
        events = VolumeSurgeSensor().scan(df, "BTC/USDT:USDT")
        self.assertEqual(events, [])

    def test_short_mega_surge(self):
        df = _base_df()
        df.loc[105, ["open", "close", "high", "low", "volume"]] = [100.0, 97.0, 100.0, 97.0, 500.0]
        events = VolumeSurgeSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].side, TradeSide.SHORT)
        self.assertEqual(events[0].sensor_name, "volume_surge")
        self.assertGreaterEqual(events[0].raw_metrics["vol_ratio"], 4.0)

    def test_long_mega_surge(self):
        df = _base_df()
        df.loc[105, ["open", "close", "high", "low", "volume"]] = [97.0, 100.0, 100.0, 97.0, 500.0]
        events = VolumeSurgeSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].side, TradeSide.LONG)

    def test_zero_atr_safe(self):
        df = _base_df()
        df["atr"] = 0.0
        df["volume"] = 0.0
        events = VolumeSurgeSensor().scan(df, "BTC/USDT:USDT")
        self.assertEqual(events, [])


class TestVolatilityExpansionSensor(unittest.TestCase):
    def test_range_expansion_with_breakout_volume_floor(self):
        df = _base_df()
        # Range = 3.0×ATR; vol ~2.5x SMA20 (BREAKOUT floor) but below MEGA_SURGE 4x
        df.loc[105, ["open", "close", "high", "low", "volume"]] = [100.0, 97.0, 100.0, 97.0, 280.0]
        events = VolatilityExpansionSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].side, TradeSide.SHORT)
        self.assertGreaterEqual(events[0].raw_metrics["range_atr_ratio"], 2.2)

    def test_range_expansion_rejects_low_volume(self):
        df = _base_df()
        df.loc[105, ["open", "close", "high", "low", "volume"]] = [100.0, 97.0, 100.0, 97.0, 200.0]
        events = VolatilityExpansionSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(events, [])


class TestSwingBreakoutSensor(unittest.TestCase):
    def test_breakout_24h_short(self):
        df = _base_df()
        # Prior 96-bar low is 99.0; break below with 3x volume
        df.loc[105, "close"] = 98.0
        df.loc[105, "volume"] = 300.0
        events = SwingBreakoutSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].side, TradeSide.SHORT)
        self.assertEqual(events[0].sensor_type, SensorType.PATTERN)

    def test_breakout_24h_long(self):
        df = _base_df()
        df.loc[105, "close"] = 102.0
        df.loc[105, "high"] = 102.5
        df.loc[105, "volume"] = 300.0
        events = SwingBreakoutSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].side, TradeSide.LONG)


class TestWaterfallDropSensor(unittest.TestCase):
    def test_waterfall_through_swing_low(self):
        df = _base_df()
        # Flat history then decisive dump through swing_low_12
        # vol must clear MEGA_SURGE floor vs SMA20 (incl. current bar)
        df.loc[105, ["open", "close", "high", "low", "volume"]] = [100.0, 96.0, 100.0, 95.5, 520.0]
        events = WaterfallDropSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].side, TradeSide.SHORT)
        self.assertEqual(events[0].sensor_name, "waterfall_drop")

    def test_no_waterfall_on_green(self):
        df = _base_df()
        df.loc[105, ["open", "close", "high", "low", "volume"]] = [96.0, 100.0, 100.5, 95.5, 400.0]
        events = WaterfallDropSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(events, [])


class TestA80PlusSensor(unittest.TestCase):
    def test_long_a80(self):
        df = _base_df()
        df.loc[105, ["open", "close", "high", "low", "long_score"]] = [99.0, 101.0, 101.2, 98.8, 85.0]
        events = A80PlusSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].side, TradeSide.LONG)
        self.assertEqual(events[0].sensor_type, SensorType.PATTERN)

    def test_score_below_threshold(self):
        df = _base_df()
        df.loc[105, ["open", "close", "high", "low", "long_score"]] = [99.0, 101.0, 101.2, 98.8, 70.0]
        events = A80PlusSensor().scan(df, "BTC/USDT:USDT", candle_index=105)
        self.assertEqual(events, [])


class TestSensorSuite(unittest.TestCase):
    def test_suite_aggregates_sensors(self):
        df = _base_df()
        df.loc[105, ["open", "close", "high", "low", "volume"]] = [100.0, 97.0, 100.0, 97.0, 500.0]
        suite = SensorSuite()
        events = suite.scan(df, "BTC/USDT:USDT", candle_index=105)
        names = {e.sensor_name for e in events}
        self.assertIn("volume_surge", names)
        self.assertIn("volatility_expansion", names)


class TestCandidateBuilder(unittest.TestCase):
    def test_same_side_confluence_merge(self):
        events = [
            _event("volume_surge", SensorType.ANOMALY, TradeSide.LONG, 105),
            _event("a80_plus", SensorType.PATTERN, TradeSide.LONG, 105),
            _event("swing_breakout", SensorType.PATTERN, TradeSide.LONG, 105),
        ]
        builder = CandidateBuilder(cooldown_bars=12)
        candidates = builder.process(events)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.confluence_count, 3)
        self.assertEqual(c.side, TradeSide.LONG)
        # Pattern preferred over anomaly as primary
        self.assertIn(c.primary_sensor, {"a80_plus", "swing_breakout"})
        self.assertNotEqual(c.primary_sensor, "volume_surge")
        self.assertEqual(len(c.secondary_sensors), 2)

    def test_opposite_side_neutralize(self):
        # Incomplete anomalies on both sides are kept for composer (not neutralized).
        # Neutralize only when BOTH sides are complete sniper recipes.
        from strategies.mtf_sniper.directional.long.setups import LONG_SNIPER_PART_NAMES
        from strategies.mtf_sniper.directional.short.setups import SHORT_SNIPER_PART_NAMES

        events = []
        for name in LONG_SNIPER_PART_NAMES:
            events.append(_event(name, SensorType.PATTERN, TradeSide.LONG, 105))
        for name in SHORT_SNIPER_PART_NAMES:
            events.append(_event(name, SensorType.PATTERN, TradeSide.SHORT, 105))
        rejected: list = []
        builder = CandidateBuilder(cooldown_bars=12)
        candidates = builder.process(events, rejected=rejected)
        self.assertEqual(candidates, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], RejectionReasonCode.CONTRADICTORY_SIGNALS.value)

    def test_directional_cooldown_enforced(self):
        events = [
            _event("volume_surge", SensorType.ANOMALY, TradeSide.LONG, 100),
            _event("volume_surge", SensorType.ANOMALY, TradeSide.LONG, 105),  # within 12 bars
            _event("volume_surge", SensorType.ANOMALY, TradeSide.LONG, 113),  # exactly 13 bars later OK
        ]
        rejected: list = []
        builder = CandidateBuilder(cooldown_bars=12)
        candidates = builder.process(events, rejected=rejected)
        self.assertEqual([c.candle_index for c in candidates], [100, 113])
        self.assertTrue(any(r["reason"] == RejectionReasonCode.COOLDOWN_ACTIVE.value for r in rejected))

    def test_directional_cooldown_isolation(self):
        """Long and Short cooldowns are independent."""
        events = [
            _event("volume_surge", SensorType.ANOMALY, TradeSide.LONG, 102),
            _event("volume_surge", SensorType.ANOMALY, TradeSide.SHORT, 104),
        ]
        builder = CandidateBuilder(cooldown_bars=12)
        candidates = builder.process(events)
        self.assertEqual(len(candidates), 2)
        sides = {c.side for c in candidates}
        self.assertEqual(sides, {TradeSide.LONG, TradeSide.SHORT})

    def test_end_to_end_suite_to_builder(self):
        df = _base_df()
        df.loc[105, ["open", "close", "high", "low", "volume", "long_score"]] = [
            97.0, 100.0, 100.5, 96.5, 500.0, 85.0
        ]
        suite = SensorSuite()
        events = suite.scan(df, "ETH/USDT:USDT", candle_index=105)
        # Should include at least volume_surge + a80_plus on long
        long_events = [e for e in events if e.side == TradeSide.LONG]
        self.assertGreaterEqual(len(long_events), 2)

        builder = CandidateBuilder(cooldown_bars=12)
        candidates = builder.process(events)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].side, TradeSide.LONG)
        self.assertGreaterEqual(candidates[0].confluence_count, 2)


if __name__ == "__main__":
    unittest.main()
