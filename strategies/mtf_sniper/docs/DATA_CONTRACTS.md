# MTF Sniper — Data Contracts

**Package:** `strategies/mtf_sniper/` · **Source of truth:** `core/contracts.py`, `core/enums.py`, `core/candidate_id.py`  
**Version:** matches `STRATEGY_VERSION` in `__init__.py` (currently `4.5.0-waterfall`)

This document mirrors **implemented** schemas. Target / future designs are marked explicitly and are **not** live behavior.

All inter-module dataclasses are `frozen=True` (**shallow** immutability only): the dataclass instance cannot rebind fields, but nested `List` / `Dict` values remain mutable unless callers treat them as read-only.
---

## 1. Enumerations (`core/enums.py`)

| Enum | Values |
|---|---|
| `TradeSide` | `long`, `short` |
| `SensorType` | `anomaly`, `pattern` |
| `MarketRegime` | `bull_expansion`, `bull_pullback`, `bear_capitulation`, `bear_relief`, `choppy_ranging`, `volatility_squeeze` |
| `DecisionAction` | `confirm`, `veto`, `hold` |
| `RejectionReasonCode` | Sensor/Builder, Directional, JEV AI, Risk codes (see source) |

---

## 2. Candidate identity (`core/candidate_id.py`)

Canonical format:

```text
{pair}_{side}_{utc_epoch}_{candle_index}
```

Examples: `BTC/USDT:USDT_short_1717243200_42`

| Helper | Role |
|---|---|
| `make_candidate_id` | Build canonical ID; validates pair (`/`), side exact `long`/`short`, Integral `candle_index >= 0`, `datetime`/`Timestamp` only |
| `parse_candidate_id` | Parse unique canonical form only |
| `is_canonical_candidate_id` | Parse + remake equality (round-trip stable) |
| `candidate_id_matches_fields` | Match ID vs pair/side/timestamp/candle_index |
| `candidate_id_matches_trade` | Optional pair/side context match |
| `CANDIDATE_ID_MISSING` | Sentinel when correlation cannot be reconstructed |
| `CANDIDATE_ID_CUSTOM_DATA_KEY` | `"mtf_candidate_id"` on Trade custom data |

`CandidateRegistry.remember` rebuilds/replaces any provided `candidate_id` that does not match the candidate’s pair/side/timestamp/index. `lookup` requires `current_time` for age-bounded results (default max-age 6h); `allow_unbounded=True` is diagnostics-only.

**Not valid:** missing candle index; uppercase side (`LONG`); leading-zero epoch/index (`01`); negative index; bad pair; ad-hoc tag IDs.
Correlation window: `DEFAULT_LOOKUP_MAX_AGE` = **6 hours** (shared by `CandidateRegistry.lookup` and DataFrame reconstruct fallback).

---

## 3. Contracts (`core/contracts.py`)

### 3.1 `TriggerEvent` (frozen)

| Field | Type | Notes |
|---|---|---|
| `sensor_name` | `str` | e.g. `short_structure` |
| `sensor_type` | `SensorType` | |
| `side` | `TradeSide` | |
| `pair` | `str` | |
| `timestamp` | `datetime` | Candle time |
| `candle_index` | `int` | Absolute row in analyzed DF |
| `raw_metrics` | `Dict[str, Any]` | Snapshot metrics |

### 3.2 `TradeCandidate` (frozen)

| Field | Type | Notes |
|---|---|---|
| `candidate_id` | `str` | Canonical — see §2 |
| `pair` | `str` | |
| `side` | `TradeSide` | |
| `primary_sensor` | `str` | e.g. `setup:sniper_short` after composer / fast path |
| `secondary_sensors` | `List[str]` | |
| `confluence_count` | `int` | |
| `timestamp` | `datetime` | |
| `candle_index` | `int` | |
| `raw_features` | `Dict[str, Any]` | |
| `cooldown_bars` | `int` | default `12` |

Produced by **either** the vectorized fast path **or** SensorSuite → Builder → SetupComposer (same contract; see `ARCHITECTURE.md`).

### 3.3 `PolicyEvaluationResult` (frozen)

