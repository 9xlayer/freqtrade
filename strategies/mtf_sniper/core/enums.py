"""Core enumerations for the MTF Sniper unified trading engine."""

from enum import Enum, StrEnum


class TradeSide(Enum):
    """Direction of the trade."""

    LONG = "long"
    SHORT = "short"


class SensorType(Enum):
    """Classification of the market sensor."""

    ANOMALY = "anomaly"
    PATTERN = "pattern"


class MarketRegime(Enum):
    """Macro and intermediate market regime classification."""

    BULL_EXPANSION = "bull_expansion"
    BULL_PULLBACK = "bull_pullback"
    BEAR_CAPITULATION = "bear_capitulation"
    BEAR_RELIEF = "bear_relief"
    CHOPPY_RANGING = "choppy_ranging"
    VOLATILITY_SQUEEZE = "volatility_squeeze"


class DecisionAction(StrEnum):
    """Verdict issued by strategic policy or AI gatekeeper."""

    CONFIRM = "confirm"
    VETO = "veto"
    HOLD = "hold"


class RejectionReasonCode(StrEnum):
    """Standardized rejection reason codes for telemetry and audit."""

    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    CONTRADICTORY_SIGNALS = "CONTRADICTORY_SIGNALS"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"

    REJECT_REGIME_INCOMPATIBLE = "REJECT_REGIME_INCOMPATIBLE"
    REJECT_UPPER_WICK_REJECTION = "REJECT_UPPER_WICK_REJECTION"
    REJECT_LOWER_WICK_REJECTION = "REJECT_LOWER_WICK_REJECTION"
    REJECT_CLOSE_POSITION_WEAK = "REJECT_CLOSE_POSITION_WEAK"
    REJECT_RSI_OVEREXTENDED = "REJECT_RSI_OVEREXTENDED"
    REJECT_EMA_DISTANCE_TOO_FAR = "REJECT_EMA_DISTANCE_TOO_FAR"
    REJECT_SCORE_BELOW_THRESHOLD = "REJECT_SCORE_BELOW_THRESHOLD"

    AI_VETO_LOW_CONFIDENCE = "AI_VETO_LOW_CONFIDENCE"

    RISK_CIRCUIT_BREAKER_ACTIVE = "RISK_CIRCUIT_BREAKER_ACTIVE"
    RISK_DIRECTIONAL_LOCK = "RISK_DIRECTIONAL_LOCK"
    RISK_PORTFOLIO_DISTRESSED = "RISK_PORTFOLIO_DISTRESSED"
    RISK_EXPOSURE_LIMIT_EXCEEDED = "RISK_EXPOSURE_LIMIT_EXCEEDED"
