"""Unit tests for MTF Sniper P1 Market Sensor Suite & CandidateBuilder."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import RejectionReasonCode, SensorType, TradeSide
from strategies.mtf_sniper.signals.candidate_builder import CandidateBuilder
from strategies.mtf_sniper.signals.sensor_suite import SensorSuite
from strategies.mtf_sniper.signals.sensors import (
    MarketRegimeSensor,
    WaterfallMomentumSensor,
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


class TestSensorSuite(unittest.TestCase):
    def test_suite_aggregates_sensors(self):
        from strategies.mtf_sniper.market.data_guards import REQUIRED_INFORMATIVE_COLS

        df = _base_df(140)
        for col in REQUIRED_INFORMATIVE_COLS:
            df[col] = 100.0
        for col in (
            "swing_low_20d_1d",
            "ema_50_4h",
            "bb_width_4h",
            "ema_200_1d",
            "rsi",
            "ema_21",
            "lower_wick_ratio",
            "close_pos_ratio",
            "rel_vol",
        ):
            df[col] = 1.0
        df["market_regime_1w"] = "bear_trending"
        df.loc[110, ["open", "close", "high", "low", "volume"]] = [100.0, 96.0, 100.0, 95.5, 520.0]

        suite = SensorSuite()
        events = suite.scan(df, "BTC/USDT:USDT", candle_index=110)
        names = {e.sensor_name for e in events}
        self.assertIn("waterfall_momentum", names)
        self.assertIn("market_regime", names)


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
            _event(
                "volume_surge", SensorType.ANOMALY, TradeSide.LONG, 113
            ),  # exactly 13 bars later OK
        ]
        rejected: list = []
        builder = CandidateBuilder(cooldown_bars=12)
        candidates = builder.process(events, rejected=rejected)
        self.assertEqual([c.candle_index for c in candidates], [100, 113])
        self.assertTrue(
            any(r["reason"] == RejectionReasonCode.COOLDOWN_ACTIVE.value for r in rejected)
        )

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
        from strategies.mtf_sniper.market.data_guards import REQUIRED_INFORMATIVE_COLS

        df = _base_df(140)
        for col in REQUIRED_INFORMATIVE_COLS:
            df[col] = 100.0
        for col in (
            "swing_low_20d_1d",
            "ema_50_4h",
            "bb_width_4h",
            "ema_200_1d",
            "rsi",
            "ema_21",
            "lower_wick_ratio",
            "close_pos_ratio",
            "rel_vol",
        ):
            df[col] = 1.0
        df["market_regime_1w"] = "bear_trending"
        df.loc[110, ["open", "close", "high", "low", "volume"]] = [100.0, 96.0, 100.0, 95.5, 520.0]

        suite = SensorSuite()
        events = suite.scan(df, "ETH/USDT:USDT", candle_index=110)
        self.assertTrue(any(e.sensor_name == "waterfall_momentum" for e in events))

        builder = CandidateBuilder(cooldown_bars=12)
        candidates = builder.process(events)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].side, TradeSide.SHORT)
        self.assertEqual(candidates[0].raw_features["context_sensors"], ["market_regime"])


class TestWaterfallMomentumSensor(unittest.TestCase):
    def _ready_df(self, n: int = 160) -> pd.DataFrame:
        from strategies.mtf_sniper.market.data_guards import REQUIRED_INFORMATIVE_COLS

        df = _base_df(n)
        for col in REQUIRED_INFORMATIVE_COLS:
            df[col] = 100.0
        for col in (
            "swing_low_20d_1d",
            "ema_50_4h",
            "bb_width_4h",
            "ema_200_1d",
            "rsi",
            "ema_21",
            "lower_wick_ratio",
            "close_pos_ratio",
            "rel_vol",
        ):
            df[col] = 1.0
        return df

    def test_matches_momentum_radar_and_ready_masks(self):
        from strategies.mtf_sniper.market.data_guards import (
            data_ready_mask,
            finite_series_mask,
        )
        from strategies.mtf_sniper.signals.momentum_radar import MomentumRadar
        from strategies.mtf_sniper.signals.sensors.anomaly_waterfall import (
            _WATERFALL_FINITE_COLS,
        )

        df = self._ready_df(180)
        df.loc[110, ["open", "close", "high", "low", "volume"]] = [
            100.0,
            96.0,
            100.0,
            95.5,
            520.0,
        ]
        df.loc[115, ["open", "close", "high", "low", "volume"]] = [
            100.0,
            95.0,
            100.0,
            94.5,
            550.0,
        ]
        df.loc[130, ["open", "close", "high", "low", "volume"]] = [
            99.0,
            94.0,
            99.0,
            93.5,
            450.0,
        ]
        df.loc[150, ["open", "close", "high", "low", "volume"]] = [
            99.0,
            93.0,
            99.0,
            92.5,
            450.0,
        ]
        df.loc[150, "ema_50_4h"] = float("nan")

        sensor = WaterfallMomentumSensor()
        mask = sensor.scan_mask(df, "BTC/USDT:USDT")
        assert mask is not None
        ready = (data_ready_mask(df) & finite_series_mask(df, _WATERFALL_FINITE_COLS)).to_numpy(
            dtype=bool, na_value=False
        )
        expected = [
            (pos, ev)
            for pos, ev in MomentumRadar.find_candidates(
                df, cooldown_bars=12, directional_cooldown=True
            )
            if "SHORT" in ev and ready[pos]
        ]
        expected_bars = [pos for pos, _ in expected]
        actual_bars = [int(i) for i in np.flatnonzero(mask)]
        self.assertEqual(actual_bars, expected_bars)
        self.assertNotIn(150, actual_bars)

        feats = sensor.scan_mask_features(df, np.asarray(actual_bars, dtype=int))
        for pos, ev in expected:
            self.assertEqual(feats[pos]["event_trigger"], ev)
            self.assertEqual(feats[pos]["signal_source"], "momentum_radar")
            self.assertEqual(feats[pos]["setup_name"], "waterfall_collapse")

    def test_waterfall_sensor_emits_events(self):
        df = self._ready_df(140)
        df.loc[110, ["open", "close", "high", "low", "volume"]] = [
            100.0,
            96.0,
            100.0,
            95.5,
            520.0,
        ]
        events = WaterfallMomentumSensor().scan(df, "BTC/USDT:USDT")
        self.assertEqual([e.candle_index for e in events], [110])
        self.assertEqual(events[0].side, TradeSide.SHORT)
        self.assertEqual(events[0].sensor_name, "waterfall_momentum")


class TestMarketRegimeSensor(unittest.TestCase):
    def test_absent_or_unknown_1w_returns_empty_and_false_mask(self):
        df = _base_df(130)
        sensor = MarketRegimeSensor()
        self.assertFalse(sensor.scan_mask(df, "BTC/USDT:USDT").any())
        self.assertEqual(sensor.scan(df, "BTC/USDT:USDT"), [])

        df["market_regime_1w"] = "unknown"
        self.assertFalse(sensor.scan_mask(df, "BTC/USDT:USDT").any())
        self.assertEqual(sensor.scan(df, "BTC/USDT:USDT"), [])

    def test_context_event_enriches_directional_without_standalone_candidate(self):
        df = _base_df(130)
        df["market_regime_1w"] = "cycle_top"
        df["macdhist_1w"] = -250.0
        df["adx_1w"] = 42.0
        df["rsi_1w"] = 58.0
        df["is_bear_wave_1w"] = True
        df["is_bull_wave_1w"] = False

        sensor = MarketRegimeSensor()
        ctx_events = sensor.scan(df, "BTC/USDT:USDT", candle_index=110)
        self.assertEqual(len(ctx_events), 1)
        self.assertEqual(ctx_events[0].sensor_type, SensorType.CONTEXT)
        self.assertIsNone(ctx_events[0].side)
        self.assertEqual(ctx_events[0].raw_metrics["regime_1w"], "cycle_top")

        builder = CandidateBuilder(cooldown_bars=0)
        # Context alone produces zero trade candidates
        self.assertEqual(builder.process(ctx_events), [])

        # Context paired with directional event enriches raw_features cleanly
        dir_event = _event("short_structure", SensorType.PATTERN, TradeSide.SHORT, 110)
        cands = builder.process([*ctx_events, dir_event])
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].side, TradeSide.SHORT)
        self.assertEqual(cands[0].confluence_count, 1)
        self.assertEqual(cands[0].raw_features["regime_1w"], "cycle_top")
        self.assertTrue(cands[0].raw_features["is_bear_wave_1w"])
        self.assertEqual(cands[0].raw_features["context_sensors"], ["market_regime"])


if __name__ == "__main__":
    unittest.main()
