"""
DirectionalTradingEngine — LIVE entry evaluator (step 4 facade).

4a SetupComposer annotates recipes; 4b Long/Short Policy decides ACCEPT.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.long.policy import LongPolicy, LongPolicyConfig
from strategies.mtf_sniper.directional.setup_composer import SetupComposer
from strategies.mtf_sniper.directional.short.policy import ShortPolicy, ShortPolicyConfig


class DirectionalTradingEngine:
    """
    Strategic brain: optional compose → route by side → policy evaluation.

    Does NOT authorize risk or call JEV; returns PolicyEvaluationResult only.
    """

    def __init__(
        self,
        long_policy: LongPolicy | None = None,
        short_policy: ShortPolicy | None = None,
        long_config: LongPolicyConfig | None = None,
        short_config: ShortPolicyConfig | None = None,
        setup_composer: SetupComposer | None = None,
    ) -> None:
        self.long_policy = long_policy or LongPolicy(long_config)
        self.short_policy = short_policy or ShortPolicy(short_config)
        self.setup_composer = setup_composer or SetupComposer()

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
    ) -> list[tuple[TradeCandidate, PolicyEvaluationResult]]:
        """
        Evaluate a batch of candidates.

        `rows_by_index` maps candle_index -> feature row (dict-like).
        Candidates whose index is missing are rejected as invalid.
        """
        results: list[tuple[TradeCandidate, PolicyEvaluationResult]] = []
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
