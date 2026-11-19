"""
JEV Hunter escalation gateway.

  soft-reject → CandidateScorer → pass → JevHunterEngine
                         ↘ fail → drop
  → post-AI gates (legacy conf / vol / chop / macro) → promote or reject
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pandas import DataFrame

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.common_gates import (
    DEFAULT_MARKET_SAFETY,
    MarketSafetyConfig,
)
from strategies.mtf_sniper.directional.long.hunter_gates import (
    DEFAULT_LONG_HUNTER_GATES,
    LongHunterGateConfig,
)
from strategies.mtf_sniper.directional.short.hunter_gates import (
    DEFAULT_SHORT_HUNTER_GATES,
    ShortHunterGateConfig,
)
from strategies.mtf_sniper.intelligence.post_hunter_gates import accept_hunter_verdict
from strategies.mtf_sniper.scoring import CandidateScorer, ScoreConfig, ScoreVerdict


logger = logging.getLogger(__name__)


class HunterEvaluator(Protocol):
    def evaluate_candidate(
        self,
        df_32: DataFrame,
        pair: str,
        ts_str: str,
        event_type: str,
        macro_info: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass
class HunterEscalationConfig:
    enabled: bool = True
    lookback_bars: int = 32
    allow_long_promotion: bool = False
    # Pre-JEV cheap gate
    score: ScoreConfig = field(default_factory=ScoreConfig)
    # Post-AI acceptance (legacy active parity)
    market: MarketSafetyConfig = field(default_factory=lambda: DEFAULT_MARKET_SAFETY)
    short_gates: ShortHunterGateConfig = field(default_factory=lambda: DEFAULT_SHORT_HUNTER_GATES)
    long_gates: LongHunterGateConfig = field(default_factory=lambda: DEFAULT_LONG_HUNTER_GATES)


@dataclass
class EscalationAttempt:
    outcome: str
    # promoted | low_potential | hunter_rejected | post_gate_rejected |
    # hard_or_ineligible | disabled | error | score_fail
    promoted: PolicyEvaluationResult | None = None
    score_verdict: ScoreVerdict | None = None
    gate_reasons: list[str] | None = None


def sensor_to_event_trigger(candidate: TradeCandidate) -> str:
    sensor = (candidate.primary_sensor or "").lower()
    side = candidate.side
    if "waterfall" in sensor:
        return "MEGA_SURGE_SHORT" if side == TradeSide.SHORT else "BREAKOUT_24H_LONG"
    if "swing" in sensor or "breakout" in sensor:
        return "BREAKOUT_24H_SHORT" if side == TradeSide.SHORT else "BREAKOUT_24H_LONG"
    if "volume" in sensor or "surge" in sensor or "volatility" in sensor:
        return "MEGA_SURGE_SHORT" if side == TradeSide.SHORT else "MEGA_SURGE_LONG"
    return "BREAKOUT_24H_SHORT" if side == TradeSide.SHORT else "BREAKOUT_24H_LONG"


def build_macro_info(row: Mapping[str, Any]) -> dict[str, Any]:
    is_bull = float(row.get("is_bull_wave_4h", 0) or 0) == 1
    is_bear = float(row.get("is_bear_wave_4h", 0) or 0) == 1
    if is_bull:
        trend_4h = "BULL"
    elif is_bear:
        trend_4h = "BEAR"
    else:
        trend_4h = "NEUTRAL"

    close = float(row.get("close", 0) or 0)
    ema_50_1h = row.get("ema_50_1h")
    ema50_dist = 0.0
    if ema_50_1h is not None and float(ema_50_1h) > 0 and close > 0:
        ema50_dist = (close - float(ema_50_1h)) / float(ema_50_1h) * 100.0

    return {
        "trend_4h": trend_4h,
        "ema50_1h_dist_pct": ema50_dist,
        "atr_percentile": float(row.get("atr_pct", 50.0) or 50.0),
        "range_24h_pct": 0.0,
        "is_macro_bull_run_1d": float(row.get("is_macro_bull_run_1d", 0) or 0),
        "is_macro_bear_market_1d": float(row.get("is_macro_bear_market_1d", 0) or 0),
    }


class HunterEscalationGateway:
    """Score gate → JEV Hunter → legacy post-AI gates → optional promote."""

    def __init__(
        self,
        evaluator: HunterEvaluator,
        config: HunterEscalationConfig | None = None,
        scorer: CandidateScorer | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.config = config or HunterEscalationConfig()
        self.scorer = scorer or CandidateScorer(self.config.score)

    def evaluate(
        self,
        candidate: TradeCandidate,
        policy_result: PolicyEvaluationResult,
        dataframe: DataFrame,
        row: Mapping[str, Any],
    ) -> EscalationAttempt:
        if not self.config.enabled:
            return EscalationAttempt(outcome="disabled")

        verdict = self.scorer.evaluate(candidate, policy_result, row)
        if not verdict.passed:
            if verdict.reason == "fail_hard_reject":
                outcome = "hard_or_ineligible"
            elif verdict.reason == "fail_below_threshold":
                outcome = "low_potential"
            else:
                outcome = "score_fail"
            return EscalationAttempt(outcome=outcome, score_verdict=verdict)

        return self._run_hunter_and_gates(
            candidate,
            dataframe,
            row,
            score_verdict=verdict,
            policy_result=policy_result,
            radar_path=False,
        )

    def evaluate_radar(
        self,
        candidate: TradeCandidate,
        dataframe: DataFrame,
        row: Mapping[str, Any],
        *,
        stop_distance_pct: float = 0.018,
        target_rr: float = 2.0,
    ) -> EscalationAttempt:
        """
        Parallel MomentumRadar path: skip scorer, run JEV + legacy post-AI gates.

        Used when anomaly sensors wake Hunter independently of sniper AND-mask.
        """
        if not self.config.enabled:
            return EscalationAttempt(outcome="disabled")

        synthetic = PolicyEvaluationResult(
            is_valid=False,
            score=0.0,
            quality_grade="RADAR",
            reasons=["RADAR_WAKEUP"],
            suggested_stop_distance_pct=stop_distance_pct,
            target_rr=target_rr,
            metadata={"radar": True},
        )
        return self._run_hunter_and_gates(
            candidate,
            dataframe,
            row,
            score_verdict=None,
            policy_result=synthetic,
            radar_path=True,
        )

    def _run_hunter_and_gates(
        self,
        candidate: TradeCandidate,
        dataframe: DataFrame,
        row: Mapping[str, Any],
        *,
        score_verdict: ScoreVerdict | None,
        policy_result: PolicyEvaluationResult,
        radar_path: bool,
    ) -> EscalationAttempt:
        if candidate.side == TradeSide.LONG and not self.config.allow_long_promotion:
            return EscalationAttempt(outcome="hard_or_ineligible", score_verdict=score_verdict)

        idx = candidate.candle_index
        if idx < 0 or idx >= len(dataframe):
            return EscalationAttempt(outcome="error", score_verdict=score_verdict)

        lookback = max(16, self.config.lookback_bars)
        start = max(0, idx + 1 - lookback)
        df_window = dataframe.iloc[start : idx + 1]
        if len(df_window) < 16:
            return EscalationAttempt(outcome="error", score_verdict=score_verdict)

        ts_str = ""
        if "date" in dataframe.columns:
            ts_str = str(dataframe.iloc[idx]["date"])
        event_trigger = sensor_to_event_trigger(candidate)
        macro = build_macro_info(row)

        try:
            hunter_verdict = self.evaluator.evaluate_candidate(
                df_window,
                candidate.pair,
                ts_str,
                event_trigger,
                macro,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("[HunterEscalation] evaluate failed: %s", exc)
            return EscalationAttempt(outcome="error", score_verdict=score_verdict)

        action = str(hunter_verdict.get("action", "no_trade")).lower()
        status = str(hunter_verdict.get("status", "")).upper()
        confidence = float(hunter_verdict.get("confidence", 0.0) or 0.0)
        setup = str(hunter_verdict.get("setup_type", "unknown") or "unknown")

        # Legacy only accepted CONFIRMED.
        if status != "CONFIRMED":
            return EscalationAttempt(outcome="hunter_rejected", score_verdict=score_verdict)

        want_long = action == "enter_long"
        want_short = action == "enter_short"
        if candidate.side == TradeSide.LONG and not want_long:
            return EscalationAttempt(outcome="hunter_rejected", score_verdict=score_verdict)
        if candidate.side == TradeSide.SHORT and not want_short:
            return EscalationAttempt(outcome="hunter_rejected", score_verdict=score_verdict)

        accepted, gate_reasons = accept_hunter_verdict(
            side=candidate.side,
            setup=setup,
            confidence=confidence,
            row=row,
            market=self.config.market,
            short_gates=self.config.short_gates,
            long_gates=self.config.long_gates,
        )
        if not accepted:
            return EscalationAttempt(
                outcome="post_gate_rejected",
                score_verdict=score_verdict,
                gate_reasons=gate_reasons,
            )

        tag = f"jev_hunter_{setup}"
        meta: dict[str, Any] = {
            "setup": setup,
            "entry_tag": tag,
            "hunter_action": action,
            "hunter_confidence": confidence,
            "hunter_status": status,
            "policy_soft_reasons": list(policy_result.reasons),
            "escalated": True,
            "radar_path": radar_path,
            "event_trigger": event_trigger,
            "post_gate_reasons": gate_reasons,
        }
        if score_verdict is not None:
            meta["score"] = score_verdict.score
            meta["score_reason"] = score_verdict.reason
            meta["score_components"] = score_verdict.components

        promoted = PolicyEvaluationResult(
            is_valid=True,
            score=min(1.0, max(0.0, confidence)),
            quality_grade="HUNTER",
            reasons=["HUNTER_ESCALATION_PROMOTE"],
            suggested_stop_distance_pct=float(policy_result.suggested_stop_distance_pct or 0.018),
            target_rr=float(policy_result.target_rr or 2.0),
            metadata=meta,
        )
        return EscalationAttempt(
            outcome="promoted",
            promoted=promoted,
            score_verdict=score_verdict,
            gate_reasons=gate_reasons,
        )


def default_hunter_gateway(
    *,
    enabled: bool,
    allow_long_promotion: bool,
    pass_threshold: float = 0.55,
) -> HunterEscalationGateway | None:
    if not enabled:
        return None
    from strategies.mtf_sniper.intelligence.hunter_adapter import JevHunterAdapter

    return HunterEscalationGateway(
        evaluator=JevHunterAdapter(),
        config=HunterEscalationConfig(
            enabled=True,
            allow_long_promotion=allow_long_promotion,
            score=ScoreConfig(pass_threshold=pass_threshold),
        ),
    )
