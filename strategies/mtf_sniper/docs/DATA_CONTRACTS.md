# MTF SNIPER QUANTITATIVE TRADING ENGINE
## Data Contracts & Type Definitions Specification
### Location: `strategies/mtf_sniper/docs/DATA_CONTRACTS.md` · Version: 3.2

---

## 1. Overview

This document specifies the exact schemas, enumerations, and dataclasses used across all subsystems of the Unified Trading Engine. All inter-module communication is strictly typed and immutable (`frozen=True`) to guarantee zero side-effects.

---

## 2. Core Enumerations (`core/enums.py`)

```python
from enum import Enum

class TradeSide(str, Enum):
    """Direction of the trade."""
    LONG = "long"
    SHORT = "short"

class SensorType(str, Enum):
    """Classification of the market sensor."""
    ANOMALY = "anomaly"      # Volatility / volume spikes, extreme price displacement
    PATTERN = "pattern"      # Technical confluence, EMA formations, breakout structures

class MarketRegime(str, Enum):
    """Macro and intermediate market regime classification."""
    BULL_EXPANSION = "bull_expansion"      # Strong uptrend above EMA 50/200 1D, expanding volatility
    BULL_PULLBACK = "bull_pullback"        # Bullish macro with 4H/1H retrace
    BEAR_CAPITULATION = "bear_capitulation"# Violent sell-off below key daily moving averages
    BEAR_RELIEF = "bear_relief"            # Bear market counter-trend bounce
    CHOPPY_RANGING = "choppy_ranging"      # Low ADX, compressed Bollinger Bands, high false-breakout risk
    VOLATILITY_SQUEEZE = "volatility_squeeze" # Pre-breakout compression zone

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
```

---

## 3. Data Contracts (`core/contracts.py`)

### 3.1. Sensor Trigger Event
Emitted by an individual sensor when a threshold or pattern condition is detected on the current candle.

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from strategies.mtf_sniper.core.enums import TradeSide, SensorType, MarketRegime, DecisionAction

@dataclass(frozen=True)
class TriggerEvent:
    sensor_name: str                      # e.g., "volume_surge", "a80_plus"
    sensor_type: SensorType               # ANOMALY or PATTERN
    side: TradeSide                       # LONG or SHORT
    pair: str                             # e.g., "BTC/USDT:USDT"
    timestamp: datetime                   # Candle timestamp
    candle_index: int                     # Positional row index in analyzed dataframe
    raw_metrics: Dict[str, Any]           # Snapshot: close, volume_ratio, rsi, atr
```

---

### 3.2. Trade Candidate (Merged & Deduplicated)
Produced by `CandidateBuilder` after merging agreeing sensors, resolving conflicts, and applying directional cooldown.

```python
@dataclass(frozen=True)
class TradeCandidate:
    candidate_id: str                     # Format: {pair}_{side}_{timestamp_epoch}
    pair: str
    side: TradeSide
    primary_sensor: str                   # Dominant trigger (Pattern > Anomaly)
    secondary_sensors: List[str]         # Other sensors that triggered simultaneously
    confluence_count: int                 # Total number of sensors agreeing (>= 1)
    timestamp: datetime
    candle_index: int
    raw_features: Dict[str, Any]          # Full feature snapshot at trigger time
    cooldown_bars: int = 12
```

---

### 3.3. Policy Evaluation Result
Returned by `DirectionalTradingEngine` (`LongPolicy` or `ShortPolicy`) after comprehensive strategic evaluation.

```python
@dataclass
class PolicyEvaluationResult:
    is_valid: bool                        # True if passed all policy criteria
    score: float                          # Quality score in range [0.0, 1.0]
    quality_grade: str                    # "A+", "A", "B", or "REJECT"
    reasons: List[str]                    # Informational reason codes
    suggested_stop_distance_pct: float   # Suggested stop distance (e.g. 0.018 for 1.8%)
    target_rr: float                      # Expected risk-to-reward ratio (e.g. 2.0)
    metadata: Dict[str, Any] = field(default_factory=dict)
```

---

### 3.4. JEV AI Decision Verdict
Returned by the `JevDecisionAdapter` representing the final intelligence layer verdict.

```python
@dataclass
class DecisionVerdict:
    action: DecisionAction                # CONFIRM or VETO
    score: float                          # AI confidence / trajectory score
    confidence: float                     # Model certainty
    veto_reasons: List[str]               # Reason codes if vetoed
    model_version: Optional[str]          # Resolved model (e.g. "jev-1.14.0")
    payload_hash: str                     # SHA-256 hash of the 32-bar trajectory payload
    timestamp: datetime                   # Timestamp of evaluation
```

---

### 3.5. Risk Authorization
Returned by the `UnifiedRiskEngine` giving final execution authorization.

```python
@dataclass
class RiskAuthorization:
    is_approved: bool                     # True if trade is approved for execution
    stake_amount: float                   # Allocated capital in quote currency (USDT)
    leverage: float                       # Allocated leverage (max 10.0x)
    stoploss_rate: float                  # Exact stoploss rate (e.g. -0.018)
    rejection_reasons: List[str]          # Empty if approved, otherwise rejection codes
    risk_budget_used_pct: float = 0.02    # Risk budget percentage applied (e.g. 2%)
```

---

### 3.6. Telemetry Records (`telemetry/`)
Data structures persisted to the Decision and Trade Ledgers for performance auditability.

```python
@dataclass
class TelemetryDecisionRecord:
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
    final_action: str                     # "ENTER" or "REJECT"
    final_reasons: List[str]

@dataclass
class TelemetryTradeRecord:
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
    max_favorable_excursion: float       # MFE (peak profit ratio achieved)
    max_adverse_excursion: float         # MAE (maximum drawdown suffered)
    exit_reason: str
```
