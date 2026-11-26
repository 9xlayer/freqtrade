"""Unit tests for DirectionalTradingEngine and EntryPipeline (P2)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import RejectionReasonCode, TradeSide
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
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
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
        policy = LongPolicy()
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

    def test_anomaly_long_disabled_by_default(self):
        policy = LongPolicy()
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
        # Extreme oversold 4h (> -10%) and 1d (> -20%) with valid RSI
        row = {
            "open": 50000.0,
            "high": 50100.0,
            "low": 49000.0,
            "close": 49200.0,
            "swing_low_20d_1d": 52000.0,  # floor_dist < 0
            "ema_50_4h": 55000.0,  # -10.5% (climax overextended beyond -10%)
            "ema_50_1d": 62000.0,  # -20.6% (climax overextended beyond -20%)
            "ema_200_1d": 65000.0,
            "lower_wick_ratio": 0.15,
            "close_pos_ratio": 0.18,
            "rsi": 30.0,
            "ema_21": 49800.0,
            "short_score": 80.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)
        self.assertIn(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value, result.reasons)

    def test_waterfall_vacuum_deep_runway_accepted(self):
        """Deep vacuum momentum (-8% 4H / -18% 1D) within -10%/-20% band is accepted."""
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        cand = _candidate(TradeSide.SHORT, "waterfall_drop", features={})
        # Deep vacuum breakdown: -8.0% from 4H EMA50, -18.0% from 1D EMA50 (within -10% / -20% band)
        row = {
            "open": 50800.0,
            "high": 50900.0,
            "low": 50500.0,
            "close": 50600.0,
            "swing_low_20d_1d": 52000.0,  # floor_dist < 0 (vacuum)
            "ema_50_4h": 55000.0,  # -8.0% from 4H EMA50 (valid under -10% climax)
            "ema_50_1d": 61707.0,  # -18.0% from 1D EMA50 (valid under -20% climax)
            "ema_200_1d": 65000.0,
            "lower_wick_ratio": 0.12,
            "close_pos_ratio": 0.15,
            "rsi": 30.0,
            "ema_21": 51000.0,
            "short_score": 80.0,
        }
        result = policy.evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.metadata.get("setup"), "jev_hunter_waterfall_collapse")

    def test_waterfall_near_floor_without_surge_rejected(self):
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        cand = _candidate(TradeSide.SHORT, "waterfall_drop", features={})
        # Near floor (0.3% > 0) but volume surge is inadequate (rel_vol 1.5 < 3.0)
        row = {
            "open": 72800.0,
            "high": 72900.0,
            "low": 72100.0,
            "close": 72216.0,
            "swing_low_20d_1d": 72000.0,  # floor_dist = +0.3%
            "rel_vol": 1.5,
            "ema_50_4h": 74500.0,
            "ema_50_1d": 78000.0,
            "ema_200_1d": 75000.0,
            "lower_wick_ratio": 0.10,
            "close_pos_ratio": 0.10,
            "rsi": 30.0,
            "ema_21": 72600.0,
            "short_score": 80.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)
        self.assertIn(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value, result.reasons)

    def test_waterfall_near_floor_with_surge_accepted(self):
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        cand = _candidate(TradeSide.SHORT, "waterfall_drop", features={})
        # Near floor (0.3% > 0) with strong volume surge (rel_vol 3.5 >= 3.0)
        row = {
            "open": 72800.0,
            "high": 72900.0,
            "low": 72100.0,
            "close": 72216.0,
            "swing_low_20d_1d": 72000.0,  # floor_dist = +0.3%
            "rel_vol": 3.5,
            "ema_50_4h": 74500.0,
            "ema_50_1d": 78000.0,
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

    def test_waterfall_eth_rejected_above_ema50_4h(self):
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        cand = TradeCandidate(
            candidate_id="ETH_short_0_105",
            pair="ETH/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="waterfall_drop",
            secondary_sensors=[],
            confluence_count=1,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candle_index=105,
            raw_features={},
        )
        row = {
            "open": 3100.0,
            "high": 3110.0,
            "low": 3040.0,
            "close": 3050.0,
            "swing_low_20d_1d": 2900.0,
            "ema_50_4h": 3000.0,
            "ema_50_1d": 3300.0,
            "ema_200_1d": 3200.0,
            "macdhist_4h": 5.0,
            "bb_width_4h": 0.08,
            "lower_wick_ratio": 0.10,
            "close_pos_ratio": 0.10,
            "rsi": 40.0,
            "ema_21": 3080.0,
            "short_score": 80.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)
        self.assertIn("REJECT_REGIME_INCOMPATIBLE", result.reasons)

    def test_waterfall_eth_accepted_below_ema50_4h(self):
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        cand = TradeCandidate(
            candidate_id="ETH_short_0_105",
            pair="ETH/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="waterfall_drop",
            secondary_sensors=[],
            confluence_count=1,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candle_index=105,
            raw_features={},
        )
        row = {
            "open": 2980.0,
            "high": 2990.0,
            "low": 2930.0,
            "close": 2940.0,
            "swing_low_20d_1d": 2800.0,
            "ema_50_4h": 3000.0,
            "ema_50_1d": 3300.0,
            "ema_200_1d": 3200.0,
            "macdhist_4h": -5.0,
            "bb_width_4h": 0.08,
            "lower_wick_ratio": 0.10,
            "close_pos_ratio": 0.10,
            "rsi": 40.0,
            "ema_21": 2960.0,
            "short_score": 80.0,
        }
        result = policy.evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.metadata.get("setup"), "jev_hunter_waterfall_collapse")

    def test_waterfall_eth_in_vacuum_rejected_if_above_ema50_4h(self):
        """ETH in vacuum breakdown must STILL be rejected if price is above 4H EMA50."""
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        cand = TradeCandidate(
            candidate_id="ETH_short_0_106",
            pair="ETH/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="waterfall_drop",
            secondary_sensors=[],
            confluence_count=1,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candle_index=106,
            raw_features={},
        )
        # Clean floor breakdown (floor_dist <= 0) and high surge vol (rel_vol 3.5),
        # but close 2800 > ema_50_4h 2750 (+1.8%)
        row = {
            "open": 2850.0,
            "high": 2860.0,
            "low": 2790.0,
            "close": 2800.0,
            "swing_low_20d_1d": 2800.0,  # floor_dist = 0.0 (vacuum)
            "rel_vol": 3.5,
            "ema_50_4h": 2750.0,  # Above 4H EMA50!
            "ema_50_1d": 3100.0,
            "ema_200_1d": 3000.0,
            "lower_wick_ratio": 0.10,
            "close_pos_ratio": 0.10,
            "rsi": 35.0,
            "ema_21": 2820.0,
            "short_score": 80.0,
        }
        result = policy.evaluate(cand, row)
        self.assertFalse(result.is_valid)
        self.assertIn(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value, result.reasons)


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
    def test_live_factory_disables_anomalies_by_default(self):
        strat = type("S", (), {"block_macro_bull_shorts": False})()
        pipe = EntryPipeline.live(strat)
        self.assertFalse(pipe.engine.long_policy.config.allow_anomaly_entries)
        self.assertFalse(pipe.engine.short_policy.config.allow_anomaly_entries)

    def test_live_factory_enables_waterfall_when_requested(self):
        strat = type("S", (), {"block_macro_bull_shorts": False, "enable_waterfall": True})()
        pipe = EntryPipeline.live(strat)
        self.assertFalse(pipe.engine.long_policy.config.allow_anomaly_entries)
        self.assertTrue(pipe.engine.short_policy.config.allow_anomaly_entries)

    def test_live_factory_wires_strategy_policy_parameters(self):
        strat = type(
            "S",
            (),
            {
                "block_macro_bull_shorts": False,
                "enable_waterfall": True,
                "max_rejection_wick_ratio": 0.35,
                "max_close_pos_ratio_short": 0.35,
                "short_min_rsi_entry": 26.0,
            },
        )()
        pipe = EntryPipeline.live(strat)
        self.assertAlmostEqual(pipe.engine.short_policy.config.max_rejection_wick_ratio, 0.35)
        self.assertAlmostEqual(pipe.engine.short_policy.config.max_close_pos_ratio, 0.35)
        self.assertAlmostEqual(pipe.engine.short_policy.config.min_rsi_entry, 26.0)

    def test_waterfall_low_short_score_not_rejected_against_zero(self):
        cand = _candidate(TradeSide.SHORT, "waterfall_drop", features={"short_score": 50.0})
        row = {
            "open": 100.0,
            "high": 100.5,
            "low": 94.8,
            "close": 95.0,
            "lower_wick_ratio": 0.05,
            "close_pos_ratio": 0.05,
            "rsi": 35.0,
            "ema_21": 96.0,
            "ema_50_4h": 97.0,
            "ema_50_1d": 102.0,
            "ema_200_1d": 105.0,
            "swing_low_20d_1d": 96.0,
            "macdhist_4h": -5.0,
            "bb_width_4h": 0.06,
            "rel_vol": 4.0,
            "atr_pct": 60.0,
            "short_score": 50.0,
        }
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        res = policy.evaluate(cand, row)
        self.assertTrue(res.is_valid)
        self.assertGreaterEqual(res.score, 0.70)

    def test_short_policy_fails_closed_on_nan_indicators(self):
        cand = TradeCandidate(
            candidate_id="ETH_SHORT_20240101T0000_105",
            pair="ETH/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="waterfall_drop",
            secondary_sensors=[],
            confluence_count=1,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candle_index=105,
            raw_features={"setup_name": "waterfall_collapse", "short_score": 80.0},
        )
        row = {
            "open": 100.0,
            "high": 100.5,
            "low": 98.0,
            "close": 98.2,
            "swing_low_20d_1d": 99.0,
            "ema_50_4h": float("nan"),
            "bb_width_4h": float("nan"),
            "atr_pct": float("nan"),
            "rel_vol": 3.5,
            "lower_wick_ratio": float("nan"),
            "close_pos_ratio": float("nan"),
            "rsi": float("nan"),
            "ema_21": float("nan"),
            "short_score": 80.0,
        }
        policy = ShortPolicy(ShortPolicyConfig(allow_anomaly_entries=True))
        res = policy.evaluate(cand, row)
        self.assertFalse(res.is_valid)
        self.assertIn(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value, res.reasons)

    def test_long_policy_fails_closed_on_nan_and_non_positive_close(self):
        cand = _candidate(TradeSide.LONG, "volume_surge", features={"long_score": 85.0})
        policy = LongPolicy(LongPolicyConfig(allow_anomaly_entries=True))
        nan_row = {
            "close": 100.0,
            "upper_wick_ratio": float("nan"),
            "close_pos_ratio": float("nan"),
            "rsi": float("nan"),
            "ema_21": float("nan"),
        }
        self.assertFalse(policy.evaluate(cand, nan_row).is_valid)
        self.assertFalse(policy.evaluate(cand, {"close": 0.0, "rsi": 55.0}).is_valid)

    def test_entry_pipeline_dedups_same_bar_accepted_and_gates_unready_radar(self):
        from types import SimpleNamespace

        import pandas as pd

        n = 120
        dates = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        df = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.0] * n,
                "volume": [10.0] * n,
                "body": [1.0] * n,
                "total_range": [2.0] * n,
                "atr": [1.0] * n,
                "atr_pct": [50.0] * n,
                "close_1h": [100.0] * n,
                "ema_50_1h": [100.0] * n,
                "macdhist_1h": [-0.1] * n,
                "macdhist_prev_1h": [0.0] * n,
                "rsi_max_24_1h": [60.0] * n,
                "rsi_min_24_1h": [40.0] * n,
                "adx_4h": [25.0] * n,
                "macdhist_4h": [-0.2] * n,
                "is_bull_wave_4h": [0] * n,
                "is_bear_wave_4h": [1] * n,
                "ema_50_1d": [105.0] * n,
                "macdhist_1d": [-0.3] * n,
                "pdh_1d": [104.0] * n,
                "swing_low": [98.0] * n,
                "swing_high": [102.0] * n,
                "swing_low_20d_1d": [102.0] * n,
                "ema_50_4h": [103.0] * n,
                "bb_width_4h": [0.05] * n,
                "bb_width_1d": [0.15] * n,
                "range_24h": [0.03] * n,
                "ema_10": [98.0] * n,
                "ema_200_1d": [110.0] * n,
                "dist_from_ema_200_1d": [0.1] * n,
                "is_macro_bull_run_1d": [0] * n,
                "is_macro_bear_market_1d": [0] * n,
                "is_deep_capitulation_1d": [0] * n,
                "rsi": [35.0] * n,
                "ema_21": [96.0] * n,
                "upper_wick_ratio": [0.05] * n,
                "lower_wick_ratio": [0.05] * n,
                "close_pos_ratio": [0.05] * n,
                "rel_vol": [5.0] * n,
                "short_score": [85.0] * n,
                "long_score": [50.0] * n,
            }
        )
        # Bar 105 triggers MomentumRadar MEGA_SURGE_SHORT, but atr_pct is NaN -> must be gated out.
        df.loc[105, ["open", "high", "low", "close", "volume"]] = [100.0, 100.0, 95.0, 95.2, 60.0]
        df.loc[105, "atr_pct"] = float("nan")
        # Bar 118 triggers MomentumRadar after 12-bar cooldown with all valid fields.
        df.loc[118, ["open", "high", "low", "close", "volume"]] = [100.0, 100.0, 95.0, 95.2, 60.0]

        pipe = EntryPipeline(
            short_config=ShortPolicyConfig(allow_anomaly_entries=True),
        )
        c1 = _candidate(
            TradeSide.SHORT, "setup:sniper_short", idx=10, features=_setup_features("short", 85.0)
        )
        c2 = _candidate(
            TradeSide.SHORT,
            "waterfall_drop",
            idx=10,
            features={"setup_name": "waterfall_collapse", "short_score": 80.0},
        )
        pipe.setup_composer.compose_suite = lambda *_a, **_kw: [c1, c2]  # type: ignore[method-assign]
        res = pipe.run(df, "BTC/USDT:USDT")
        self.assertEqual(len(res.accepted), 1)
        self.assertEqual(res.accepted[0][0].candidate_id, c1.candidate_id)

        def _p(v: object) -> SimpleNamespace:
            return SimpleNamespace(value=v)

        strat = SimpleNamespace(
            block_macro_bull_shorts=False,
            enable_waterfall=True,
            custom_pair_params={},
            block_high_vol=_p(True),
            high_vol_pct=_p(90.0),
            min_adx_4h=_p(18.0),
            filter_wick_rejection=_p(True),
            max_rejection_wick_ratio=_p(0.35),
            min_close_pos_ratio_long=_p(0.65),
            max_close_pos_ratio_short=_p(0.35),
            filter_pdh_ceiling=_p(True),
            block_4h_counter_wave=_p(True),
            block_1d_counter_wave=_p(True),
            long_max_dist_ema21=_p(3.3),
            long_candle_body_ratio_min=_p(0.63),
            long_lookback_resistance=_p(15),
            long_loc_tolerance_1h=_p(0.988),
            long_max_1h_rsi_exhaustion=_p(57.1),
            long_block_1d_macd=_p(False),
            long_overextended_dist_max=_p(0.53),
            long_max_rsi_entry=_p(68.1),
            short_max_dist_ema21=_p(1.0),
            short_candle_body_ratio_min=_p(0.62),
            short_loc_tolerance_1h=_p(0.991),
            short_min_1h_rsi_exhaustion=_p(55.1),
            short_min_rsi_entry=_p(27.8),
        )
        live_pipe = EntryPipeline.live(strat)
        live_cands = live_pipe.setup_composer.compose_suite(
            live_pipe.sensor_suite, df, "BTC/USDT:USDT"
        )
        radar_bars = [c.candle_index for c in live_cands if c.primary_sensor == "waterfall_drop"]
        self.assertNotIn(105, radar_bars)
        self.assertIn(118, radar_bars)

    def test_engine_dispatches_waterfall_and_custom_subsystem_policies(self):
        from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine
        from strategies.mtf_sniper.directional.short.waterfall_policy import (
            WaterfallShortConfig,
            WaterfallShortPolicy,
        )

        class _CustomSubPolicy:
            def evaluate(self, candidate, row):
                return PolicyEvaluationResult(
                    is_valid=True,
                    score=0.95,
                    quality_grade="A+",
                    reasons=["ACCEPT"],
                    suggested_stop_distance_pct=0.015,
                    target_rr=2.5,
                    metadata={"subsystem": "custom_squeeze", "entry_tag": "custom_squeeze"},
                )

        wf_policy = WaterfallShortPolicy(WaterfallShortConfig(enabled=True))
        engine = DirectionalTradingEngine(
            short_config=ShortPolicyConfig(allow_anomaly_entries=True),
            waterfall_policy=wf_policy,
            subsystem_policies={"custom_squeeze": _CustomSubPolicy()},
        )
        self.assertIsInstance(engine.subsystem_policies["waterfall"], WaterfallShortPolicy)

        wf_cand = _candidate(
            TradeSide.SHORT, "waterfall_drop", features={"subsystem": "waterfall"}
        )
        wf_row = {
            "open": 50600.0,
            "close": 49500.0,
            "high": 50650.0,
            "low": 49450.0,
            "swing_low_20d_1d": 50000.0,
            "ema_50_4h": 54000.0,
            "ema_50_1d": 60000.0,
            "ema_200_1d": 58000.0,
            "macdhist_4h": -20.0,
            "bb_width_4h": 0.08,
            "rel_vol": 4.0,
            "atr_pct": 50.0,
            "lower_wick_ratio": 0.05,
            "close_pos_ratio": 0.08,
            "rsi": 35.0,
            "ema_21": 50000.0,
        }
        wf_res = engine.evaluate(wf_cand, wf_row)
        self.assertTrue(wf_res.is_valid)
        self.assertEqual(wf_res.metadata.get("subsystem"), "waterfall")

        custom_cand = _candidate(
            TradeSide.SHORT, "setup:custom_squeeze", features={"subsystem": "custom_squeeze"}
        )
        custom_res = engine.evaluate(custom_cand, wf_row)
        self.assertTrue(custom_res.is_valid)
        self.assertEqual(custom_res.metadata.get("entry_tag"), "custom_squeeze")


if __name__ == "__main__":
    unittest.main()
