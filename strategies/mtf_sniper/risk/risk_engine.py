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
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

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
        default_leverage: float = 10.0,
        tag_multipliers: Mapping[str, float] | None = None,
    ) -> None:
        self.decision_ledger = decision_ledger
        self.max_open_trades = max_open_trades
        self.config_fingerprint = config_fingerprint
        self.default_leverage = default_leverage
        self.tag_multipliers = dict(tag_multipliers) if tag_multipliers is not None else None
        self._seen_reject_bursts: set[tuple[str, str, str, tuple[str, ...], int]] = set()

    @staticmethod
    def _unpack_jev_result(
        raw: bool | Mapping[str, Any],
    ) -> tuple[bool, str, float, str | None, str]:
        if isinstance(raw, Mapping):
            return (
                bool(raw.get("approved", False)),
                str(raw.get("ai_verdict") or ""),
                float(raw.get("ai_confidence") or 0.0),
                raw.get("ai_model_version"),
                str(raw.get("market_regime") or ""),
            )
        return raw, ("CONFIRM" if raw else "VETO"), 0.0, None, ""

    def _is_duplicate_reject_burst(
        self,
        *,
        pair: str,
        side: str,
        candidate_id: str,
        reasons: Sequence[str],
        current_time: datetime,
    ) -> bool:
        dt = (
            current_time.replace(tzinfo=UTC)
            if current_time.tzinfo is None
            else current_time.astimezone(UTC)
        )
        candle_bucket = int(dt.timestamp() // 900)
        key = (pair, side.lower(), candidate_id, tuple(reasons), candle_bucket)
        if key in self._seen_reject_bursts:
            return True
        if len(self._seen_reject_bursts) >= 256:
            self._seen_reject_bursts.clear()
        self._seen_reject_bursts.add(key)
        return False

    def _resolve_ai_confirmation(
        self,
        *,
        reasons: list[str],
        stage: str,
        jev_confirm: Callable[[], bool | Mapping[str, Any]] | None,
        ai_verdict: str,
        ai_confidence: float,
        ai_model_version: str | None,
        market_regime: str,
    ) -> tuple[str, str, float, str | None, str]:
        ai_verdict_out = ai_verdict
        ai_confidence_out = ai_confidence
        ai_model_out = ai_model_version
        market_regime_out = market_regime
        if reasons or jev_confirm is None:
            return stage, ai_verdict_out, ai_confidence_out, ai_model_out, market_regime_out

        jev_ok, j_verdict, j_conf, j_model, j_regime = self._unpack_jev_result(jev_confirm())
        ai_verdict_out = ai_verdict_out or j_verdict
        ai_confidence_out = ai_confidence_out or j_conf
        ai_model_out = ai_model_out or j_model
        market_regime_out = market_regime_out or j_regime
        if not jev_ok:
            reasons.append(RejectionReasonCode.AI_VETO_LOW_CONFIDENCE.value)
            stage = stage or "entry"
            ai_verdict_out = ai_verdict_out or "VETO"
        return stage, ai_verdict_out, ai_confidence_out, ai_model_out, market_regime_out

    @staticmethod
    def _compute_exposure_metrics(
        *,
        open_trades: Sequence[Any],
        approved: bool,
        stake: float,
        lev: float,
        wallet_equity: float | None,
    ) -> tuple[float, float | None, float | None]:
        total_exposure = 0.0
        for t in open_trades:
            try:
                total_exposure += float(getattr(t, "stake_amount", 0.0) or 0.0) * float(
                    getattr(t, "leverage", 1.0) or 1.0
                )
            except (TypeError, ValueError, AttributeError):
                continue
        if approved:
            total_exposure += stake * (lev or 1.0)

        equity: float | None = None
        if wallet_equity is not None:
            try:
                if wallet_equity > 0:
                    equity = wallet_equity
            except (TypeError, ValueError):
                equity = None
        exposure_pct: float | None = (total_exposure / equity) if equity is not None else None
        return total_exposure, equity, exposure_pct

    def _check_portfolio_blocks(
        self,
        strategy_name: str,
        pair: str,
        side: str,
        current_time: datetime,
        closed_trades: Sequence[Any],
        open_trades: Sequence[Any],
        get_price: Callable[[str, datetime], float | None],
        open_n: int,
    ) -> tuple[list[str], str]:
        reasons: list[str] = []
        stage = ""
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
        return reasons, stage

    def authorize_entry(
        self,
        *,
        strategy_name: str,
        pair: str,
        side: str,
        current_time: datetime,
        closed_trades: Sequence[Any],
        open_trades: Sequence[Any],
        get_price: Callable[[str, datetime], float | None],
        jev_confirm: Callable[[], bool | Mapping[str, Any]] | None = None,
        entry_tag: str | None = None,
        sensors_triggered: list[str] | None = None,
        confluence_count: int = 1,
        market_regime: str = "",
        policy_score: float = 0.0,
        quality_grade: str = "",
        policy_verdict: str = "",
        ai_verdict: str = "",
        ai_confidence: float = 0.0,
        ai_model_version: str | None = None,
        candidate_id: str | None = None,
        setup_type: str = "",
        signal_source: str = "",
        policy_version: str = "",
        feature_snapshot: dict | None = None,
        proposed_stake: float = 0.0,
        max_stake: float = 0.0,
        enable_dynamic_sizing: bool = False,
        max_leverage: float = 10.0,
        default_stoploss: float = -0.28,
        stop_distance_pct: float = 0.0,
        wallet_equity: float | None = None,
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
        reasons, stage = self._check_portfolio_blocks(
            strategy_name=strategy_name,
            pair=pair,
            side=side,
            current_time=current_time,
            closed_trades=closed_trades,
            open_trades=open_trades,
            get_price=get_price,
            open_n=open_n,
        )

        stage, ai_verdict_out, ai_confidence_out, ai_model_out, market_regime_out = (
            self._resolve_ai_confirmation(
                reasons=reasons,
                stage=stage,
                jev_confirm=jev_confirm,
                ai_verdict=ai_verdict,
                ai_confidence=ai_confidence,
                ai_model_version=ai_model_version,
                market_regime=market_regime,
            )
        )

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
                tag_multipliers=self.tag_multipliers,
            )
            lev = resolve_leverage(max_leverage, target=self.default_leverage)

        total_exposure, equity, exposure_pct = self._compute_exposure_metrics(
            open_trades=open_trades,
            approved=approved,
            stake=stake,
            lev=lev,
            wallet_equity=wallet_equity,
        )
        # Stop-distance risk budget is not implemented — leave None (unavailable).
        risk_per_trade: float | None = None

        auth = RiskAuthorization(
            is_approved=approved,
            stake_amount=float(stake),
            leverage=float(lev) if approved else 0.0,
            stoploss_rate=default_stoploss if approved else 0.0,
            rejection_reasons=list(reasons),
            risk_budget_used_pct=None,
            risk_per_trade_pct=risk_per_trade,
            stop_distance_pct=float(stop_distance_pct or 0.0),
            max_open_positions=self.max_open_trades,
            open_positions=open_n,
            directional_open=directional_open,
            total_exposure_notional=float(total_exposure),
            total_exposure_pct=exposure_pct,
            wallet_equity=equity,
            sizing_method="freqtrade_proposed",
            config_fingerprint=self.config_fingerprint,
            stage_rejected="" if approved else (stage or "portfolio"),
        )

        from strategies.mtf_sniper.core.candidate_id import CANDIDATE_ID_MISSING

        cid_resolved = candidate_id or CANDIDATE_ID_MISSING
        if not approved and self._is_duplicate_reject_burst(
            pair=pair,
            side=side,
            candidate_id=cid_resolved,
            reasons=auth.rejection_reasons,
            current_time=current_time,
        ):
            return auth

        default_ai_verdict = (
            "CONFIRM"
            if (approved and jev_confirm is not None)
            else ("DISABLED" if approved else "")
        )
        self._log_decision(
            candidate_id=cid_resolved,
            current_time=current_time,
            pair=pair,
            side=side,
            sensors_triggered=sensors_triggered or ([entry_tag] if entry_tag else []),
            confluence_count=confluence_count,
            market_regime=market_regime_out,
            policy_score=policy_score,
            quality_grade=quality_grade or ("ACCEPT" if approved else "REJECT"),
            policy_verdict=policy_verdict or ("ACCEPT" if approved else "REJECT"),
            ai_verdict=ai_verdict_out or default_ai_verdict,
            ai_confidence=ai_confidence_out,
            ai_model_version=ai_model_out,
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
        sensors_triggered: list[str],
        confluence_count: int,
        market_regime: str,
        policy_score: float,
        quality_grade: str,
        policy_verdict: str,
        ai_verdict: str,
        ai_confidence: float,
        ai_model_version: str | None,
        auth: RiskAuthorization,
        setup_type: str = "",
        signal_source: str = "",
        policy_version: str = "",
        feature_snapshot: dict | None = None,
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
        return resolve_leverage(max_leverage, target=self.default_leverage)

    def get_stake_amount(
        self,
        *,
        proposed_stake: float,
        max_stake: float,
        entry_tag: str | None,
        enable_dynamic_sizing: bool,
        counter_trend_scale: float = 1.0,
    ) -> float:
        return resolve_stake_amount(
            proposed_stake=proposed_stake,
            max_stake=max_stake,
            entry_tag=entry_tag,
            enable_dynamic_sizing=enable_dynamic_sizing,
            counter_trend_scale=counter_trend_scale,
            tag_multipliers=self.tag_multipliers,
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
