"""
Short strategic policy — Directional 4b.

Deterministic phase: only complete sniper setups (4a) ACCEPT.
Anomaly soft/hard path kept behind ``allow_anomaly_entries`` (default False / AI off).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import RejectionReasonCode, TradeSide
from strategies.mtf_sniper.directional.short.hunter_gates import DEFAULT_SHORT_HUNTER_GATES
from strategies.mtf_sniper.directional.short.setups import (
    ShortSetup,
    quality_grade_from_score,
    resolve_short_setup,
)


@dataclass
class ShortPolicyConfig:
    """Defaults mirror StrategyMTFSniper hyperopt defaults (BTC baseline)."""

    max_rejection_wick_ratio: float = 0.25
    max_close_pos_ratio: float = 0.30
    min_rsi_entry: float = 27.8
    max_dist_ema21_pct: float = 1.0
    filter_wick_rejection: bool = True
    block_macro_bull_shorts: bool = False
    floor_dist_min: float = DEFAULT_SHORT_HUNTER_GATES.floor_dist_min
    confluence_bonus: float = 0.10
    base_stop_distance_pct: float = 0.018
    target_rr: float = 2.0
    # AI/Hunter branch off until deterministic sniper flow is stable
    allow_anomaly_entries: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class ShortPolicy:
    """Evaluate a SHORT TradeCandidate from the entry pipeline."""

    def __init__(self, config: Optional[ShortPolicyConfig] = None) -> None:
        self.config = config or ShortPolicyConfig()

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

        reasons: list[str] = []
        cfg = self.config
        hg = DEFAULT_SHORT_HUNTER_GATES
        setup = resolve_short_setup(candidate.primary_sensor, candidate.raw_features)
        is_waterfall = setup == ShortSetup.WATERFALL_COLLAPSE
        from strategies.mtf_sniper.signals.composite_sensor import is_legacy_sniper_pass

        is_setup_pass = is_legacy_sniper_pass(
            candidate.primary_sensor, candidate.raw_features, "short"
        )

        close = float(row.get("close", 0.0) or 0.0)

        if is_setup_pass:
            short_score = float(
                candidate.raw_features.get("short_score", row.get("short_score", 0.0)) or 0.0
            )
            base = min(1.0, max(0.0, short_score / 100.0)) if short_score > 0 else 0.70
            score = min(1.0, base + max(0, candidate.confluence_count - 1) * cfg.confluence_bonus)
            grade = quality_grade_from_score(score)
            return PolicyEvaluationResult(
                is_valid=True,
                score=score,
                quality_grade=grade if grade != "REJECT" else "B",
                reasons=["ACCEPT"],
                suggested_stop_distance_pct=cfg.base_stop_distance_pct,
                target_rr=cfg.target_rr,
                metadata={
                    "setup": setup.value,
                    "entry_tag": setup.value,
                    "short_score": short_score,
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

        # --- Anomaly path (re-enable with allow_anomaly_entries=True + Hunter) ---
        ema_50_1d = row.get("ema_50_1d")
        ema_200_1d = row.get("ema_200_1d")
        if cfg.block_macro_bull_shorts and ema_50_1d is not None and ema_200_1d is not None:
            in_macro_bull = close > float(ema_50_1d) and close > float(ema_200_1d)
            if in_macro_bull:
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        if is_waterfall:
            is_bear_macro = (
                float(row.get("is_macro_bear_market_1d", 0) or 0) == 1
                or (ema_50_1d is not None and close < float(ema_50_1d))
                or float(row.get("is_bear_wave_4h", 0) or 0) == 1
                or float(row.get("macdhist_4h", 0) or 0) < 0
            )
            is_bull_macro = (
                float(row.get("is_macro_bull_run_1d", 0) or 0) == 1
                or (ema_50_1d is not None and close > float(ema_50_1d))
                or float(row.get("is_bull_wave_4h", 0) or 0) == 1
            )
            if not is_bear_macro or is_bull_macro:
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        if cfg.filter_wick_rejection:
            lower_wick = float(row.get("lower_wick_ratio", 0.0) or 0.0)
            close_pos = float(row.get("close_pos_ratio", 0.5) or 0.5)
            wick_limit = hg.max_lower_wick_ratio if is_waterfall else cfg.max_rejection_wick_ratio
            close_cap = hg.max_close_pos_ratio if is_waterfall else cfg.max_close_pos_ratio
            if lower_wick > wick_limit:
                reasons.append(RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value)
            if close_pos > close_cap:
                reasons.append(RejectionReasonCode.REJECT_CLOSE_POSITION_WEAK.value)

        rsi = float(row.get("rsi", 50.0) or 50.0)
        rsi_floor = hg.min_rsi if is_waterfall else cfg.min_rsi_entry
        if rsi < rsi_floor:
            reasons.append(RejectionReasonCode.REJECT_RSI_OVEREXTENDED.value)

        ema_21 = row.get("ema_21")
        if ema_21 is not None and float(ema_21) > 0:
            dist_pct = (float(ema_21) - close) / close * 100.0
            max_dist = (
                (hg.max_dist_ema21_below * 100.0) if is_waterfall else cfg.max_dist_ema21_pct
            )
            if dist_pct > max_dist:
                reasons.append(RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value)

        sw_low = row.get("swing_low_20d_1d")
        floor_min = hg.floor_dist_min if is_waterfall else cfg.floor_dist_min
        if sw_low is not None and float(sw_low) > 0:
            floor_dist = (close - float(sw_low)) / float(sw_low)
            if is_waterfall:
                floor_safe = floor_dist >= floor_min or floor_dist < 0.0
            else:
                floor_safe = floor_dist >= floor_min
            if not floor_safe:
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        short_score = float(
            candidate.raw_features.get("short_score", row.get("short_score", 0.0)) or 0.0
        )
        base = min(1.0, max(0.0, short_score / 100.0)) if short_score > 0 else (
            0.85 if is_waterfall else 0.70
        )
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

        entry_tag = setup.value
        if setup in (ShortSetup.A80_PLUS, ShortSetup.B65_PLUS, ShortSetup.C_SUB65):
            entry_tag = setup.value
        elif short_score >= 80:
            entry_tag = ShortSetup.A80_PLUS.value
        elif short_score >= 65:
            entry_tag = ShortSetup.B65_PLUS.value
        elif short_score > 0:
            entry_tag = ShortSetup.C_SUB65.value

        return PolicyEvaluationResult(
            is_valid=True,
            score=score,
            quality_grade=grade,
            reasons=["ACCEPT"],
            suggested_stop_distance_pct=cfg.base_stop_distance_pct,
            target_rr=cfg.target_rr,
            metadata={"setup": setup.value, "entry_tag": entry_tag, "short_score": short_score},
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
