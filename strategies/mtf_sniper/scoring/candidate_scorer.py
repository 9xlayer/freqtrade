"""
CandidateScorer — standalone pre-JEV gate.

Input:  TradeCandidate + PolicyEvaluationResult (+ optional market row)
Output: ScoreVerdict(passed, score, ...)

  passed=True  → escalate to JEV Hunter
  passed=False → drop immediately (no AI cost)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.policy_escalation import (
    SOFT_REJECT_REASONS,
    is_soft_policy_reject,
)

_SOFT_REASON_PENALTY = 0.08

_PATTERN_SENSORS = frozenset(
    {
        "a80_plus",
        "swing_breakout",
        "sniper_long_mask",
        "sniper_short_mask",
    }
)


@dataclass(frozen=True)
class ScoreVerdict:
    """Result of scoring one candidate for JEV eligibility."""

    passed: bool
    score: float
    threshold: float
    reason: str
    components: dict = field(default_factory=dict)


@dataclass
class ScoreConfig:
    """Tunable weights / pass threshold for CandidateScorer."""

    pass_threshold: float = 0.55
    w_policy_score: float = 0.35
    w_confluence: float = 0.20
    w_sensor_quality: float = 0.15
    w_near_miss: float = 0.20
    w_context: float = 0.10

    @classmethod
    def with_threshold(cls, threshold: float, **kwargs: Any) -> "ScoreConfig":
        return cls(pass_threshold=float(threshold), **kwargs)


class CandidateScorer:
    """
    Deterministic scorer: one candidate in → pass/fail out.

    Does not call JEV, does not write enter_*, does not touch Risk.
    """

    def __init__(self, config: Optional[ScoreConfig] = None) -> None:
        self.config = config or ScoreConfig()

    def evaluate(
        self,
        candidate: TradeCandidate,
        policy_result: PolicyEvaluationResult,
        row: Optional[Mapping[str, Any]] = None,
    ) -> ScoreVerdict:
        """
        Score a policy-rejected (or soft) candidate.

        Returns ``passed=True`` only when soft-reject AND score >= threshold.
        """
        row = row or {}
        threshold = float(self.config.pass_threshold)

        if policy_result.is_valid:
            return ScoreVerdict(
                passed=False,
                score=0.0,
                threshold=threshold,
                reason="fail_already_passed_policy",
                components={},
            )

        if not is_soft_policy_reject(policy_result):
            return ScoreVerdict(
                passed=False,
                score=0.0,
                threshold=threshold,
                reason="fail_hard_reject",
                components={"policy_reasons": list(policy_result.reasons)},
            )

        cfg = self.config
        policy_s = min(1.0, max(0.0, float(policy_result.score or 0.0)))
        confluence_s = min(1.0, max(0.0, (int(candidate.confluence_count) - 1) / 3.0))
        sensor_s = self._sensor_quality(candidate)
        near_miss_s = self._near_miss_quality(policy_result)
        context_s = self._context_quality(candidate.side, row)

        weight_sum = (
            cfg.w_policy_score
            + cfg.w_confluence
            + cfg.w_sensor_quality
            + cfg.w_near_miss
            + cfg.w_context
        )
        raw = (
            cfg.w_policy_score * policy_s
            + cfg.w_confluence * confluence_s
            + cfg.w_sensor_quality * sensor_s
            + cfg.w_near_miss * near_miss_s
            + cfg.w_context * context_s
        )
        score = min(1.0, max(0.0, raw / weight_sum if weight_sum else 0.0))
        components = {
            "policy_score": policy_s,
            "confluence": confluence_s,
            "sensor_quality": sensor_s,
            "near_miss": near_miss_s,
            "context": context_s,
            "soft_reason_count": len(
                [r for r in policy_result.reasons if r in SOFT_REJECT_REASONS]
            ),
            "primary_sensor": candidate.primary_sensor,
        }

        if score >= threshold:
            return ScoreVerdict(
                passed=True,
                score=score,
                threshold=threshold,
                reason="pass",
                components=components,
            )
        return ScoreVerdict(
            passed=False,
            score=score,
            threshold=threshold,
            reason="fail_below_threshold",
            components=components,
        )

    @staticmethod
    def _sensor_quality(candidate: TradeCandidate) -> float:
        name = (candidate.primary_sensor or "").lower()
        if name in _PATTERN_SENSORS or "a80" in name:
            return 1.0
        if "swing" in name or "breakout" in name:
            return 0.85
        if "waterfall" in name:
            return 0.75
        if "volume" in name or "surge" in name:
            return 0.55
        if "volatility" in name:
            return 0.45
        return 0.40

    @staticmethod
    def _near_miss_quality(policy_result: PolicyEvaluationResult) -> float:
        soft_count = sum(1 for r in policy_result.reasons if r in SOFT_REJECT_REASONS)
        if soft_count <= 0:
            return 0.0
        return max(0.0, 1.0 - (soft_count - 1) * _SOFT_REASON_PENALTY * 4)

    @staticmethod
    def _context_quality(side: TradeSide, row: Mapping[str, Any]) -> float:
        score = 0.5
        if side == TradeSide.SHORT:
            if float(row.get("is_bear_wave_4h", 0) or 0) == 1:
                score += 0.25
            if float(row.get("is_macro_bear_market_1d", 0) or 0) == 1:
                score += 0.25
            if float(row.get("is_bull_wave_4h", 0) or 0) == 1:
                score -= 0.35
        else:
            if float(row.get("is_bull_wave_4h", 0) or 0) == 1:
                score += 0.25
            if float(row.get("is_macro_bull_run_1d", 0) or 0) == 1:
                score += 0.25
            if float(row.get("is_bear_wave_4h", 0) or 0) == 1:
                score -= 0.35
        return min(1.0, max(0.0, score))
