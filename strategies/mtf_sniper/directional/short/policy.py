"""
Short Sniper strategic policy — Directional 4b.

Deterministic phase: complete sniper setups ACCEPT; Waterfall Collapse candidates
delegate to the dedicated WaterfallShortPolicy sub-system.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import RejectionReasonCode, TradeSide
from strategies.mtf_sniper.directional.common_gates import (
    has_invalid_numeric,
    read_finite_float,
)
from strategies.mtf_sniper.directional.short.setups import (
    ShortSetup,
    quality_grade_from_score,
    resolve_short_setup,
)
from strategies.mtf_sniper.directional.short.waterfall_policy import (
    DEFAULT_ETH_WATERFALL_FILTER,
    DEFAULT_WATERFALL_FILTER,
    WaterfallFilterConfig,
    WaterfallShortPolicy,
    build_waterfall_config_from_params,
    check_short_floor_gate,
    is_vacuum_breakdown,
    resolve_pair_waterfall_config,
)


__all__ = [
    "DEFAULT_ETH_WATERFALL_FILTER",
    "DEFAULT_WATERFALL_FILTER",
    "ShortPolicy",
    "ShortPolicyConfig",
    "WaterfallFilterConfig",
    "WaterfallShortPolicy",
    "build_waterfall_config_from_params",
    "is_vacuum_breakdown",
]


@dataclass
class ShortPolicyConfig:
    max_rejection_wick_ratio: float = 0.25
    max_close_pos_ratio: float = 0.30
    min_rsi_entry: float = 27.8
    max_dist_ema21_pct: float = 1.0
    filter_wick_rejection: bool = True
    block_macro_bull_shorts: bool = False
    floor_dist_min: float = 0.035
    confluence_bonus: float = 0.10
    base_stop_distance_pct: float = 0.018
    target_rr: float = 2.0
    allow_anomaly_entries: bool = False
    waterfall_default: WaterfallFilterConfig = field(default_factory=WaterfallFilterConfig)
    waterfall_pair_configs: dict[str, WaterfallFilterConfig] = field(
        default_factory=lambda: {
            "ETH/USDT:USDT": DEFAULT_ETH_WATERFALL_FILTER,
            "ETH/USDT": DEFAULT_ETH_WATERFALL_FILTER,
        }
    )
    extra: dict[str, Any] = field(default_factory=dict)

    def get_waterfall_config(self, pair: str | None = None) -> WaterfallFilterConfig:
        return resolve_pair_waterfall_config(
            pair, self.waterfall_default, self.waterfall_pair_configs
        )


class ShortPolicy:
    """Evaluate a SHORT Sniper TradeCandidate (delegates Waterfall to WaterfallShortPolicy)."""

    _STANDARD_NUM_KEYS = (
        "open",
        "high",
        "low",
        "lower_wick_ratio",
        "close_pos_ratio",
        "rsi",
        "ema_21",
        "swing_low_20d_1d",
        "rel_vol",
    )

    def __init__(self, config: ShortPolicyConfig | None = None) -> None:
        self.config = config or ShortPolicyConfig()
        self._waterfall_policy = WaterfallShortPolicy.from_short_config(self.config)

    def evaluate(
        self,
        candidate: TradeCandidate,
        row: Mapping[str, Any],
    ) -> PolicyEvaluationResult:
        if candidate.side != TradeSide.SHORT:
            return self._reject(
                [RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value],
                metadata={"error": "wrong_side"},
            )

        cfg = self.config
        setup = resolve_short_setup(candidate.primary_sensor, candidate.raw_features)
        if setup == ShortSetup.WATERFALL_COLLAPSE:
            if not cfg.allow_anomaly_entries:
                return self._reject(
                    [RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value],
                    metadata={
                        "setup": setup.value,
                        "setup_complete": False,
                        "deterministic_only": True,
                    },
                )
            return self._waterfall_policy.evaluate(candidate, row)

        from strategies.mtf_sniper.signals.composite_sensor import is_legacy_sniper_pass

        if is_legacy_sniper_pass(candidate.primary_sensor, candidate.raw_features, "short"):
            short_score = float(
                candidate.raw_features.get("short_score", row.get("short_score", 0.0)) or 0.0
            )
            base = min(1.0, max(0.0, short_score / 100.0)) if short_score > 0 else 0.70
            score = min(1.0, base + max(0, candidate.confluence_count - 1) * cfg.confluence_bonus)
            return PolicyEvaluationResult(
                is_valid=True,
                score=score,
                quality_grade=quality_grade_from_score(score),
                reasons=["ACCEPT"],
                suggested_stop_distance_pct=cfg.base_stop_distance_pct,
                target_rr=cfg.target_rr,
                metadata={
                    "setup": setup.value,
                    "subsystem": "short_sniper",
                    "entry_tag": setup.value,
                    "short_score": short_score,
                    "setup_complete": True,
                    "setup_eligible": True,
                    "mask_pass": True,
                },
            )

        if not cfg.allow_anomaly_entries:
            return self._reject(
                [RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value],
                metadata={
                    "setup": setup.value,
                    "setup_complete": False,
                    "deterministic_only": True,
                },
            )

        close = float(row.get("close", 0.0) or 0.0)
        reasons = self._check_anomaly_filters(row, close)
        short_score = float(
            candidate.raw_features.get("short_score", row.get("short_score", 0.0)) or 0.0
        )
        base = min(1.0, max(0.0, short_score / 100.0)) if short_score > 0 else 0.70
        score = min(1.0, base + max(0, candidate.confluence_count - 1) * cfg.confluence_bonus)
        grade = quality_grade_from_score(score)

        if reasons or grade == "REJECT":
            if not reasons:
                reasons.append(RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value)
            return PolicyEvaluationResult(
                is_valid=False,
                score=score,
                quality_grade="REJECT",
                reasons=reasons,
                suggested_stop_distance_pct=cfg.base_stop_distance_pct,
                target_rr=cfg.target_rr,
                metadata={"setup": setup.value, "short_score": short_score},
            )

        return PolicyEvaluationResult(
            is_valid=True,
            score=score,
            quality_grade=grade,
            reasons=["ACCEPT"],
            suggested_stop_distance_pct=cfg.base_stop_distance_pct,
            target_rr=cfg.target_rr,
            metadata={
                "setup": setup.value,
                "entry_tag": self._resolve_entry_tag(setup, short_score),
                "short_score": short_score,
            },
        )

    def _check_anomaly_filters(self, row: Mapping[str, Any], close: float) -> list[str]:
        if (
            not math.isfinite(close)
            or close <= 0.0
            or has_invalid_numeric(row, self._STANDARD_NUM_KEYS)
        ):
            return [RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value]

        reasons: list[str] = []
        cfg = self.config
        ema_50_1d = row.get("ema_50_1d")
        ema_200_1d = row.get("ema_200_1d")
        if (
            cfg.block_macro_bull_shorts
            and ema_50_1d is not None
            and ema_200_1d is not None
            and close > float(ema_50_1d)
            and close > float(ema_200_1d)
        ):
            reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        if cfg.filter_wick_rejection:
            if read_finite_float(row, "lower_wick_ratio", 0.0) > cfg.max_rejection_wick_ratio:
                reasons.append(RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value)
            if read_finite_float(row, "close_pos_ratio", 0.5) > cfg.max_close_pos_ratio:
                reasons.append(RejectionReasonCode.REJECT_CLOSE_POSITION_WEAK.value)

        if read_finite_float(row, "rsi", 50.0) < cfg.min_rsi_entry:
            reasons.append(RejectionReasonCode.REJECT_RSI_OVEREXTENDED.value)

        ema_21 = row.get("ema_21")
        if (
            ema_21 is not None
            and float(ema_21) > 0
            and ((float(ema_21) - close) / close * 100.0) > cfg.max_dist_ema21_pct
        ):
            reasons.append(RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value)

        reasons.extend(
            check_short_floor_gate(row, close, is_waterfall=False, floor_min=cfg.floor_dist_min)
        )
        return reasons

    @staticmethod
    def _resolve_entry_tag(setup: ShortSetup, short_score: float) -> str:
        if setup in (
            ShortSetup.A80_PLUS,
            ShortSetup.B65_PLUS,
            ShortSetup.C_SUB65,
            ShortSetup.WATERFALL_COLLAPSE,
        ):
            return setup.value
        if short_score >= 80:
            return ShortSetup.A80_PLUS.value
        if short_score >= 65:
            return ShortSetup.B65_PLUS.value
        if short_score > 0:
            return ShortSetup.C_SUB65.value
        return setup.value

    @staticmethod
    def _reject(reasons: list[str], metadata: dict | None = None) -> PolicyEvaluationResult:
        return PolicyEvaluationResult(
            is_valid=False,
            score=0.0,
            quality_grade="REJECT",
            reasons=reasons,
            suggested_stop_distance_pct=0.018,
            target_rr=2.0,
            metadata=metadata or {},
        )