| Field | Type | Semantics |
|---|---|---|
| `is_valid` | `bool` | **Setup eligibility** — may continue toward `enter_*` |
| `score` | `float` | Quality score |
| `quality_grade` | `str` | Quality label (`A+`/`A`/`B`/`C`/`REJECT`) — **informational** at Policy |
| `reasons` | `List[str]` | e.g. `ACCEPT` |
| `suggested_stop_distance_pct` | `float` | |
| `target_rr` | `float` | |
| `metadata` | `Dict[str, Any]` | Includes `entry_tag` when accepted |

**Invariant:** `is_valid=True` with `quality_grade=REJECT` can still reach `confirm_trade_entry`. Decision Gate does **not** read `quality_grade`.

### 3.4 `DecisionVerdict` (frozen)

| Field | Type |
|---|---|
| `action` | `DecisionAction` |
| `score` | `float` |
| `confidence` | `float` |
| `veto_reasons` | `List[str]` |
| `model_version` | `Optional[str]` |
| `payload_hash` | `str` |
| `timestamp` | `datetime` |

### 3.5 Risk: two separate mechanisms (do not conflate)

#### A. `RiskAuthorization` — confirm-path **authorization gate** only

Returned by `RiskEngine.authorize_entry()` and consumed in `confirm_trade_entry()` as:

```text
return bool(auth.is_approved)
```

| Field | Live meaning on confirm path |
|---|---|
| `is_approved` | **Only field Strategy uses** to allow/deny entry |
| `stake_amount` | Diagnostic echo of estimated stake (`amount * rate / lev`, `enable_dynamic_sizing=False` in `authorize_entry` so `custom_stake_amount` is never double-multiplied); `0.0` if `amount`/`rate` unavailable |
| `leverage` | Target leverage (`get_leverage(None)` -> `10.0`) passed for exposure telemetry, **not** applied to order directly |
| `stoploss_rate` | Pair/default SL passed into authorize (context only) |
| `risk_budget_used_pct` / `risk_per_trade_pct` | **`None`** — stop-distance risk sizing **not** implemented |
| `wallet_equity` / `total_exposure_pct` | Populated from `self.wallets.get_total(stake_currency)` when available; **`None`** when unavailable |
| `sizing_method` | Label `"freqtrade_proposed"`; not proof that authorize sized the order |
| other fields | Portfolio / fingerprint / exposure metadata — see `core/contracts.py` |

**Do not describe `RiskAuthorization` as the capital allocator for the order.**

#### B. Freqtrade sizing callbacks — values that actually size the order

| Callback | Strategy → RiskEngine | Behavior (`risk/position_sizing.py`) |
|---|---|---|
| `leverage()` | `RiskEngine.get_leverage(max_leverage)` | `min(target_leverage, max_leverage)`; target default **10x** |
| `custom_stake_amount()` | `RiskEngine.get_stake_amount(...)` | Keeps Freqtrade `proposed_stake`, capped by `max_stake` |
| | | `enable_dynamic_sizing=False` (Python default) → multiplier **1.0** |
| | | If dynamic sizing **on** (Docker/config overlay: `true`) → tag multipliers from `_TAG_MULTIPLIERS`, then `min(proposed * mult, max_stake)` |

Stoploss ladder remains `custom_stoploss` → `RiskEngine.get_stoploss()` (separate from authorize metadata).

**Stabilization scope:** do not expand dynamic sizing; do not implement stop-distance risk budgeting.

### 3.6 Telemetry

**`TelemetryDecisionRecord`** (frozen) — base decision fields (`ai_verdict`, `ai_confidence`, `ai_model_version`, `market_regime`, `policy_score`) plus:

- `setup_type`, `signal_source`, `policy_version`
- `config_fingerprint`
- `feature_snapshot` (small dict — never full DataFrames)

`RiskEngine` deduplicates identical reject bursts within the same 15m candle (`_seen_reject_bursts`) so 5s throttle loops do not flood the ledger.

**`TelemetryTradeRecord`** (frozen) — trade outcome + MFE/MAE; `candidate_id` must be canonical or `candidate_id_missing`.

Both `DecisionLedger` and `TradeLedger` use a bounded in-memory `deque(maxlen=2000)` and rotate JSONL files to `<path>.1` at `10 MiB` (`telemetry/jsonl_io.py`).

---

## 4. Contract tests

Structural field / ID checks live in:

- `tests/strategy/test_mtf_data_contracts.py`
- `tests/strategy/test_mtf_candidate_id_hardening.py`
- `tests/strategy/test_mtf_candidate_parity.py`
