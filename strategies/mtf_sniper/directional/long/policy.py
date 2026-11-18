"""
Long strategic policy — Directional 4b.

Deterministic phase: only complete sniper setups (4a) ACCEPT.
Anomaly path behind ``allow_anomaly_entries`` (default False / AI off).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import RejectionReasonCode, TradeSide
from strategies.mtf_sniper.directional.long.hunter_gates import DEFAULT_LONG_HUNTER_GATES
from strategies.mtf_sniper.directional.long.setups import (
    LongSetup,
    quality_grade_from_score,
    resolve_long_setup,
)


@dataclass
class LongPolicyConfig:
    """Defaults mirror StrategyMTFSniper hyperopt defaults (BTC baseline)."""

    max_rejection_wick_ratio: float = 0.25
    min_close_pos_ratio: float = 0.70
    max_rsi_entry: float = 67.6
    max_dist_ema21_pct: float = 3.3
    overextended_dist_max: float = 0.59
    filter_wick_rejection: bool = True
    filter_pdh_ceiling: bool = True
    require_macro_bull: bool = True
    require_score_a80: bool = True
    score_threshold: float = 80.0
    confluence_bonus: float = 0.10
    base_stop_distance_pct: float = 0.018
    target_rr: float = 2.0
    enable_hunter_long: bool = False
    allow_anomaly_entries: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class LongPolicy:
    """Evaluate a LONG TradeCandidate from the entry pipeline."""

    def __init__(self, config: Optional[LongPolicyConfig] = None) -> None:
        self.config = config or LongPolicyConfig()

    def evaluate(
        self,
        candidate: TradeCandidate,
        row: Mapping[str, Any],
    ) -> PolicyEvaluationResult:
        if candidate.side != TradeSide.LONG:
            return self._reject(
                [RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value],
                metadata={"error": "wrong_side"},
            )

        reasons: list[str] = []
        cfg = self.config
        hg = DEFAULT_LONG_HUNTER_GATES
        setup = resolve_long_setup(candidate.primary_sensor, candidate.raw_features)
        is_hunterish = setup in (LongSetup.BREAKOUT_EXPANSION, LongSetup.SWING_BREAKOUT)
        from strategies.mtf_sniper.signals.composite_sensor import is_legacy_sniper_pass

        is_setup_pass = is_legacy_sniper_pass(
            candidate.primary_sensor, candidate.raw_features, "long"
        )

        ema_50_1d = row.get("ema_50_1d")
        ema_200_1d = row.get("ema_200_1d")
        close = float(row.get("close", 0.0) or 0.0)

        if is_setup_pass:
            long_score = float(
                candidate.raw_features.get("long_score", row.get("long_score", 0.0)) or 0.0
            )
            base = min(1.0, max(0.0, long_score / 100.0)) if long_score > 0 else 0.80
            score = min(1.0, base + max(0, candidate.confluence_count - 1) * cfg.confluence_bonus)
            grade = quality_grade_from_score(score)
            return PolicyEvaluationResult(
                is_valid=True,
                score=score,
                quality_grade=grade if grade != "REJECT" else "A",
                reasons=["ACCEPT"],
                suggested_stop_distance_pct=cfg.base_stop_distance_pct,
                target_rr=cfg.target_rr,
                metadata={
                    "setup": LongSetup.A80_PLUS.value,
                    "entry_tag": LongSetup.A80_PLUS.value,
                    "long_score": long_score,
                    "setup_complete": True,
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

        if cfg.require_macro_bull and ema_50_1d is not None and ema_200_1d is not None:
            in_bear = close < float(ema_200_1d) or close < float(ema_50_1d)
            macro_bear_flag = float(row.get("is_macro_bear_market_1d", 0) or 0) == 1
            if in_bear or macro_bear_flag:
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        if cfg.filter_wick_rejection:
            upper_wick = float(row.get("upper_wick_ratio", 0.0) or 0.0)
            close_pos = float(row.get("close_pos_ratio", 0.5) or 0.5)
            wick_limit = hg.max_upper_wick_ratio if is_hunterish else cfg.max_rejection_wick_ratio
            close_floor = hg.min_close_pos_ratio if is_hunterish else cfg.min_close_pos_ratio
            if upper_wick > wick_limit:
                reasons.append(RejectionReasonCode.REJECT_UPPER_WICK_REJECTION.value)
            if close_pos < close_floor:
                reasons.append(RejectionReasonCode.REJECT_CLOSE_POSITION_WEAK.value)

        rsi = float(row.get("rsi", 50.0) or 50.0)
        rsi_cap = hg.max_rsi if is_hunterish else cfg.max_rsi_entry
        if rsi > rsi_cap:
            reasons.append(RejectionReasonCode.REJECT_RSI_OVEREXTENDED.value)

        ema_21 = row.get("ema_21")
        if ema_21 is not None and float(ema_21) > 0:
            dist_pct = (close - float(ema_21)) / float(ema_21) * 100.0
            max_dist = (hg.max_dist_ema21 * 100.0) if is_hunterish else cfg.max_dist_ema21_pct
            if dist_pct > max_dist:
                reasons.append(RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value)

        overext_cap = hg.max_dist_ema200_1d if is_hunterish else cfg.overextended_dist_max
        if "dist_from_ema_200_1d" in row and row.get("dist_from_ema_200_1d") is not None:
            if float(row["dist_from_ema_200_1d"]) > overext_cap:
                reasons.append(RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value)

        if cfg.filter_pdh_ceiling and "pdh_1d" in row and row.get("pdh_1d") is not None:
            pdh = float(row["pdh_1d"])
            if close < pdh and close >= pdh * 0.997:
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        long_score = float(
            candidate.raw_features.get("long_score", row.get("long_score", 0.0)) or 0.0
        )
        if setup == LongSetup.A80_PLUS and cfg.require_score_a80:
            if long_score < cfg.score_threshold:
                reasons.append(RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value)
        if is_hunterish and not cfg.enable_hunter_long:
            reasons.append(RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value)

        base = min(1.0, max(0.0, long_score / 100.0)) if long_score > 0 else 0.70
        if is_hunterish:
            base = 0.85
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
                metadata={"setup": setup.value, "long_score": long_score},
            )

        return PolicyEvaluationResult(
            is_valid=True,
            score=score,
            quality_grade=grade,
            reasons=["ACCEPT"],
            suggested_stop_distance_pct=cfg.base_stop_distance_pct,
            target_rr=cfg.target_rr,
            metadata={"setup": setup.value, "entry_tag": setup.value, "long_score": long_score},
        )

    @staticmethod
    def _reject(reasons: list[str], metadata: Optional[dict] = None) -> PolicyEvaluationResult:
        return PolicyEvaluationResult(
            is_valid=False,
            score=0.0,
            quality_grade="REJECT",
            reasons=reasons,
            suggested_stop_distance_pct=0.018,
            target_rr=2.0,
            metadata=metadata or {},
        )
