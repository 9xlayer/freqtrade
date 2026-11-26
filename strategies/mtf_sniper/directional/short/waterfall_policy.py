"""
Waterfall Collapse directional sub-system (Short Momentum / Vacuum Breakdown).

Handles both Regime A (Vacuum Breakdown at/below 20-day swing low) and
Regime B (In-Range Collapse with 4H volatility expansion).
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
)


def is_vacuum_breakdown(
    floor_dist_pct: float,
    rel_vol: float,
    *,
    vacuum_floor_dist_max: float = 0.5,
    min_surge_vol: float = 3.0,
) -> bool:
    """
    Unified definition of Waterfall Vacuum Breakdown (Single Source of Truth).

    True if price has broken the 20D floor (<= 0.0%) or sits within the near-floor
    buffer (<= vacuum_floor_dist_max) with strong volume surge confirmation.
    """
    if floor_dist_pct <= 0.0:
        return True
    return floor_dist_pct <= vacuum_floor_dist_max and rel_vol >= min_surge_vol


@dataclass(frozen=True)
class WaterfallFilterConfig:
    min_body_ratio: float = 0.50
    max_atr_pct: float = 90.0
    ignore_atr_on_vacuum: bool = True
    capitulation_climax_dist_ema50_4h: float = -10.0
    capitulation_climax_dist_ema50_1d: float = -20.0
    max_dist_ema50_4h: float = -3.5
    require_below_ema50_4h: bool = False
    max_dist_ema50_1d: float = -12.0
    max_dist_ema200_1d: float = 5.0
    max_macdhist_4h: float = 35.0
    min_bb_width_4h: float = 0.035
    high_floor_dist_threshold: float = 10.0
    high_floor_min_vol_ratio: float = 4.0
    high_floor_min_bb_width_4h: float = 0.06
    ema21_base_dist_pct: float = 1.5
    ema21_expanded_dist_pct: float = 1.6
    ema21_expansion_min_body: float = 0.70
    ema21_expansion_max_wick: float = 0.15
    min_rsi: float = 24.0
    max_lower_wick_ratio: float = 0.285
    max_close_pos_ratio: float = 0.35
    floor_dist_min: float = 0.035
    vacuum_floor_dist_max: float = 0.5
    vacuum_min_vol_ratio: float = 3.0


DEFAULT_WATERFALL_FILTER = WaterfallFilterConfig()
DEFAULT_ETH_WATERFALL_FILTER = WaterfallFilterConfig(require_below_ema50_4h=True)


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


def resolve_pair_waterfall_config(
    pair: str | None,
    default_cfg: WaterfallFilterConfig,
    pair_configs: Mapping[str, WaterfallFilterConfig],
) -> WaterfallFilterConfig:
    if not pair:
        return default_cfg
    for k, v in pair_configs.items():
        if k == pair or k.split(":")[0] == pair.split(":")[0]:
            return v
    return default_cfg


def check_short_floor_gate(
    row: Mapping[str, Any],
    close: float,
    *,
    is_waterfall: bool,
    floor_min: float,
    wf_cfg: WaterfallFilterConfig | None = None,
) -> list[str]:
    sw_low = row.get("swing_low_20d_1d")
    if sw_low is None or float(sw_low) <= 0:
        return [RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value] if is_waterfall else []

    floor_dist = (close - float(sw_low)) / float(sw_low)
    floor_dist_pct = floor_dist * 100.0
    vol_ratio = read_finite_float(row, "rel_vol", 1.0)
    if is_waterfall and wf_cfg is not None:
        is_vacuum = is_vacuum_breakdown(
            floor_dist_pct,
            vol_ratio,
            vacuum_floor_dist_max=wf_cfg.vacuum_floor_dist_max,
            min_surge_vol=wf_cfg.vacuum_min_vol_ratio,
        )
        floor_safe = floor_dist >= floor_min or is_vacuum
    elif is_waterfall:
        floor_safe = floor_dist >= floor_min or floor_dist < 0.0
    else:
        floor_safe = floor_dist >= floor_min
    return [] if floor_safe else [RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value]


@dataclass
class WaterfallShortConfig:
    """Configuration for the standalone Waterfall Collapse short sub-system."""

    enabled: bool = True
    block_macro_bull_shorts: bool = False
    filter_wick_rejection: bool = True
    confluence_bonus: float = 0.10
    base_stop_distance_pct: float = 0.018
    target_rr: float = 2.0
    waterfall_default: WaterfallFilterConfig = field(default_factory=WaterfallFilterConfig)
    waterfall_pair_configs: dict[str, WaterfallFilterConfig] = field(
        default_factory=lambda: {
            "ETH/USDT:USDT": DEFAULT_ETH_WATERFALL_FILTER,
            "ETH/USDT": DEFAULT_ETH_WATERFALL_FILTER,
        }
    )

    def get_waterfall_config(self, pair: str | None = None) -> WaterfallFilterConfig:
        return resolve_pair_waterfall_config(
            pair, self.waterfall_default, self.waterfall_pair_configs
        )


class WaterfallShortPolicy:
    """Dedicated directional policy for the Waterfall Collapse short sub-system."""

    _WATERFALL_NUM_KEYS = (
        "open",
        "high",
        "low",
        "swing_low_20d_1d",
        "ema_50_4h",
        "ema_50_1d",
        "ema_200_1d",
        "macdhist_4h",
        "bb_width_4h",
        "rel_vol",
        "atr_pct",
    )
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

    def __init__(self, config: WaterfallShortConfig | None = None) -> None:
        self.config = config or WaterfallShortConfig()

    @classmethod
    def from_short_config(cls, short_cfg: Any) -> WaterfallShortPolicy:
        cfg = WaterfallShortConfig(
            enabled=bool(getattr(short_cfg, "allow_anomaly_entries", True)),
            block_macro_bull_shorts=bool(getattr(short_cfg, "block_macro_bull_shorts", False)),
            filter_wick_rejection=bool(getattr(short_cfg, "filter_wick_rejection", True)),
            confluence_bonus=float(getattr(short_cfg, "confluence_bonus", 0.10)),
            base_stop_distance_pct=float(getattr(short_cfg, "base_stop_distance_pct", 0.018)),
            target_rr=float(getattr(short_cfg, "target_rr", 2.0)),
            waterfall_default=getattr(short_cfg, "waterfall_default", DEFAULT_WATERFALL_FILTER),
            waterfall_pair_configs=dict(
                getattr(
                    short_cfg,
                    "waterfall_pair_configs",
                    {
                        "ETH/USDT:USDT": DEFAULT_ETH_WATERFALL_FILTER,
                        "ETH/USDT": DEFAULT_ETH_WATERFALL_FILTER,
                    },
                )
            ),
        )
        return cls(cfg)

    @staticmethod
    def _rel_dist_pct(close: float, ref: float) -> float:
        return ((close - ref) / ref * 100.0) if ref > 0.0 else 0.0

    def _check_macro_and_regime(self, pair: str, row: Mapping[str, Any], close: float) -> list[str]:
        if (
            not math.isfinite(close)
            or close <= 0.0
            or has_invalid_numeric(row, self._WATERFALL_NUM_KEYS)
        ):
            return [RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value]

        reasons: list[str] = []
        ema_50_1d = row.get("ema_50_1d")
        ema_200_1d = row.get("ema_200_1d")
        if (
            self.config.block_macro_bull_shorts
            and ema_50_1d is not None
            and ema_200_1d is not None
            and close > float(ema_50_1d)
            and close > float(ema_200_1d)
        ):
            reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        wf_cfg = self.config.get_waterfall_config(pair)
        open_p = read_finite_float(row, "open", close)
        high_p = read_finite_float(row, "high", close)
        low_p = read_finite_float(row, "low", close)
        c_range = high_p - low_p
        body_ratio = abs(close - open_p) / c_range if c_range > 0 else 0.5
        if not (close < open_p and body_ratio >= wf_cfg.min_body_ratio):
            reasons.append(RejectionReasonCode.REJECT_CLOSE_POSITION_WEAK.value)

        floor_dist_pct = self._rel_dist_pct(close, read_finite_float(row, "swing_low_20d_1d", 0.0))
        dist_ema50_4h = self._rel_dist_pct(close, read_finite_float(row, "ema_50_4h", 0.0))
        dist_ema50_1d = self._rel_dist_pct(close, read_finite_float(row, "ema_50_1d", 0.0))
        dist_ema200_1d = self._rel_dist_pct(close, read_finite_float(row, "ema_200_1d", 0.0))
        macdhist_4h = read_finite_float(row, "macdhist_4h", 0.0)
        bb_width_4h = read_finite_float(row, "bb_width_4h", 0.05)
        vol_ratio = read_finite_float(row, "rel_vol", 1.0)
        is_vacuum = is_vacuum_breakdown(
            floor_dist_pct,
            vol_ratio,
            vacuum_floor_dist_max=wf_cfg.vacuum_floor_dist_max,
            min_surge_vol=wf_cfg.vacuum_min_vol_ratio,
        )

        if wf_cfg.require_below_ema50_4h and dist_ema50_4h > 0.0:
            reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        if is_vacuum:
            if (
                dist_ema50_4h < wf_cfg.capitulation_climax_dist_ema50_4h
                or dist_ema50_1d < wf_cfg.capitulation_climax_dist_ema50_1d
            ):
                reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)
        elif (
            dist_ema50_4h < wf_cfg.max_dist_ema50_4h
            or dist_ema50_1d < wf_cfg.max_dist_ema50_1d
            or dist_ema200_1d > wf_cfg.max_dist_ema200_1d
            or macdhist_4h > wf_cfg.max_macdhist_4h
            or bb_width_4h < wf_cfg.min_bb_width_4h
            or (
                floor_dist_pct > wf_cfg.high_floor_dist_threshold
                and vol_ratio < wf_cfg.high_floor_min_vol_ratio
                and bb_width_4h < wf_cfg.high_floor_min_bb_width_4h
            )
        ):
            reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        atr_pct = read_finite_float(row, "atr_pct", 50.0)
        if not (is_vacuum and wf_cfg.ignore_atr_on_vacuum) and atr_pct > wf_cfg.max_atr_pct:
            reasons.append(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)
        return reasons

    def _check_candle_and_floor(
        self, pair: str, row: Mapping[str, Any], close: float
    ) -> list[str]:
        if (
            not math.isfinite(close)
            or close <= 0.0
            or has_invalid_numeric(row, self._STANDARD_NUM_KEYS)
        ):
            return [RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value]

        reasons: list[str] = []
        wf_cfg = self.config.get_waterfall_config(pair)
        lower_wick = read_finite_float(row, "lower_wick_ratio", 0.0)
        close_pos = read_finite_float(row, "close_pos_ratio", 0.5)

        if self.config.filter_wick_rejection:
            if lower_wick > wf_cfg.max_lower_wick_ratio:
                reasons.append(RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value)
            if close_pos > wf_cfg.max_close_pos_ratio:
                reasons.append(RejectionReasonCode.REJECT_CLOSE_POSITION_WEAK.value)

        if read_finite_float(row, "rsi", 50.0) < wf_cfg.min_rsi:
            reasons.append(RejectionReasonCode.REJECT_RSI_OVEREXTENDED.value)

        ema_21 = row.get("ema_21")
        if ema_21 is not None and float(ema_21) > 0:
            dist_pct = (float(ema_21) - close) / close * 100.0
            open_p = read_finite_float(row, "open", close)
            high_p = read_finite_float(row, "high", close)
            low_p = read_finite_float(row, "low", close)
            c_range = high_p - low_p
            body_ratio = abs(close - open_p) / c_range if c_range > 0 else 0.5
            is_solid = (
                body_ratio >= wf_cfg.ema21_expansion_min_body
                and lower_wick <= wf_cfg.ema21_expansion_max_wick
            )
            max_dist = wf_cfg.ema21_expanded_dist_pct if is_solid else wf_cfg.ema21_base_dist_pct
            if dist_pct > max_dist:
                reasons.append(RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value)

        reasons.extend(
            check_short_floor_gate(
                row,
                close,
                is_waterfall=True,
                floor_min=wf_cfg.floor_dist_min,
                wf_cfg=wf_cfg,
            )
        )
        return reasons

    def evaluate(
        self,
        candidate: TradeCandidate,
        row: Mapping[str, Any],
    ) -> PolicyEvaluationResult:
        cfg = self.config
        if candidate.side != TradeSide.SHORT:
            return PolicyEvaluationResult(
                is_valid=False,
                score=0.0,
                quality_grade="REJECT",
                reasons=[RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value],
                suggested_stop_distance_pct=cfg.base_stop_distance_pct,
                target_rr=cfg.target_rr,
                metadata={"error": "wrong_side", "subsystem": "waterfall"},
            )

        if not cfg.enabled:
            return PolicyEvaluationResult(
                is_valid=False,
                score=0.0,
                quality_grade="REJECT",
                reasons=[RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value],
                suggested_stop_distance_pct=cfg.base_stop_distance_pct,
                target_rr=cfg.target_rr,
                metadata={
                    "setup": ShortSetup.WATERFALL_COLLAPSE.value,
                    "subsystem": "waterfall",
                    "setup_complete": False,
                    "deterministic_only": True,
                },
            )

        close = float(row.get("close", 0.0) or 0.0)
        reasons = self._check_macro_and_regime(candidate.pair, row, close)
        reasons.extend(self._check_candle_and_floor(candidate.pair, row, close))

        short_score = float(
            candidate.raw_features.get("short_score", row.get("short_score", 0.0)) or 0.0
        )
        if short_score > 0:
            raw_base = min(1.0, max(0.0, short_score / 100.0))
            base = max(0.70, raw_base)
        else:
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
                metadata={
                    "setup": ShortSetup.WATERFALL_COLLAPSE.value,
                    "subsystem": "waterfall",
                    "short_score": short_score,
                },
            )

        return PolicyEvaluationResult(
            is_valid=True,
            score=score,
            quality_grade=grade,
            reasons=["ACCEPT"],
            suggested_stop_distance_pct=cfg.base_stop_distance_pct,
            target_rr=cfg.target_rr,
            metadata={
                "setup": ShortSetup.WATERFALL_COLLAPSE.value,
                "subsystem": "waterfall",
                "entry_tag": ShortSetup.WATERFALL_COLLAPSE.value,
                "short_score": short_score,
            },
        )
