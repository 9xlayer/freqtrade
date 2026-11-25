"""Part sensors + SetupComposer + deterministic EntryPipeline (AND-mask parity)."""

from __future__ import annotations

import unittest
from datetime import UTC
from types import SimpleNamespace

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.common_gates import (
    data_ready_mask,
    sideway_safe_mask,
    volatility_safe_mask,
)
from strategies.mtf_sniper.directional.setup_composer import SetupComposer
from strategies.mtf_sniper.directional.setup_recipes import SETUP_SNIPER_SHORT
from strategies.mtf_sniper.directional.short.setups import (
    SHORT_SNIPER_PART_NAMES,
    build_raw_sniper_short_mask,
    build_short_sniper_parts,
)
from strategies.mtf_sniper.execution.entry_pipeline import EntryPipeline, apply_entry_pipeline
from strategies.mtf_sniper.signals.sensor_suite import SensorSuite
from strategies.mtf_sniper.signals.sensors.pattern_sniper_parts import (
    _PARTS_CACHE_ATTR,
    cached_sniper_parts,
    short_sniper_part_sensors,
)


def _param(value):
    return SimpleNamespace(value=value)


def _make_strategy(**overrides):
    base = {
        "high_vol_pct": _param(90.0),
        "block_high_vol": _param(True),
        "min_adx_4h": _param(18.0),
        "short_max_dist_ema21": _param(1.0),
        "short_candle_body_ratio_min": _param(0.62),
        "short_loc_tolerance_1h": _param(0.991),
        "short_min_1h_rsi_exhaustion": _param(55.1),
        "short_min_rsi_entry": _param(27.8),
        "short_cooldown_candles": _param(1),
        "long_max_dist_ema21": _param(3.3),
        "long_candle_body_ratio_min": _param(0.63),
        "long_loc_tolerance_1h": _param(1.008),
        "long_max_1h_rsi_exhaustion": _param(62.4),
        "long_lookback_resistance": _param(22),
        "long_max_rsi_entry": _param(67.6),
        "long_cooldown_candles": _param(1),
        "long_overextended_dist_max": _param(0.59),
        "long_block_1d_macd": _param(True),
        "block_4h_counter_wave": _param(True),
        "block_1d_counter_wave": _param(True),
        "filter_wick_rejection": _param(True),
        "max_rejection_wick_ratio": _param(0.25),
        "min_close_pos_ratio_long": _param(0.65),
        "max_close_pos_ratio_short": _param(0.35),
        "filter_pdh_ceiling": _param(True),
        "enable_hunter_long": False,
        "enable_jev_hunter": False,
        "block_macro_bull_shorts": False,
        "_get_pair_params": lambda pair: {},
    }
    base.update(overrides)
    strat = SimpleNamespace(**base)
    from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper

    strat.apply_cooldown = StrategyMTFSniper.apply_cooldown
    return strat


def _full_df(n: int = 150) -> pd.DataFrame:
    idx = pd.RangeIndex(n)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="15min"),
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 100.0),
            "body": np.full(n, 1.0),
            "total_range": np.full(n, 2.0),
            "atr": np.full(n, 1.0),
            "atr_pct": np.full(n, 50.0),
            "rsi": np.full(n, 50.0),
            "ema_10": np.full(n, 100.0),
            "ema_21": np.full(n, 100.0),
            "ema_200": np.full(n, 95.0),
            "swing_low": np.full(n, 98.0),
            "swing_high": np.full(n, 102.0),
            "upper_wick_ratio": np.full(n, 0.1),
            "lower_wick_ratio": np.full(n, 0.1),
            "close_pos_ratio": np.full(n, 0.5),
            "long_score": np.full(n, 50.0),
            "short_score": np.full(n, 50.0),
            "close_1h": np.full(n, 100.0),
            "ema_50_1h": np.full(n, 100.0),
            "macdhist_1h": np.full(n, 0.0),
            "macdhist_prev_1h": np.full(n, 0.0),
            "rsi_max_24_1h": np.full(n, 60.0),
            "rsi_min_24_1h": np.full(n, 40.0),
            "adx_4h": np.full(n, 25.0),
            "macdhist_4h": np.full(n, 0.0),
            "is_bull_wave_4h": np.full(n, 0),
            "is_bear_wave_4h": np.full(n, 0),
            "ema_50_1d": np.full(n, 90.0),
            "ema_200_1d": np.full(n, 80.0),
            "macdhist_1d": np.full(n, 0.1),
            "pdh_1d": np.full(n, 110.0),
            "dist_from_ema_200_1d": np.full(n, 0.2),
            "is_macro_bull_run_1d": np.full(n, 0),
            "is_macro_bear_market_1d": np.full(n, 0),
            "swing_low_20d_1d": np.full(n, 70.0),
            "is_deep_capitulation_1d": np.full(n, 0),
            "is_bull_wave_1d": np.full(n, 0),
            "bb_width_4h": np.full(n, 0.05),
            "bb_width_1d": np.full(n, 0.15),
            "range_24h": np.full(n, 0.03),
            "rel_vol": np.full(n, 1.0),
            "volume_mean_20": np.full(n, 100.0),
        },
        index=idx,
    )


