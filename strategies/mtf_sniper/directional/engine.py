"""
DirectionalTradingEngine — LIVE entry evaluator.

Routes TradeCandidates from SensorSuite/CandidateBuilder to Long/Short policy.
"""
from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence, Tuple

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.long.policy import LongPolicy, LongPolicyConfig
from strategies.mtf_sniper.directional.short.policy import ShortPolicy, ShortPolicyConfig


class DirectionalTradingEngine:
    """
    Strategic brain: route TradeCandidate by side -> policy evaluation.

    Does NOT authorize risk or call JEV; returns PolicyEvaluationResult only.
    """

    def __init__(
        self,
        long_policy: Optional[LongPolicy] = None,
        short_policy: Optional[ShortPolicy] = None,
        long_config: Optional[LongPolicyConfig] = None,
        short_config: Optional[ShortPolicyConfig] = None,
    ) -> None:
        self.long_policy = long_policy or LongPolicy(long_config)
        self.short_policy = short_policy or ShortPolicy(short_config)

    def evaluate(
        self,
        candidate: TradeCandidate,
        row: Mapping[str, Any],
    ) -> PolicyEvaluationResult:
        if candidate.side == TradeSide.LONG:
            return self.long_policy.evaluate(candidate, row)
        return self.short_policy.evaluate(candidate, row)

    def evaluate_many(
        self,
        candidates: Sequence[TradeCandidate],
        rows_by_index: Mapping[int, Mapping[str, Any]],
    ) -> List[Tuple[TradeCandidate, PolicyEvaluationResult]]:
        """
        Evaluate a batch of candidates.

        `rows_by_index` maps candle_index -> feature row (dict-like).
        Candidates whose index is missing are rejected as invalid.
        """
        results: List[Tuple[TradeCandidate, PolicyEvaluationResult]] = []
        for candidate in candidates:
            row = rows_by_index.get(candidate.candle_index)
            if row is None:
                results.append(
                    (
                        candidate,
                        PolicyEvaluationResult(
                            is_valid=False,
                            score=0.0,
                            quality_grade="REJECT",
                            reasons=["INSUFFICIENT_HISTORY"],
                            suggested_stop_distance_pct=0.018,
                            target_rr=2.0,
                            metadata={},
                        ),
                    )
                )
                continue
            results.append((candidate, self.evaluate(candidate, row)))
        return results
