"""Unit tests for DirectionalTradingEngine and EntryPipeline (P2)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

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


class TestLongPolicy(unittest.TestCase):
    def test_accept_a80_in_bull(self):
        policy = LongPolicy(LongPolicyConfig(enable_hunter_long=False))
        cand = _candidate(TradeSide.LONG, "a80_plus", features={"long_score": 85.0})
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
        self.assertEqual(result.metadata["entry_tag"], "long_A80plus")

    def test_reject_bear_regime(self):
        policy = LongPolicy()
        cand = _candidate(TradeSide.LONG, "a80_plus", features={"long_score": 90.0})
        row = {
            "close": 80.0,
            "ema_50_1d": 90.0,
            "ema_200_1d": 95.0,
            "is_macro_bear_market_1d": 1,
            "upper_wick_ratio": 0.10,
            "close_pos_ratio": 0.85,
            "rsi": 55.0,
            "ema_21": 81.0,
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
    def test_accept_waterfall_in_bear(self):
        policy = ShortPolicy(ShortPolicyConfig(block_macro_bull_shorts=False))
        cand = _candidate(TradeSide.SHORT, "waterfall_drop", features={})
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
        }
        result = policy.evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertIn("waterfall", result.metadata["entry_tag"])

    def test_reject_lower_wick(self):
        policy = ShortPolicy()
        cand = _candidate(
            TradeSide.SHORT, "a80_plus", features={"short_score": 85.0}
        )
        row = {
            "close": 95.0,
            "ema_50_1d": 100.0,
            "ema_200_1d": 110.0,
            "lower_wick_ratio": 0.40,
            "close_pos_ratio": 0.20,
            "rsi": 40.0,
            "ema_21": 96.0,
            "swing_low_20d_1d": 80.0,
            "short_score": 85.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)


class TestDirectionalEngine(unittest.TestCase):
    def test_routes_by_side(self):
        engine = DirectionalTradingEngine()
        long_c = _candidate(TradeSide.LONG, "a80_plus", features={"long_score": 85.0})
        short_c = _candidate(TradeSide.SHORT, "a80_plus", features={"short_score": 85.0}, idx=106)
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
            "ema_21": 90.5,  # dist <= short_max_dist_ema21 (1.0%)
            "swing_low_20d_1d": 80.0,
            "short_score": 85.0,
        }
        self.assertTrue(engine.evaluate(long_c, bull_row).is_valid)
        self.assertTrue(engine.evaluate(short_c, bear_row).is_valid)


class TestEntryPipeline(unittest.TestCase):
    def test_pipeline_emits_long_a80(self):
        n = 120
        dates = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        df = pd.DataFrame(
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
                "rsi": np.full(n, 55.0),
                "ema_21": np.full(n, 99.0),
                "ema_50_1d": np.full(n, 90.0),
                "ema_200_1d": np.full(n, 80.0),
                "is_macro_bear_market_1d": np.zeros(n),
                "upper_wick_ratio": np.full(n, 0.10),
                "lower_wick_ratio": np.full(n, 0.10),
                "close_pos_ratio": np.full(n, 0.80),
                "dist_from_ema_200_1d": np.full(n, 0.2),
                "pdh_1d": np.full(n, 120.0),
                "swing_low_20d_1d": np.full(n, 70.0),
            }
        )
        # Decisive green A80 candle
        df.loc[105, ["open", "close", "high", "low", "long_score", "close_pos_ratio"]] = [
            99.0, 101.0, 101.2, 98.8, 85.0, 0.90
        ]
        pipeline = EntryPipeline(
            long_config=LongPolicyConfig(enable_hunter_long=False),
            short_config=ShortPolicyConfig(),
        )
        result = pipeline.run(df, "BTC/USDT:USDT")
        self.assertTrue(bool(result.enter_long.iloc[105]))
        self.assertEqual(result.enter_tag.iloc[105], "long_A80plus")
        self.assertFalse(bool(result.enter_short.iloc[105]))


if __name__ == "__main__":
    unittest.main()
