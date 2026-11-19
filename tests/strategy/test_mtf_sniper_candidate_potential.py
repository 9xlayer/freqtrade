"""Tests for standalone CandidateScorer (pre-JEV gate)."""
from __future__ import annotations

import unittest
from datetime import datetime, UTC

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import RejectionReasonCode, TradeSide
from strategies.mtf_sniper.intelligence.hunter_escalation import (
    HunterEscalationConfig,
    HunterEscalationGateway,
)
from strategies.mtf_sniper.scoring import CandidateScorer, ScoreConfig


def _cand(
    *,
    side: TradeSide = TradeSide.SHORT,
    primary: str = "a80_plus",
    confluence: int = 2,
    idx: int = 40,
) -> TradeCandidate:
    return TradeCandidate(
        candidate_id="c1",
        pair="BTC/USDT:USDT",
        side=side,
        primary_sensor=primary,
        secondary_sensors=[],
        confluence_count=confluence,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        candle_index=idx,
        raw_features={},
    )


def _soft(score: float = 0.7, *extra: str) -> PolicyEvaluationResult:
    reasons = [RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value, *extra]
    return PolicyEvaluationResult(
        is_valid=False,
        score=score,
        quality_grade="REJECT",
        reasons=reasons,
        suggested_stop_distance_pct=0.018,
        target_rr=2.0,
        metadata={},
    )


class TestCandidateScorer(unittest.TestCase):
    def test_pass_strong_near_miss(self):
        scorer = CandidateScorer(ScoreConfig(pass_threshold=0.55))
        verdict = scorer.evaluate(
            _cand(primary="a80_plus", confluence=3),
            _soft(0.75),
            {"is_bear_wave_4h": 1, "is_macro_bear_market_1d": 1},
        )
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.reason, "pass")
        self.assertGreaterEqual(verdict.score, 0.55)

    def test_fail_below_threshold(self):
        scorer = CandidateScorer(ScoreConfig(pass_threshold=0.70))
        verdict = scorer.evaluate(
            _cand(primary="volatility_expansion", confluence=1),
            _soft(
                0.2,
                RejectionReasonCode.REJECT_RSI_OVEREXTENDED.value,
                RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value,
                RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value,
            ),
            {"is_bull_wave_4h": 1},
        )
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "fail_below_threshold")

    def test_fail_hard_reject(self):
        scorer = CandidateScorer()
        hard = PolicyEvaluationResult(
            is_valid=False,
            score=0.9,
            quality_grade="REJECT",
            reasons=[RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value],
            suggested_stop_distance_pct=0.018,
            target_rr=2.0,
        )
        verdict = scorer.evaluate(_cand(), hard, {})
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "fail_hard_reject")
        self.assertEqual(verdict.score, 0.0)


class TestScorerGatesHunter(unittest.TestCase):
    def test_score_fail_skips_hunter_call(self):
        class CountingHunter:
            def __init__(self):
                self.calls = 0

            def evaluate_candidate(self, *args, **kwargs):
                self.calls += 1
                return {
                    "action": "enter_short",
                    "status": "CONFIRMED",
                    "confidence": 0.99,
                    "setup_type": "waterfall_collapse",
                }

        hunter = CountingHunter()
        gw = HunterEscalationGateway(
            hunter,
            HunterEscalationConfig(
                enabled=True,
                score=ScoreConfig(pass_threshold=0.99),
            ),
        )
        n = 50
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
                "open": np.full(n, 100.0),
                "high": np.full(n, 101.0),
                "low": np.full(n, 99.0),
                "close": np.full(n, 100.0),
                "is_bear_wave_4h": np.zeros(n),
                "is_bull_wave_4h": np.ones(n),
            }
        )
        attempt = gw.evaluate(
            _cand(primary="volatility_expansion", confluence=1),
            _soft(0.15, RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value),
            df,
            {"is_bull_wave_4h": 1},
        )
        self.assertEqual(attempt.outcome, "low_potential")
        self.assertIsNone(attempt.promoted)
        self.assertEqual(hunter.calls, 0)


if __name__ == "__main__":
    unittest.main()
