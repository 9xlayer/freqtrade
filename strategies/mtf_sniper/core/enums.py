"""Core enumerations for the MTF Sniper unified trading engine."""
from enum import Enum


class TradeSide(str, Enum):
    """Direction of the trade."""

    LONG = "long"
    SHORT = "short"


class SensorType(str, Enum):
    """Classification of the market sensor."""

    ANOMALY = "anomaly"  # Volatility / volume spikes, extreme price displacement
    PATTERN = "pattern"  # Technical confluence, EMA formations, breakout structures


class MarketRegime(str, Enum):
    """Macro and intermediate market regime classification."""

    BULL_EXPANSION = "bull_expansion"
    BULL_PULLBACK = "bull_pullback"
    BEAR_CAPITULATION = "bear_capitulation"
    BEAR_RELIEF = "bear_relief"
    CHOPPY_RANGING = "choppy_ranging"
    VOLATILITY_SQUEEZE = "volatility_squeeze"


class DecisionAction(str, Enum):
    """Verdict issued by strategic policy or AI gatekeeper."""

    CONFIRM = "confirm"
    VETO = "veto"
    HOLD = "hold"


class RejectionReasonCode(str, Enum):
    """Standardized rejection reason codes for telemetry and audit."""

    # Sensor & Builder reasons
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    CONTRADICTORY_SIGNALS = "CONTRADICTORY_SIGNALS"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"

    # Directional Engine reasons
    REJECT_REGIME_INCOMPATIBLE = "REJECT_REGIME_INCOMPATIBLE"
    REJECT_UPPER_WICK_REJECTION = "REJECT_UPPER_WICK_REJECTION"
    REJECT_LOWER_WICK_REJECTION = "REJECT_LOWER_WICK_REJECTION"
    REJECT_CLOSE_POSITION_WEAK = "REJECT_CLOSE_POSITION_WEAK"
    REJECT_RSI_OVEREXTENDED = "REJECT_RSI_OVEREXTENDED"
    REJECT_EMA_DISTANCE_TOO_FAR = "REJECT_EMA_DISTANCE_TOO_FAR"
    REJECT_SCORE_BELOW_THRESHOLD = "REJECT_SCORE_BELOW_THRESHOLD"

    # JEV AI reasons
    AI_VETO_CHOPPY_REGIME = "AI_VETO_CHOPPY_REGIME"
    AI_VETO_LOW_CONFIDENCE = "AI_VETO_LOW_CONFIDENCE"
    AI_FAIL_SAFE_REJECT = "AI_FAIL_SAFE_REJECT"

    # Risk Engine reasons
    RISK_CIRCUIT_BREAKER_ACTIVE = "RISK_CIRCUIT_BREAKER_ACTIVE"
    RISK_DIRECTIONAL_LOCK = "RISK_DIRECTIONAL_LOCK"
    RISK_PORTFOLIO_DISTRESSED = "RISK_PORTFOLIO_DISTRESSED"
    RISK_INSUFFICIENT_LIQUIDATION_BUFFER = "RISK_INSUFFICIENT_LIQUIDATION_BUFFER"
    RISK_EXPOSURE_LIMIT_EXCEEDED = "RISK_EXPOSURE_LIMIT_EXCEEDED"
