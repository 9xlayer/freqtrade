"""Phase 8 — MTF indicator look-ahead / warm-up / numeric safety checks."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from strategies.mtf_sniper.market import indicators as ind


def _ohlcv(n: int = 500, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, size=n))
    high = close + rng.uniform(0.1, 1.5, size=n)
    low = close - rng.uniform(0.1, 1.5, size=n)
    open_ = close + rng.normal(0, 0.3, size=n)
    vol = rng.uniform(10, 100, size=n)
    dates = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        }
    )


class TestLookaheadIndicators(unittest.TestCase):
    def test_pdh_pdl_use_prior_candle_only(self):
        df = ind.populate_indicators_1d(_ohlcv(300))
        # pdh/pdl are previous day's high/low — shift(1) relative to same TF frame
        self.assertTrue(np.isnan(df["pdh"].iloc[0]) or pd.isna(df["pdh"].iloc[0]))
        for i in range(1, 50):
            self.assertEqual(df["pdh"].iloc[i], df["high"].iloc[i - 1])
            self.assertEqual(df["pdl"].iloc[i], df["low"].iloc[i - 1])

    def test_swing_high_low_exclude_current_bar(self):
        df = ind.populate_indicators_15m(_ohlcv(400), short_lookback_support=17, long_lookback_resistance=15)
        # swing_high at i uses high.shift(1).rolling — must not include high[i]
        i = 50
        lookback = 15
        window = df["high"].iloc[i - lookback : i].to_numpy()  # ends at i-1
        self.assertEqual(df["swing_high"].iloc[i], np.nanmax(window))
        self.assertNotEqual(df["swing_high"].iloc[i], df["high"].iloc[i])

    def test_prefix_recompute_matches_full(self):
        """Truncating future candles must not change past indicator values."""
        full = _ohlcv(500)
        cut = 400
        df_full = ind.populate_indicators_15m(
            full.copy(), short_lookback_support=17, long_lookback_resistance=15
        )
        df_prefix = ind.populate_indicators_15m(
            full.iloc[:cut].copy(), short_lookback_support=17, long_lookback_resistance=15
        )
        cols = ["ema_21", "rsi", "macdhist", "swing_high", "swing_low", "atr"]
        # Skip warm-up where EMA/RSI still settling identically on both
        start = 250
        for col in cols:
            a = df_full[col].iloc[start:cut].to_numpy(dtype=float)
            b = df_prefix[col].iloc[start:].to_numpy(dtype=float)
            np.testing.assert_allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True)

    def test_no_inf_after_warmup(self):
        df = ind.populate_indicators_15m(
            _ohlcv(500), short_lookback_support=17, long_lookback_resistance=15
        )
        warmup = 250
        check_cols = [
            c
            for c in df.columns
            if c not in ("date",) and pd.api.types.is_numeric_dtype(df[c])
        ]
        for col in check_cols:
            chunk = df[col].iloc[warmup:]
            self.assertFalse(np.isinf(chunk.to_numpy(dtype=float)).any(), msg=col)

    def test_1h_4h_1d_prefix_stable(self):
        for fn in (ind.populate_indicators_1h, ind.populate_indicators_4h, ind.populate_indicators_1d):
            full = _ohlcv(400)
            cut = 320
            a = fn(full.copy())
            b = fn(full.iloc[:cut].copy())
            col = "macdhist"
            start = 200
            np.testing.assert_allclose(
                a[col].iloc[start:cut].to_numpy(dtype=float),
                b[col].iloc[start:].to_numpy(dtype=float),
                rtol=1e-9,
                atol=1e-9,
                equal_nan=True,
            )


if __name__ == "__main__":
    unittest.main()