def _force_short_sniper_bar(df: pd.DataFrame, i: int = 120) -> None:
    """Satisfy legacy short AND-mask at index i (BTC)."""
    df.loc[i, ["open", "close", "high", "low"]] = [100.0, 97.0, 100.0, 96.5]
    df.loc[i, "body"] = 3.0
    df.loc[i, "total_range"] = 3.5
    df.loc[i, "atr"] = 1.0
    df.loc[i, "swing_low"] = 98.0
    df.loc[i - 1, "close"] = 99.0
    df.loc[i, ["ema_10", "ema_21"]] = [97.4, 97.6]
    df.loc[i, "rsi"] = 35.0
    df.loc[i, "lower_wick_ratio"] = 0.10
    df.loc[i, "close_pos_ratio"] = 0.20
    df.loc[i, "short_score"] = 85.0
    df.loc[i, "close_1h"] = 100.0
    df.loc[i, "ema_50_1h"] = 100.5
    df.loc[i, "macdhist_1h"] = -0.1
    df.loc[i, "macdhist_prev_1h"] = 0.0
    df.loc[i, "rsi_max_24_1h"] = 60.0
    df.loc[i, "is_bull_wave_4h"] = 0
    df.loc[i, "is_macro_bull_run_1d"] = 0
    df.loc[i, "is_bull_wave_1d"] = 0
    df.loc[i, "swing_low_20d_1d"] = 70.0
    df.loc[i, "is_deep_capitulation_1d"] = 0
    df.loc[i, "atr_pct"] = 50.0
    df.loc[i, "adx_4h"] = 25.0
    df.loc[i, "volume"] = 100.0


