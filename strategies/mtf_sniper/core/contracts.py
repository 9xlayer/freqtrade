"""Immutable inter-layer data contracts for the MTF Sniper engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from strategies.mtf_sniper.core.enums import DecisionAction, SensorType, TradeSide


@dataclass(frozen=True)
class TriggerEvent:
    """Raw blip emitted by an individual sensor on the current candle."""

    sensor_name: str
    sensor_type: SensorType
    side: TradeSide
    pair: str
    timestamp: datetime
    candle_index: int
    raw_metrics: Dict[str, Any]


# Note: frozen=True is shallow — nested List/Dict fields are still mutable.
# Callers must treat nested collections as read-only after construction.


@dataclass(frozen=True)
class TradeCandidate:
    """Merged / deduplicated candidate produced by CandidateBuilder."""

    candidate_id: str
    pair: str
    side: TradeSide
    primary_sensor: str
    secondary_sensors: List[str]
    confluence_count: int
    timestamp: datetime
    candle_index: int
    raw_features: Dict[str, Any]
    cooldown_bars: int = 12


@dataclass(frozen=True)
class PolicyEvaluationResult:
    """Strategic evaluation result from LongPolicy / ShortPolicy."""

    is_valid: bool
    score: float
    quality_grade: str
    reasons: List[str]
    suggested_stop_distance_pct: float
    target_rr: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionVerdict:
    """JEV AI gatekeeper verdict."""

    action: DecisionAction
    score: float
    confidence: float
    veto_reasons: List[str]
    model_version: Optional[str]
    payload_hash: str
    timestamp: datetime


@dataclass(frozen=True)
class RiskAuthorization:
    """Final capital / risk authorization from RiskEngine.

    Stages (documented separately): entry eligibility → portfolio authorization
    → position sizing → leverage → stop-loss. Fields below make approved values
    and rejection reasons explicit.

    Honesty rules:
    - ``None`` means unavailable / not computed (never invent placeholders).
    - ``sizing_method="freqtrade_proposed"`` = stake comes from FT proposed_stake
      (+ optional tag multiplier), NOT stop-distance risk budgeting.
    - ``total_exposure_notional`` = sum(stake * leverage); pct only when equity known.
    """

    is_approved: bool
    stake_amount: float
    leverage: float
    stoploss_rate: float
    rejection_reasons: List[str]
    # Optional metrics — None = unavailable (do not invent 0.02 / 0.0 placeholders)
    risk_budget_used_pct: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    stop_distance_pct: float = 0.0
    max_open_positions: int = 0
    open_positions: int = 0
    directional_open: int = 0
    total_exposure_notional: float = 0.0
    total_exposure_pct: Optional[float] = None
    wallet_equity: Optional[float] = None
    sizing_method: str = "freqtrade_proposed"
    config_fingerprint: str = ""
    stage_rejected: str = ""  # entry|portfolio|sizing|leverage|stoploss|""


@dataclass(frozen=True)
class TelemetryDecisionRecord:
    """Decision ledger row for ACCEPT / REJECT audit."""

    candidate_id: str
    timestamp: str
    pair: str
    side: str
    sensors_triggered: List[str]
    confluence_count: int
    market_regime: str
    policy_score: float
    quality_grade: str
    policy_verdict: str
    ai_verdict: str
    ai_confidence: float
    ai_model_version: Optional[str]
    risk_approved: bool
    final_action: str
    final_reasons: List[str]
    # Phase 7 correlation / provenance (optional for backward compat)
    setup_type: str = ""
    signal_source: str = ""
    policy_version: str = ""
    config_fingerprint: str = ""
    feature_snapshot: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TelemetryTradeRecord:
    """Trade ledger row for execution outcome attribution."""

    trade_id: int
    candidate_id: str
    pair: str
    side: str
    open_date: str
    close_date: str
    open_rate: float
    close_rate: float
    stake_amount: float
    leverage: float
    pnl_usdt: float
    pnl_pct: float
    max_favorable_excursion: float
    max_adverse_excursion: float
    exit_reason: str
