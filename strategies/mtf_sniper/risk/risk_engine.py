"""
Central risk coordinator for entry authorization, sizing, and stoploss.

JEV Decision Gate remains optional and is invoked here so Strategy stays thin.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, List, Optional, Sequence

from strategies.mtf_sniper.core.contracts import RiskAuthorization, TelemetryDecisionRecord
from strategies.mtf_sniper.core.enums import RejectionReasonCode
from strategies.mtf_sniper.risk.portfolio_protection import (
    circuit_breaker_blocks,
    directional_lock_blocks,
    distress_guard_blocks,
)
from strategies.mtf_sniper.risk.position_sizing import resolve_leverage, resolve_stake_amount
from strategies.mtf_sniper.risk.stoploss_ladder import resolve_custom_stoploss

logger = logging.getLogger(__name__)


class RiskEngine:
    """Unified risk authority used by StrategyMTFSniper callbacks."""

    def __init__(self, decision_ledger: Any | None = None) -> None:
        self.decision_ledger = decision_ledger

    def authorize_entry(
        self,
        *,
        strategy_name: str,
        pair: str,
        side: str,
        current_time: datetime,
        closed_trades: Sequence[Any],
        open_trades: Sequence[Any],
        get_price: Callable[[str, datetime], Optional[float]],
        jev_confirm: Optional[Callable[[], bool]] = None,
        entry_tag: Optional[str] = None,
        sensors_triggered: Optional[List[str]] = None,
        confluence_count: int = 1,
        market_regime: str = "",
        policy_score: float = 0.0,
        quality_grade: str = "",
        policy_verdict: str = "",
        ai_verdict: str = "",
        ai_confidence: float = 0.0,
        ai_model_version: Optional[str] = None,
        candidate_id: Optional[str] = None,
    ) -> RiskAuthorization:
        reasons: list[str] = []

        if circuit_breaker_blocks(
            closed_trades, strategy_name=strategy_name, current_time=current_time
        ):
            reasons.append(RejectionReasonCode.RISK_CIRCUIT_BREAKER_ACTIVE.value)

        if directional_lock_blocks(open_trades, side):
            reasons.append(RejectionReasonCode.RISK_DIRECTIONAL_LOCK.value)

        if distress_guard_blocks(
            open_trades, pair=pair, current_time=current_time, get_price=get_price
        ):
            reasons.append(RejectionReasonCode.RISK_PORTFOLIO_DISTRESSED.value)

        ai_verdict_out = ai_verdict
        ai_confidence_out = ai_confidence

        if not reasons and jev_confirm is not None:
            if not jev_confirm():
                reasons.append(RejectionReasonCode.AI_VETO_LOW_CONFIDENCE.value)
                if not ai_verdict_out:
                    ai_verdict_out = "VETO"

        approved = len(reasons) == 0
        auth = RiskAuthorization(
            is_approved=approved,
            stake_amount=0.0,
            leverage=10.0 if approved else 0.0,
            stoploss_rate=0.0,
            rejection_reasons=list(reasons),
        )

        self._log_decision(
            candidate_id=candidate_id
            or f"{pair}_{side}_{int(current_time.timestamp())}",
            current_time=current_time,
            pair=pair,
            side=side,
            sensors_triggered=sensors_triggered or ([entry_tag] if entry_tag else []),
            confluence_count=confluence_count,
            market_regime=market_regime,
            policy_score=policy_score,
            quality_grade=quality_grade or ("ACCEPT" if approved else "REJECT"),
            policy_verdict=policy_verdict or ("ACCEPT" if approved else "REJECT"),
            ai_verdict=ai_verdict_out or ("CONFIRM" if approved else ""),
            ai_confidence=ai_confidence_out,
            ai_model_version=ai_model_version,
            auth=auth,
        )
        return auth

    def _log_decision(
        self,
        *,
        candidate_id: str,
        current_time: datetime,
        pair: str,
        side: str,
        sensors_triggered: List[str],
        confluence_count: int,
        market_regime: str,
        policy_score: float,
        quality_grade: str,
        policy_verdict: str,
        ai_verdict: str,
        ai_confidence: float,
        ai_model_version: Optional[str],
        auth: RiskAuthorization,
    ) -> None:
        if self.decision_ledger is None:
            return
        try:
            self.decision_ledger.record(
                TelemetryDecisionRecord(
                    candidate_id=candidate_id,
                    timestamp=current_time.isoformat(),
                    pair=pair,
                    side=side,
                    sensors_triggered=list(sensors_triggered),
                    confluence_count=confluence_count,
                    market_regime=market_regime,
                    policy_score=policy_score,
                    quality_grade=quality_grade,
                    policy_verdict=policy_verdict,
                    ai_verdict=ai_verdict,
                    ai_confidence=ai_confidence,
                    ai_model_version=ai_model_version,
                    risk_approved=auth.is_approved,
                    final_action="ENTER" if auth.is_approved else "REJECT",
                    final_reasons=list(auth.rejection_reasons),
                )
            )
        except Exception as exc:
            logger.warning(f"[DecisionLedger] Failed to record decision: {exc}")

    def get_leverage(self, max_leverage: float | None) -> float:
        return resolve_leverage(max_leverage)

    def get_stake_amount(
        self,
        *,
        proposed_stake: float,
        max_stake: float,
        entry_tag: Optional[str],
        enable_dynamic_sizing: bool,
    ) -> float:
        return resolve_stake_amount(
            proposed_stake=proposed_stake,
            max_stake=max_stake,
            entry_tag=entry_tag,
            enable_dynamic_sizing=enable_dynamic_sizing,
        )

    def get_stoploss(
        self,
        *,
        trade: Any,
        current_time: datetime,
        current_profit: float,
        pair_cfg: dict,
        default_stoploss: float,
    ) -> float:
        return resolve_custom_stoploss(
            trade=trade,
            current_time=current_time,
            current_profit=current_profit,
            pair_cfg=pair_cfg,
            default_stoploss=default_stoploss,
        )
