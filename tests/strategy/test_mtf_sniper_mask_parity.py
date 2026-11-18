"""Live SensorSuite entry wiring + residual sniper mask builder unit checks."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from strategies.mtf_sniper.execution.entry_pipeline import EntryPipeline, apply_entry_pipeline


def _param(value):
    return SimpleNamespace(value=value)


def _make_strategy(**overrides):
    base = dict(
        high_vol_pct=_param(90.0),
        block_high_vol=_param(True),
        min_adx_4h=_param(18.0),
        short_max_dist_ema21=_param(1.0),
        short_candle_body_ratio_min=_param(0.62),
        short_loc_tolerance_1h=_param(0.991),
        short_min_1h_rsi_exhaustion=_param(55.1),
        short_min_rsi_entry=_param(27.8),
        short_cooldown_candles=_param(96),
        long_max_dist_ema21=_param(3.3),
        long_candle_body_ratio_min=_param(0.63),
        long_loc_tolerance_1h=_param(1.008),
        long_max_1h_rsi_exhaustion=_param(62.4),
        long_lookback_resistance=_param(22),
        long_max_rsi_entry=_param(67.6),
        long_cooldown_candles=_param(18),
        long_overextended_dist_max=_param(0.59),
        long_block_1d_macd=_param(True),
        block_4h_counter_wave=_param(True),
        block_1d_counter_wave=_param(True),
        filter_wick_rejection=_param(True),
        max_rejection_wick_ratio=_param(0.25),
        min_close_pos_ratio=_param(0.70),
        max_close_pos_ratio=_param(0.30),
        filter_pdh_ceiling=_param(True),
        enable_hunter_long=False,
        block_macro_bull_shorts=False,
        _get_pair_params=lambda pair: {},
    )
    base.update(overrides)
    strat = SimpleNamespace(**base)
    from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper

    strat.apply_cooldown = StrategyMTFSniper.apply_cooldown  # staticmethod
    return strat

def _full_df(n: int = 150) -> pd.DataFrame:
    idx = pd.RangeIndex(n)
    df = pd.DataFrame(
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
            "atr_pct": np.full(n, 1.0),
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
            "is_macro_bull_run_1d": np.full(n, 1),
            "is_macro_bear_market_1d": np.full(n, 0),
            "swing_low_20d_1d": np.full(n, 70.0),
            "is_deep_capitulation_1d": np.full(n, 0),
            "rel_vol": np.full(n, 1.0),
            "volume_mean_20": np.full(n, 100.0),
        },
        index=idx,
    )
    return df


class TestLiveSensorEntry(unittest.TestCase):
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

    def test_entry_pipeline_live_factory(self):
        p1 = EntryPipeline.live()
        self.assertGreaterEqual(len(p1.sensor_suite.sensors), 1)


if __name__ == "__main__":
    unittest.main()
