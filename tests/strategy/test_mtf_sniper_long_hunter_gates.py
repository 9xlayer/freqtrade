"""Unit tests for P4 Long Hunter breakout gates."""

from __future__ import annotations

import unittest

from strategies.mtf_sniper.directional.long.hunter_gates import (
    LongHunterGateConfig,
    evaluate_long_hunter_breakout,
)


def _row(**overrides):
    base = {
        "open": 100.0,
        "close": 101.5,
        "high": 101.8,
        "low": 99.8,
        "ema_21": 101.0,
        "rsi": 58.0,
        "upper_wick_ratio": 0.10,
        "close_pos_ratio": 0.80,
        "macdhist_4h": 0.2,
        "is_bull_wave_4h": 0,
        "dist_from_ema_200_1d": 0.25,
        "long_score": 60.0,
    }
    base.update(overrides)
    return base


class TestLongHunterGates(unittest.TestCase):
    def test_accepts_clean_breakout(self):
        ok, reasons = evaluate_long_hunter_breakout(
            _row(),
            confidence=0.92,
            pdh_safe=True,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertTrue(ok, reasons)
        self.assertEqual(reasons, [])

    def test_rejects_low_confidence(self):
        ok, reasons = evaluate_long_hunter_breakout(
            _row(),
            confidence=0.85,
            pdh_safe=True,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertFalse(ok)
        self.assertIn("CONF_BELOW_THRESHOLD", reasons)

    def test_rejects_ema21_chase(self):
        ok, reasons = evaluate_long_hunter_breakout(
            _row(close=103.0, high=103.2, ema_21=100.0),
            confidence=0.95,
            pdh_safe=True,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertFalse(ok)
        self.assertIn("EMA21_CHASE", reasons)

    def test_rejects_weak_htf(self):
        ok, reasons = evaluate_long_hunter_breakout(
            _row(macdhist_4h=-0.1, is_bull_wave_4h=0),
            confidence=0.95,
            pdh_safe=True,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertFalse(ok)
        self.assertIn("HTF_MOMENTUM_WEAK", reasons)

    def test_rejects_upper_wick(self):
        ok, reasons = evaluate_long_hunter_breakout(
            _row(upper_wick_ratio=0.35),
            confidence=0.95,
            pdh_safe=True,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertFalse(ok)
        self.assertIn("UPPER_WICK_REJECTION", reasons)

    def test_config_override_loosens_rsi(self):
        cfg = LongHunterGateConfig(max_rsi=80.0, min_confidence=0.85)
        ok, reasons = evaluate_long_hunter_breakout(
            _row(rsi=70.0),
            confidence=0.88,
            pdh_safe=True,
            vol_safe=True,
            sideway_safe=True,
            config=cfg,
        )
        self.assertTrue(ok, reasons)


if __name__ == "__main__":
    unittest.main()
