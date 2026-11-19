"""Unit tests for Short Hunter waterfall gates."""

from __future__ import annotations

import unittest

from strategies.mtf_sniper.directional.short.hunter_gates import (
    evaluate_short_hunter_waterfall,
)


def _row(**overrides):
    base = {
        "close": 100.0,
        "ema_21": 100.5,
        "rsi": 40.0,
        "lower_wick_ratio": 0.10,
        "close_pos_ratio": 0.25,
        "swing_low_20d_1d": 90.0,
    }
    base.update(overrides)
    return base


class TestShortHunterGates(unittest.TestCase):
    def test_accepts_clean_waterfall(self):
        ok, reasons = evaluate_short_hunter_waterfall(
            _row(),
            confidence=0.90,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertTrue(ok, reasons)

    def test_rejects_low_confidence(self):
        ok, reasons = evaluate_short_hunter_waterfall(
            _row(),
            confidence=0.80,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertFalse(ok)
        self.assertIn("CONF_BELOW_THRESHOLD", reasons)

    def test_rejects_floor(self):
        ok, reasons = evaluate_short_hunter_waterfall(
            _row(swing_low_20d_1d=99.0),  # ~1% above floor
            confidence=0.90,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertFalse(ok)
        self.assertIn("FLOOR_UNSAFE", reasons)

    def test_rejects_lower_wick(self):
        ok, reasons = evaluate_short_hunter_waterfall(
            _row(lower_wick_ratio=0.40),
            confidence=0.90,
            vol_safe=True,
            sideway_safe=True,
        )
        self.assertFalse(ok)
        self.assertIn("LOWER_WICK_REJECTION", reasons)


if __name__ == "__main__":
    unittest.main()