class TestCombinationMaskLivePath(unittest.TestCase):
    def test_apply_entry_pipeline_fail_closed_missing_cols(self):
        strat = _make_strategy()
        df = _full_df(40)
        df = df.drop(columns=["ema_50_1d"])
        out = apply_entry_pipeline(strat, df, {"pair": "BTC/USDT:USDT"})
        self.assertEqual(int(out["enter_long"].sum()), 0)
        self.assertEqual(int(out["enter_short"].sum()), 0)

    def test_apply_entry_pipeline_writes_enter_columns(self):
        strat = _make_strategy()
        df = _full_df()
        out = apply_entry_pipeline(strat, df, {"pair": "BTC/USDT:USDT"})
        self.assertIn("enter_long", out.columns)
        self.assertIn("enter_short", out.columns)
        self.assertIn("enter_tag", out.columns)
        self.assertIn("mtf_sniper_live_entries", out.attrs)

    def test_parts_and_equals_mono_mask(self):
        strat = _make_strategy()
        df = _full_df()
        _force_short_sniper_bar(df, 120)
        pair = "BTC/USDT:USDT"
        ready = data_ready_mask(df)
        vol = volatility_safe_mask(strat, df)
        sideway = sideway_safe_mask(strat, df)
        parts = build_short_sniper_parts(
            strat,
            df,
            pair,
            {},
            data_ready=ready,
            cond_volatility_safe=vol,
            cond_sideway_safe=sideway,
        )
        and_parts = parts[SHORT_SNIPER_PART_NAMES[0]]
        for name in SHORT_SNIPER_PART_NAMES[1:]:
            and_parts = and_parts & parts[name]
        mono = build_raw_sniper_short_mask(
            strat,
            df,
            pair,
            {},
            data_ready=ready,
            cond_volatility_safe=vol,
            cond_sideway_safe=sideway,
        )
        pd.testing.assert_series_equal(and_parts.astype(bool), mono.astype(bool))

    def test_part_sensors_all_fire_on_sniper_bar(self):
        strat = _make_strategy()
        df = _full_df()
        _force_short_sniper_bar(df, 120)
        for part in short_sniper_part_sensors(strat):
            events = part.scan(df, "BTC/USDT:USDT", candle_index=120)
            self.assertEqual(len(events), 1, msg=part.name)

    def test_sniper_parts_cache_once_per_side(self):
        strat = _make_strategy()
        df = _full_df()
        _force_short_sniper_bar(df, 120)
        pair = "BTC/USDT:USDT"
        a = cached_sniper_parts(strat, df, pair, TradeSide.SHORT)
        b = cached_sniper_parts(strat, df, pair, TradeSide.SHORT)
        self.assertIs(a, b)
        self.assertIn(_PARTS_CACHE_ATTR, df.attrs)
        # Suite scan should reuse same cache entry
        for part in short_sniper_part_sensors(strat):
            part.scan(df, pair, candle_index=120)
        store = df.attrs[_PARTS_CACHE_ATTR]
        short_keys = [k for k in store if k[1] == "short"]
        self.assertEqual(len(short_keys), 1)

    def test_live_pipeline_full_parts_to_short_a80(self):
        """Parts → Builder → Composer → Policy → enter_tag."""
        strat = _make_strategy()
        df = _full_df()
        _force_short_sniper_bar(df, 120)
        out = apply_entry_pipeline(strat, df, {"pair": "BTC/USDT:USDT"})
        self.assertEqual(int(out.loc[120, "enter_short"]), 1)
        self.assertEqual(out.loc[120, "enter_tag"], "short_A80_plus")

    def test_incomplete_parts_do_not_enter(self):
        strat = _make_strategy()
        df = _full_df()
        # Only tweak structure-ish close without satisfying full mask
        df.loc[120, "close"] = 97.0
        df.loc[120, "open"] = 100.0
        out = apply_entry_pipeline(strat, df, {"pair": "BTC/USDT:USDT"})
        self.assertEqual(int(out.loc[120, "enter_short"]), 0)

    def test_live_suite_is_parts_only_no_anomaly(self):
        strat = _make_strategy()
        suite = SensorSuite.live(strat)
        names = {s.name for s in suite.sensors}
        for part in SHORT_SNIPER_PART_NAMES:
            self.assertIn(part, names)
        self.assertNotIn("volume_surge", names)
        self.assertNotIn("volatility_expansion", names)
        self.assertNotIn("waterfall_drop", names)
        self.assertNotIn("waterfall_momentum", names)
        self.assertNotIn("composite_sniper_short", names)
        self.assertNotIn("sniper_short_mask", names)

        wf_names = {s.name for s in SensorSuite.live(strat, include_waterfall=True).sensors}
        self.assertIn("waterfall_momentum", wf_names)

        pipe = EntryPipeline.live(strat)
        self.assertIsNone(pipe.hunter_escalation)
        self.assertEqual({s.name for s in pipe.sensor_suite.sensors}, names)

    def test_setup_composer_marks_complete_recipe(self):
        from datetime import datetime

        from strategies.mtf_sniper.core.contracts import TradeCandidate
        from strategies.mtf_sniper.core.enums import TradeSide

        cand = TradeCandidate(
            candidate_id="x",
            pair="BTC/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="short_structure",
            secondary_sensors=list(SHORT_SNIPER_PART_NAMES[1:]),
            confluence_count=4,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candle_index=10,
            raw_features={
                "sensors": list(SHORT_SNIPER_PART_NAMES),
                "short_score": 85.0,
            },
        )
        out = SetupComposer().compose([cand])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].primary_sensor, SETUP_SNIPER_SHORT)
        self.assertTrue(out[0].raw_features["setup_complete"])

    def test_setup_composer_supports_multiple_subsystems_same_side(self):
        from datetime import datetime

        from strategies.mtf_sniper.core.contracts import TradeCandidate
        from strategies.mtf_sniper.core.enums import TradeSide
        from strategies.mtf_sniper.directional.setup_recipes import (
            SetupRecipe,
            all_live_recipes,
        )

        squeeze = SetupRecipe(
            name="setup:squeeze_short",
            required_sensors=frozenset({"bb_squeeze_4h", "break_15m"}),
            side="short",
            subsystem="squeeze_short",
            priority=30,
        )
        composer = SetupComposer(
            recipes=(*all_live_recipes(include_waterfall=True), squeeze),
            drop_anomalies=False,
        )
        c_sniper = TradeCandidate(
            candidate_id="s1",
            pair="BTC/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="short_structure",
            secondary_sensors=list(SHORT_SNIPER_PART_NAMES[1:]),
            confluence_count=4,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candle_index=10,
            raw_features={"sensors": list(SHORT_SNIPER_PART_NAMES)},
        )
        c_wf = TradeCandidate(
            candidate_id="w1",
            pair="BTC/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="waterfall_momentum",
            secondary_sensors=[],
            confluence_count=1,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candle_index=11,
            raw_features={"sensors": ["waterfall_momentum"]},
        )
        c_sq = TradeCandidate(
            candidate_id="q1",
            pair="BTC/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="bb_squeeze_4h",
            secondary_sensors=["break_15m"],
            confluence_count=2,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candle_index=12,
            raw_features={"sensors": ["bb_squeeze_4h", "break_15m"]},
        )

        out = composer.compose([c_sniper, c_wf, c_sq])
        self.assertEqual(len(out), 3)
        self.assertEqual(
            [c.raw_features["subsystem"] for c in out],
            ["short_sniper", "waterfall", "squeeze_short"],
        )
        self.assertEqual(out[1].primary_sensor, "waterfall_drop")
        self.assertEqual(out[2].primary_sensor, "setup:squeeze_short")


if __name__ == "__main__":
    unittest.main()
