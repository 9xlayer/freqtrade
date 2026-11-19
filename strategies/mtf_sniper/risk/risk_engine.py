"""
Central risk coordinator for entry authorization, sizing, and stoploss.

Stages (Phase 4):
  1. Entry eligibility (caller already passed policy)
  2. Portfolio authorization (circuit / directional / distress / max open)
  3. Position sizing (Freqtrade proposed stake path — NOT stop-distance-based)
  4. Leverage authorization
  5. Stop-loss authorization (pair default; ladder applied later in callback)

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

    def __init__(
        self,
        decision_ledger: Any | None = None,
        *,
        max_open_trades: int = 2,
        config_fingerprint: str = "",
    ) -> None:
        self.decision_ledger = decision_ledger
        self.max_open_trades = max_open_trades
        self.config_fingerprint = config_fingerprint

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
        setup_type: str = "",
        signal_source: str = "",
        policy_version: str = "",
        feature_snapshot: Optional[dict] = None,
        proposed_stake: float = 0.0,
        max_stake: float = 0.0,
        enable_dynamic_sizing: bool = False,
        max_leverage: float = 10.0,
        default_stoploss: float = -0.28,
        stop_distance_pct: float = 0.0,
        wallet_equity: Optional[float] = None,
    ) -> RiskAuthorization:
        reasons: list[str] = []
        stage = ""

        open_n = len(list(open_trades))
        side_l = side.lower()
        directional_open = sum(
            1
            for t in open_trades
            if (bool(getattr(t, "is_short", False)) and side_l == "short")
            or ((not bool(getattr(t, "is_short", False))) and side_l == "long")
        )

        # --- Stage: portfolio authorization ---
        if self.max_open_trades > 0 and open_n >= self.max_open_trades:
            reasons.append(RejectionReasonCode.RISK_EXPOSURE_LIMIT_EXCEEDED.value)
            stage = "portfolio"

        if circuit_breaker_blocks(
            closed_trades, strategy_name=strategy_name, current_time=current_time
        ):
            reasons.append(RejectionReasonCode.RISK_CIRCUIT_BREAKER_ACTIVE.value)
            stage = stage or "portfolio"

        if directional_lock_blocks(open_trades, side):
            reasons.append(RejectionReasonCode.RISK_DIRECTIONAL_LOCK.value)
            stage = stage or "portfolio"

        if distress_guard_blocks(
            open_trades, pair=pair, current_time=current_time, get_price=get_price
        ):
            reasons.append(RejectionReasonCode.RISK_PORTFOLIO_DISTRESSED.value)
            stage = stage or "portfolio"

        ai_verdict_out = ai_verdict
        ai_confidence_out = ai_confidence

        # Decision Gate is entry-eligibility adjacent (confirm path)
        if not reasons and jev_confirm is not None:
            if not jev_confirm():
                reasons.append(RejectionReasonCode.AI_VETO_LOW_CONFIDENCE.value)
                stage = stage or "entry"
                if not ai_verdict_out:
                    ai_verdict_out = "VETO"

        approved = len(reasons) == 0

        # --- Stage: sizing + leverage (explicit approved values) ---
        stake = 0.0
        lev = 0.0
        if approved:
            stake = resolve_stake_amount(
                proposed_stake=float(proposed_stake or 0.0),
                max_stake=float(max_stake or proposed_stake or 0.0),
                entry_tag=entry_tag,
                enable_dynamic_sizing=enable_dynamic_sizing,
            )
            lev = resolve_leverage(max_leverage)

        # Notional exposure = sum(stake * leverage). Pct only if equity known.
        total_exposure = 0.0
        for t in open_trades:
            try:
                total_exposure += float(getattr(t, "stake_amount", 0.0) or 0.0) * float(
                    getattr(t, "leverage", 1.0) or 1.0
                )
            except Exception:
                continue
        if approved:
            total_exposure += float(stake) * float(lev or 1.0)

        equity: Optional[float] = None
        if wallet_equity is not None:
            try:
                eq = wallet_equity
                if eq > 0:
                    equity = eq
            except (TypeError, ValueError):
                equity = None
        exposure_pct: Optional[float] = (
            (total_exposure / equity) if equity is not None else None
        )
        # Stop-distance risk budget is not implemented — leave None (unavailable).
        risk_per_trade: Optional[float] = None
        if stop_distance_pct and stop_distance_pct > 0 and equity is not None:
            # Only claim per-trade risk when an explicit stop distance was supplied
            # AND we sized from it (we do not today). Keep None until that lands.
            risk_per_trade = None

        auth = RiskAuthorization(
            is_approved=approved,
            stake_amount=float(stake),
            leverage=float(lev) if approved else 0.0,
            stoploss_rate=default_stoploss if approved else 0.0,
            rejection_reasons=list(reasons),
            risk_budget_used_pct=None,
            risk_per_trade_pct=risk_per_trade,
            stop_distance_pct=float(stop_distance_pct or 0.0),
            max_open_positions=int(self.max_open_trades),
            open_positions=open_n,
            directional_open=int(directional_open),
            total_exposure_notional=float(total_exposure),
            total_exposure_pct=exposure_pct,
            wallet_equity=equity,
            sizing_method="freqtrade_proposed",
            config_fingerprint=self.config_fingerprint,
            stage_rejected="" if approved else (stage or "portfolio"),
        )

        from strategies.mtf_sniper.core.candidate_id import CANDIDATE_ID_MISSING

        self._log_decision(
            candidate_id=candidate_id or CANDIDATE_ID_MISSING,
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
            setup_type=setup_type or (entry_tag or ""),
            signal_source=signal_source,
            policy_version=policy_version,
            feature_snapshot=feature_snapshot or {},
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
        setup_type: str = "",
        signal_source: str = "",
        policy_version: str = "",
        feature_snapshot: Optional[dict] = None,
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
                    setup_type=setup_type,
                    signal_source=signal_source,
                    policy_version=policy_version,
                    config_fingerprint=auth.config_fingerprint,
                    feature_snapshot=dict(feature_snapshot or {}),
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
