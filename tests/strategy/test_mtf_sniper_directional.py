"""Unit tests for DirectionalTradingEngine and EntryPipeline (P2)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from strategies.mtf_sniper.core.contracts import TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine
from strategies.mtf_sniper.directional.long.policy import LongPolicy, LongPolicyConfig
from strategies.mtf_sniper.directional.short.policy import ShortPolicy, ShortPolicyConfig
from strategies.mtf_sniper.execution.entry_pipeline import EntryPipeline


def _candidate(
    side: TradeSide,
    primary: str,
    *,
    confluence: int = 1,
    features: dict | None = None,
    idx: int = 105,
) -> TradeCandidate:
    return TradeCandidate(
        candidate_id=f"BTC_{side.value}_0_{idx}",
        pair="BTC/USDT:USDT",
        side=side,
        primary_sensor=primary,
        secondary_sensors=[],
        confluence_count=confluence,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        candle_index=idx,
        raw_features=features or {},
    )


def _setup_features(side: str, score: float = 85.0) -> dict:
    key = "long_score" if side == "long" else "short_score"
    return {
        "setup_complete": True,
        "setup_name": f"setup:sniper_{side}",
        "legacy_sniper": side,
        key: score,
    }


class TestLongPolicy(unittest.TestCase):
    def test_accept_complete_setup(self):
        policy = LongPolicy(LongPolicyConfig(enable_hunter_long=False))
        cand = _candidate(
            TradeSide.LONG,
            "setup:sniper_long",
            features=_setup_features("long", 85.0),
        )
        row = {
            "close": 100.0,
            "ema_50_1d": 90.0,
            "ema_200_1d": 80.0,
            "is_macro_bear_market_1d": 0,
            "upper_wick_ratio": 0.10,
            "close_pos_ratio": 0.85,
            "rsi": 60.0,
            "ema_21": 99.0,
            "dist_from_ema_200_1d": 0.20,
            "pdh_1d": 110.0,
            "long_score": 85.0,
        }
        result = policy.evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertTrue(result.metadata.get("setup_complete"))

    def test_reject_incomplete_without_anomaly_path(self):
        policy = LongPolicy()
        cand = _candidate(TradeSide.LONG, "a80_plus", features={"long_score": 90.0})
        row = {
            "close": 100.0,
            "ema_50_1d": 90.0,
            "ema_200_1d": 80.0,
            "is_macro_bear_market_1d": 0,
            "upper_wick_ratio": 0.10,
            "close_pos_ratio": 0.85,
            "rsi": 55.0,
            "ema_21": 99.0,
            "long_score": 90.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)

    def test_hunter_long_disabled_by_default(self):
        policy = LongPolicy(LongPolicyConfig(enable_hunter_long=False))
        cand = _candidate(TradeSide.LONG, "volume_surge", features={})
        row = {
            "close": 100.0,
            "ema_50_1d": 90.0,
            "ema_200_1d": 80.0,
            "is_macro_bear_market_1d": 0,
            "upper_wick_ratio": 0.10,
            "close_pos_ratio": 0.80,
            "rsi": 60.0,
            "ema_21": 99.5,
            "pdh_1d": 110.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)


class TestShortPolicy(unittest.TestCase):
    def test_accept_complete_setup(self):
        policy = ShortPolicy(ShortPolicyConfig(block_macro_bull_shorts=False))
        cand = _candidate(
            TradeSide.SHORT,
            "setup:sniper_short",
            features=_setup_features("short", 85.0),
        )
        row = {
            "close": 90.0,
            "ema_50_1d": 100.0,
            "ema_200_1d": 110.0,
            "is_macro_bear_market_1d": 1,
            "is_macro_bull_run_1d": 0,
            "is_bear_wave_4h": 1,
            "is_bull_wave_4h": 0,
            "macdhist_4h": -0.5,
            "lower_wick_ratio": 0.10,
            "close_pos_ratio": 0.20,
            "rsi": 35.0,
            "ema_21": 91.0,
            "swing_low_20d_1d": 80.0,
            "short_score": 85.0,
        }
        result = policy.evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertTrue(result.metadata.get("setup_complete"))

    def test_reject_incomplete_anomaly(self):
        policy = ShortPolicy()
        cand = _candidate(TradeSide.SHORT, "waterfall_drop", features={})
        row = {
            "close": 95.0,
            "ema_50_1d": 100.0,
            "ema_200_1d": 110.0,
            "lower_wick_ratio": 0.10,
            "close_pos_ratio": 0.20,
            "rsi": 40.0,
            "ema_21": 96.0,
            "swing_low_20d_1d": 80.0,
            "short_score": 85.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)

    def test_waterfall_dual_regime_vacuum_accepted(self):
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        cand = _candidate(TradeSide.SHORT, "waterfall_drop", features={})
        # Floor breakdown: close 72,000 below swing_low 72,500 (floor_dist < 0)
        row = {
            "open": 72800.0,
            "high": 72900.0,
            "low": 71900.0,
            "close": 72000.0,
            "swing_low_20d_1d": 72500.0,
            "ema_50_4h": 74500.0,  # -3.3%
            "ema_50_1d": 78000.0,  # -7.7%
            "ema_200_1d": 75000.0,
            "lower_wick_ratio": 0.10,
            "close_pos_ratio": 0.10,
            "rsi": 30.0,
            "ema_21": 72600.0,
            "short_score": 80.0,
        }
        result = policy.evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.metadata.get("setup"), "jev_hunter_waterfall_collapse")

    def test_waterfall_climax_overextended_rejected(self):
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        cand = _candidate(TradeSide.SHORT, "waterfall_drop", features={})
        # Extreme oversold 4h (> -6%) and 1d (> -15%)
        row = {
            "open": 50000.0,
            "high": 50100.0,
            "low": 49000.0,
            "close": 49200.0,
            "swing_low_20d_1d": 52000.0,  # floor_dist < 0
            "ema_50_4h": 55000.0,  # -10.5% (climax)
            "ema_50_1d": 62000.0,  # -20.6% (climax)
            "ema_200_1d": 65000.0,
            "lower_wick_ratio": 0.15,
            "close_pos_ratio": 0.18,
            "rsi": 22.0,
            "ema_21": 49800.0,
            "short_score": 80.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)


class TestDirectionalEngine(unittest.TestCase):
    def test_routes_by_side(self):
        engine = DirectionalTradingEngine()
        long_c = _candidate(
            TradeSide.LONG,
            "setup:sniper_long",
            features=_setup_features("long", 85.0),
        )
        short_c = _candidate(
            TradeSide.SHORT,
            "setup:sniper_short",
            features=_setup_features("short", 85.0),
            idx=106,
        )
        bull_row = {
            "close": 100.0,
            "ema_50_1d": 90.0,
            "ema_200_1d": 80.0,
            "is_macro_bear_market_1d": 0,
            "upper_wick_ratio": 0.10,
            "close_pos_ratio": 0.85,
            "rsi": 60.0,
            "ema_21": 99.0,
            "dist_from_ema_200_1d": 0.2,
            "pdh_1d": 110.0,
            "long_score": 85.0,
        }
        bear_row = {
            "close": 90.0,
            "ema_50_1d": 100.0,
            "ema_200_1d": 110.0,
            "lower_wick_ratio": 0.10,
            "close_pos_ratio": 0.20,
            "rsi": 40.0,
            "ema_21": 90.5,
            "swing_low_20d_1d": 80.0,
            "short_score": 85.0,
        }
        self.assertTrue(engine.evaluate(long_c, bull_row).is_valid)
        self.assertTrue(engine.evaluate(short_c, bear_row).is_valid)


class TestEntryPipeline(unittest.TestCase):
    def test_live_factory_disables_hunter(self):
        strat = type("S", (), {"block_macro_bull_shorts": False})()
        pipe = EntryPipeline.live(strat)
        self.assertIsNone(pipe.hunter_escalation)
        self.assertFalse(pipe.engine.long_policy.config.allow_anomaly_entries)
        self.assertFalse(pipe.engine.short_policy.config.allow_anomaly_entries)

    def test_live_factory_enables_waterfall_when_requested(self):
        strat = type("S", (), {"block_macro_bull_shorts": False, "enable_waterfall": True})()
        pipe = EntryPipeline.live(strat)
        self.assertIsNone(pipe.hunter_escalation)
        self.assertFalse(pipe.engine.long_policy.config.allow_anomaly_entries)
        self.assertTrue(pipe.engine.short_policy.config.allow_anomaly_entries)


if __name__ == "__main__":
    unittest.main()
