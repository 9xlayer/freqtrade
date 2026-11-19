"""
Short strategic policy — Directional 4b.

Deterministic phase: only complete sniper setups (4a) ACCEPT.
Anomaly soft/hard path kept behind ``allow_anomaly_entries`` (default False / AI off).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import RejectionReasonCode, TradeSide
from strategies.mtf_sniper.directional.short.hunter_gates import DEFAULT_SHORT_HUNTER_GATES
from strategies.mtf_sniper.directional.short.setups import (
    ShortSetup,
    quality_grade_from_score,
    resolve_short_setup,
)


@dataclass(frozen=True)
class WaterfallFilterConfig:
    min_body_ratio: float = 0.50
    max_atr_pct: float = 90.0
    capitulation_climax_dist_ema50_4h: float = -6.0
    capitulation_climax_dist_ema50_1d: float = -15.0
    max_dist_ema50_4h: float = -3.5
    require_below_ema50_4h: bool = False
    max_dist_ema50_1d: float = -12.0
    max_dist_ema200_1d: float = 5.0
    max_macdhist_4h: float = 35.0
    min_bb_width_4h: float = 0.035
    high_floor_dist_threshold: float = 10.0
    high_floor_min_vol_ratio: float = 4.0
    high_floor_min_bb_width_4h: float = 0.06


DEFAULT_WATERFALL_FILTER = WaterfallFilterConfig()
DEFAULT_ETH_WATERFALL_FILTER = WaterfallFilterConfig(
    require_below_ema50_4h=True,
)


def build_waterfall_config_from_params(
    base: WaterfallFilterConfig, params: Mapping[str, Any]
) -> WaterfallFilterConfig:
    prefix = "waterfall_"
    updates: dict[str, Any] = {}
    for k, v in params.items():
        attr = k.removeprefix(prefix)
        if hasattr(base, attr):
            updates[attr] = v
    if not updates:
        return base
    current = {f: getattr(base, f) for f in base.__dataclass_fields__}
    current.update(updates)
    return WaterfallFilterConfig(**current)


@dataclass
class ShortPolicyConfig:
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
        if not pair:
            return self.waterfall_default
        for k, v in self.waterfall_pair_configs.items():
            if k == pair or k.split(":")[0] == pair.split(":")[0]:
                return v
        return self.waterfall_default


class ShortPolicy:
    """Evaluate a SHORT TradeCandidate from the entry pipeline."""

    def __init__(self, config: ShortPolicyConfig | None = None) -> None:
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
                quality_grade=grade,
                reasons=["ACCEPT"],
                suggested_stop_distance_pct=cfg.base_stop_distance_pct,
                target_rr=cfg.target_rr,
                metadata={
                    "setup": setup.value,
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

        ema_50_1d = row.get("ema_50_1d")
        ema_200_1d = row.get("ema_200_1d")
        if cfg.block_macro_bull_shorts and ema_50_1d is not None and ema_200_1d is not None:
            in_macro_bull = close > float(ema_50_1d) and close > float(ema_200_1d)
            if in_macro_bull:
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        if is_waterfall:
            reasons.extend(self._check_waterfall_regime(candidate.pair, row, close))

        reasons.extend(self._check_standard_filters(row, close, is_waterfall))

        short_score = float(
            candidate.raw_features.get("short_score", row.get("short_score", 0.0)) or 0.0
        )
        base = (
            min(1.0, max(0.0, short_score / 100.0))
            if short_score > 0
            else (0.85 if is_waterfall else 0.70)
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

        entry_tag = self._resolve_entry_tag(setup, short_score)
        return PolicyEvaluationResult(
            is_valid=True,
            score=score,
            quality_grade=grade,
            reasons=["ACCEPT"],
            suggested_stop_distance_pct=cfg.base_stop_distance_pct,
            target_rr=cfg.target_rr,
            metadata={"setup": setup.value, "entry_tag": entry_tag, "short_score": short_score},
        )

    def _check_waterfall_regime(self, pair: str, row: Mapping[str, Any], close: float) -> list[str]:
        wf_cfg = self.config.get_waterfall_config(pair)
        reasons: list[str] = []
        c_range = float(row.get("high", 0.0) - row.get("low", 0.0))
        c_body = float(abs(row.get("close", 0.0) - row.get("open", 0.0)))
        body_ratio = c_body / c_range if c_range > 0 else 0.5
        is_red = close < float(row.get("open", close))
        if not (is_red and body_ratio >= wf_cfg.min_body_ratio):
            reasons.append(RejectionReasonCode.REJECT_CLOSE_POSITION_WEAK.value)

        sw_low = float(row.get("swing_low_20d_1d", 0.0) or 0.0)
        floor_dist_pct = ((close - sw_low) / sw_low * 100.0) if sw_low > 0 else 0.0

        ema50_4h = float(row.get("ema_50_4h", 0.0) or 0.0)
        dist_ema50_4h = ((close - ema50_4h) / ema50_4h * 100.0) if ema50_4h > 0 else 0.0

        ema50_1d_val = float(row.get("ema_50_1d", 0.0) or 0.0)
        dist_ema50_1d = ((close - ema50_1d_val) / ema50_1d_val * 100.0) if ema50_1d_val > 0 else 0.0

        ema200_1d_val = float(row.get("ema_200_1d", 0.0) or 0.0)
        dist_ema200_1d = (
            ((close - ema200_1d_val) / ema200_1d_val * 100.0) if ema200_1d_val > 0 else 0.0
        )

        macdhist_4h = float(row.get("macdhist_4h", 0.0) or 0.0)
        bb_width_4h = float(row.get("bb_width_4h", 0.05) or 0.05)
        vol_ratio = float(row.get("rel_vol", 1.0) or 1.0)

        is_vacuum_breakdown = floor_dist_pct <= 0.0
        if is_vacuum_breakdown:
            is_capitulation_climax = (
                dist_ema50_4h < wf_cfg.capitulation_climax_dist_ema50_4h
                or dist_ema50_1d < wf_cfg.capitulation_climax_dist_ema50_1d
            )
            if is_capitulation_climax:
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)
        else:
            is_overextended = (
                dist_ema50_4h < wf_cfg.max_dist_ema50_4h or dist_ema50_1d < wf_cfg.max_dist_ema50_1d
            )
            if wf_cfg.require_below_ema50_4h and dist_ema50_4h > 0.0:
                is_overextended = True

            is_macro_bull = dist_ema200_1d > wf_cfg.max_dist_ema200_1d
            is_bull_impulse = macdhist_4h > wf_cfg.max_macdhist_4h
            is_chop_squeeze = bb_width_4h < wf_cfg.min_bb_width_4h
            is_high_floor_trap = (
                (floor_dist_pct > wf_cfg.high_floor_dist_threshold)
                and (vol_ratio < wf_cfg.high_floor_min_vol_ratio)
                and (bb_width_4h < wf_cfg.high_floor_min_bb_width_4h)
            )

            if (
                is_overextended
                or is_macro_bull
                or is_bull_impulse
                or is_chop_squeeze
                or is_high_floor_trap
            ):
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        atr_pct = float(row.get("atr_pct", 50.0) or 50.0)
        if atr_pct >= wf_cfg.max_atr_pct:
            reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        return reasons

    def _check_standard_filters(
        self, row: Mapping[str, Any], close: float, is_waterfall: bool
    ) -> list[str]:
        reasons: list[str] = []
        cfg = self.config
        hg = DEFAULT_SHORT_HUNTER_GATES

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
            max_dist = (hg.max_dist_ema21_below * 100.0) if is_waterfall else cfg.max_dist_ema21_pct
            if dist_pct > max_dist:
                reasons.append(RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value)

        sw_low = row.get("swing_low_20d_1d")
        floor_min = hg.floor_dist_min if is_waterfall else cfg.floor_dist_min
        if sw_low is not None and float(sw_low) > 0:
            floor_dist = (close - float(sw_low)) / float(sw_low)
            floor_safe = (
                (floor_dist >= floor_min or floor_dist < 0.0)
                if is_waterfall
                else (floor_dist >= floor_min)
            )
            if not floor_safe:
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)
        elif is_waterfall:
            reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

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
