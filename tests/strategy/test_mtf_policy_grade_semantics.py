"""Phase 3 — quality grade must not be remapped to fake A/B on setup accept."""
from __future__ import annotations

import unittest
from datetime import datetime, UTC
from unittest.mock import patch

from strategies.mtf_sniper.core.contracts import TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.long.policy import LongPolicy
from strategies.mtf_sniper.directional.short.policy import ShortPolicy


def _cand(side: TradeSide, score_key: str, score: float) -> TradeCandidate:
    return TradeCandidate(
        candidate_id="t",
        pair="BTC/USDT:USDT",
        side=side,
        primary_sensor="setup:sniper_short" if side == TradeSide.SHORT else "setup:sniper_long",
        secondary_sensors=[],
        confluence_count=1,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        candle_index=1,
        raw_features={
            "setup_complete": True,
            "legacy_sniper": side.value,
            "sensors": [],
            score_key: score,
        },
    )


class TestPolicyGradeSemantics(unittest.TestCase):
    def test_short_setup_pass_keeps_reject_grade(self):
        cand = _cand(TradeSide.SHORT, "short_score", 10.0)
        row = {"close": 100.0, "short_score": 10.0}
        with patch(
            "strategies.mtf_sniper.signals.composite_sensor.is_legacy_sniper_pass",
            return_value=True,
        ):
            result = ShortPolicy().evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.quality_grade, "REJECT")
        self.assertTrue(result.metadata.get("setup_eligible"))

    def test_long_setup_pass_keeps_reject_grade(self):
        cand = _cand(TradeSide.LONG, "long_score", 5.0)
        row = {"close": 100.0, "long_score": 5.0}
        with patch(
            "strategies.mtf_sniper.signals.composite_sensor.is_legacy_sniper_pass",
            return_value=True,
        ):
            result = LongPolicy().evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.quality_grade, "REJECT")
        self.assertTrue(result.metadata.get("setup_eligible"))


if __name__ == "__main__":
    unittest.main()
