"""
JEV Hunter escalation gateway.

  soft-reject → CandidateScorer (scoring/) → pass → JevHunterEngine
                                    ↘ fail → drop (no AI)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Protocol

from pandas import DataFrame

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.scoring import CandidateScorer, ScoreConfig, ScoreVerdict

logger = logging.getLogger(__name__)


class HunterEvaluator(Protocol):
    def evaluate_candidate(
        self,
        df_32: DataFrame,
        pair: str,
        ts_str: str,
        event_type: str,
        macro_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        ...


@dataclass
class HunterEscalationConfig:
    enabled: bool = True
    lookback_bars: int = 32
    min_confidence: float = 0.60
    allow_long_promotion: bool = False
    # Standalone scoring system config (pre-JEV gate)
    score: ScoreConfig = field(default_factory=ScoreConfig)


@dataclass
class EscalationAttempt:
    outcome: str
    # promoted | low_potential | hunter_rejected | hard_or_ineligible | disabled | error
    promoted: Optional[PolicyEvaluationResult] = None
    score_verdict: Optional[ScoreVerdict] = None


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


def build_macro_info(row: Mapping[str, Any]) -> Dict[str, Any]:
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
    """
    Orchestrates: score gate → optional JEV Hunter promote.

    Scoring is owned by ``strategies.mtf_sniper.scoring.CandidateScorer``.
    """

    def __init__(
        self,
        evaluator: HunterEvaluator,
        config: Optional[HunterEscalationConfig] = None,
        scorer: Optional[CandidateScorer] = None,
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

        # 1) Standalone scorer: fail → drop (never call JEV)
        verdict = self.scorer.evaluate(candidate, policy_result, row)
        if not verdict.passed:
            outcome = (
                "hard_or_ineligible"
                if verdict.reason == "fail_hard_reject"
                else "score_fail"
            )
            # keep old audit label for below-threshold
            if verdict.reason == "fail_below_threshold":
                outcome = "low_potential"
            return EscalationAttempt(outcome=outcome, score_verdict=verdict)

        if candidate.side == TradeSide.LONG and not self.config.allow_long_promotion:
            return EscalationAttempt(
                outcome="hard_or_ineligible", score_verdict=verdict
            )

        # 2) Pass → call JEV Hunter
        idx = int(candidate.candle_index)
        if idx < 0 or idx >= len(dataframe):
            return EscalationAttempt(outcome="error", score_verdict=verdict)

        lookback = max(16, int(self.config.lookback_bars))
        start = max(0, idx + 1 - lookback)
        df_window = dataframe.iloc[start : idx + 1]
        if len(df_window) < 16:
            return EscalationAttempt(outcome="error", score_verdict=verdict)

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
            return EscalationAttempt(outcome="error", score_verdict=verdict)

        action = str(hunter_verdict.get("action", "no_trade")).lower()
        status = str(hunter_verdict.get("status", "")).upper()
        confidence = float(hunter_verdict.get("confidence", 0.0) or 0.0)
        setup = str(hunter_verdict.get("setup_type", "unknown") or "unknown")

        if status not in ("CONFIRMED", "SUCCESS"):
            return EscalationAttempt(outcome="hunter_rejected", score_verdict=verdict)

        want_long = action == "enter_long"
        want_short = action == "enter_short"
        if candidate.side == TradeSide.LONG and not want_long:
            return EscalationAttempt(outcome="hunter_rejected", score_verdict=verdict)
        if candidate.side == TradeSide.SHORT and not want_short:
            return EscalationAttempt(outcome="hunter_rejected", score_verdict=verdict)
        if confidence < float(self.config.min_confidence):
            return EscalationAttempt(outcome="hunter_rejected", score_verdict=verdict)

        tag = f"jev_hunter_{setup}"
        promoted = PolicyEvaluationResult(
            is_valid=True,
            score=min(1.0, max(0.0, confidence)),
            quality_grade="HUNTER",
            reasons=["HUNTER_ESCALATION_PROMOTE"],
            suggested_stop_distance_pct=float(
                policy_result.suggested_stop_distance_pct or 0.018
            ),
            target_rr=float(policy_result.target_rr or 2.0),
            metadata={
                "setup": setup,
                "entry_tag": tag,
                "hunter_action": action,
                "hunter_confidence": confidence,
                "hunter_status": status,
                "policy_soft_reasons": list(policy_result.reasons),
                "escalated": True,
                "event_trigger": event_trigger,
                "score": verdict.score,
                "score_reason": verdict.reason,
                "score_components": verdict.components,
            },
        )
        return EscalationAttempt(
            outcome="promoted",
            promoted=promoted,
            score_verdict=verdict,
        )


def default_hunter_gateway(
    *,
    enabled: bool,
    allow_long_promotion: bool,
    min_confidence: float = 0.60,
    pass_threshold: float = 0.55,
) -> Optional[HunterEscalationGateway]:
    if not enabled:
        return None
    from strategies.mtf_sniper.intelligence.hunter_adapter import JevHunterAdapter

    return HunterEscalationGateway(
        evaluator=JevHunterAdapter(),
        config=HunterEscalationConfig(
            enabled=True,
            allow_long_promotion=allow_long_promotion,
            min_confidence=min_confidence,
            score=ScoreConfig(pass_threshold=pass_threshold),
        ),
    )
