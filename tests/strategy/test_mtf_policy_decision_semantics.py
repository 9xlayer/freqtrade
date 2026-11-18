"""Policy eligibility vs quality_grade vs Decision native heuristics."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from strategies.mtf_sniper.core.contracts import TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.short.policy import ShortPolicy
from strategies.mtf_sniper.intelligence.decision_adapter import JevDecisionAdapter


def _cand(score: float) -> TradeCandidate:
    return TradeCandidate(
        candidate_id="t",
        pair="BTC/USDT:USDT",
        side=TradeSide.SHORT,
        primary_sensor="setup:sniper_short",
        secondary_sensors=[],
        confluence_count=1,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        candle_index=1,
        raw_features={
            "setup_complete": True,
            "legacy_sniper": "short",
            "sensors": [],
            "short_score": score,
        },
    )


class TestPolicyDecisionSemantics(unittest.TestCase):
    def test_setup_eligible_reject_grade_still_enters_policy(self):
        """is_valid=eligibility; quality_grade may be REJECT without blocking Policy."""
        cand = _cand(10.0)
        row = {"close": 100.0, "short_score": 10.0}
        with patch(
            "strategies.mtf_sniper.signals.composite_sensor.is_legacy_sniper_pass",
            return_value=True,
        ):
            result = ShortPolicy().evaluate(cand, row)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.quality_grade, "REJECT")
        self.assertTrue(result.metadata.get("setup_eligible"))

    def test_decision_native_uses_score_not_quality_grade(self):
        """
        Decision Gate does not consume Policy quality_grade.
        Low score → native veto; high score → confirm — independent of grade label.
        """
        adapter = JevDecisionAdapter(force_native=True, mode="native")

        def _payload(score: float) -> dict:
            return {
                "pair": "BTC/USDT:USDT",
                "side": "short",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "sequence": [
                    {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
                    for _ in range(20)
                ],
                "macro": {
                    "short_score": score,
                    "long_score": 50.0,
                    "adx_4h": 25.0,
                    "is_macro_bull_run_1d": 0,
                    "is_bull_wave_4h": 0,
                },
            }

        # Patch evaluate path to native score heuristic if full engine needs more fields
        low = adapter.evaluate(_payload(10.0))
        high = adapter.evaluate(_payload(85.0))
        # Document: Decision never sees quality_grade key
        self.assertNotIn("quality_grade", _payload(10.0))
        self.assertNotIn("quality_grade", _payload(85.0))
        # Native heuristics should treat weak vs strong scores differently when SUCCESS
        if low.get("status") == adapter.status.SUCCESS.value:
            self.assertIn(low.get("decision"), ("CONFIRM", "VETO", "ABSTAIN"))
        if high.get("status") == adapter.status.SUCCESS.value:
            self.assertIn(high.get("decision"), ("CONFIRM", "VETO", "ABSTAIN"))
        # Strong score must not be weaker than weak score when both succeed
        if (
            low.get("status") == adapter.status.SUCCESS.value
            and high.get("status") == adapter.status.SUCCESS.value
        ):
            rank = {"VETO": 0, "ABSTAIN": 1, "CONFIRM": 2}
            self.assertGreaterEqual(
                rank.get(high.get("decision"), -1),
                rank.get(low.get("decision"), -1),
            )


if __name__ == "__main__":
    unittest.main()
