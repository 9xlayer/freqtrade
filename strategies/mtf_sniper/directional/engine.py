"""
DirectionalTradingEngine — LIVE entry evaluator (step 4 facade).

4a SetupComposer annotates sub-system recipes; 4b dispatches via subsystem_policies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.long.policy import LongPolicy, LongPolicyConfig
from strategies.mtf_sniper.directional.setup_composer import SetupComposer
from strategies.mtf_sniper.directional.setup_recipes import DirectionalPolicyProtocol
from strategies.mtf_sniper.directional.short.policy import ShortPolicy, ShortPolicyConfig
from strategies.mtf_sniper.directional.short.waterfall_policy import (
    WaterfallShortConfig,
    WaterfallShortPolicy,
)


def _resolve_subsystem_id(candidate: TradeCandidate) -> str:
    raw = str((candidate.raw_features or {}).get("subsystem") or "").strip()
    if raw:
        return raw
    sensors = set((candidate.raw_features or {}).get("sensors") or [])
    if candidate.primary_sensor in ("waterfall_drop", "waterfall_momentum") or bool(
        sensors & {"waterfall_drop", "waterfall_momentum"}
    ):
        return "waterfall"
    return "long_sniper" if candidate.side == TradeSide.LONG else "short_sniper"


class DirectionalTradingEngine:
    """
    Strategic brain: compose -> registry-driven sub-system policy evaluation.

    Does NOT authorize risk or call JEV; returns PolicyEvaluationResult only.
    """

    def __init__(
        self,
        long_policy: DirectionalPolicyProtocol | None = None,
        short_policy: DirectionalPolicyProtocol | None = None,
        long_config: LongPolicyConfig | None = None,
        short_config: ShortPolicyConfig | None = None,
        setup_composer: SetupComposer | None = None,
        waterfall_policy: DirectionalPolicyProtocol | None = None,
        waterfall_config: WaterfallShortConfig | None = None,
        subsystem_policies: Mapping[str, DirectionalPolicyProtocol] | None = None,
    ) -> None:
        self.long_policy: DirectionalPolicyProtocol = long_policy or LongPolicy(long_config)
        self.short_policy: DirectionalPolicyProtocol = short_policy or ShortPolicy(short_config)
        if waterfall_policy is not None:
            self.waterfall_policy: DirectionalPolicyProtocol = waterfall_policy
        elif waterfall_config is not None:
            self.waterfall_policy = WaterfallShortPolicy(waterfall_config)
        else:
            short_cfg = getattr(self.short_policy, "config", None)
            self.waterfall_policy = (
                WaterfallShortPolicy.from_short_config(short_cfg)
                if short_cfg is not None
                else WaterfallShortPolicy()
            )
        self.subsystem_policies: dict[str, DirectionalPolicyProtocol] = {
            "long_sniper": self.long_policy,
            "short_sniper": self.short_policy,
            "waterfall": self.waterfall_policy,
        }
        if subsystem_policies:
            self.subsystem_policies.update(subsystem_policies)
        self.setup_composer = setup_composer or SetupComposer()

    def evaluate(
        self,
        candidate: TradeCandidate,
        row: Mapping[str, Any],
    ) -> PolicyEvaluationResult:
        if candidate.side == TradeSide.SHORT and type(self.short_policy) is not ShortPolicy:
            return self.short_policy.evaluate(candidate, row)

        subsystem_id = _resolve_subsystem_id(candidate)
        policy = self.subsystem_policies.get(subsystem_id)
        if policy is not None:
            return policy.evaluate(candidate, row)
        if candidate.side == TradeSide.LONG:
            return self.long_policy.evaluate(candidate, row)
        return self.short_policy.evaluate(candidate, row)

    def evaluate_many(
        self,
        candidates: Sequence[TradeCandidate],
        rows_by_index: Mapping[int, Mapping[str, Any]],
    ) -> list[tuple[TradeCandidate, PolicyEvaluationResult]]:
        """Evaluate candidates using their corresponding bar row features."""
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
