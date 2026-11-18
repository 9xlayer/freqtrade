# MTF Sniper — Data Contracts

**Package:** `strategies/mtf_sniper/` · **Source of truth:** `core/contracts.py`, `core/enums.py`, `core/candidate_id.py`  
**Version:** matches `STRATEGY_VERSION` in `__init__.py` (currently `4.4.1-deterministic`)

This document mirrors **implemented** schemas. Target / future designs are marked explicitly and are **not** live behavior.

All inter-module dataclasses are `frozen=True`.

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
| `make_candidate_id` | Build canonical ID |
| `parse_candidate_id` | Parse → `ParsedCandidateId(pair, side, epoch, candle_index)` |
| `is_canonical_candidate_id` | Structural validity (side ∈ {long,short}, pair contains `/`) |
| `candidate_id_matches_trade` | Optional pair/side context match |
| `CANDIDATE_ID_MISSING` | Sentinel when correlation cannot be reconstructed |
| `CANDIDATE_ID_CUSTOM_DATA_KEY` | `"mtf_candidate_id"` on Trade custom data |

**Not valid:** `{pair}_{side}_{epoch}` (missing candle index), ad-hoc tag/timestamp IDs, or IDs that fail pair/side match on read.

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

### 3.5 `RiskAuthorization` (frozen) — authorization gate, not sizing authority

| Field | Type | Live meaning |
|---|---|---|
| `is_approved` | `bool` | Only field `confirm_trade_entry` currently consumes |
| `stake_amount` | `float` | Echo of Freqtrade-proposed stake (metadata) |
| `leverage` | `float` | Echo / cap metadata |
| `stoploss_rate` | `float` | Pair/default SL rate used in auth context |
| `rejection_reasons` | `List[str]` | |
| `risk_budget_used_pct` | `Optional[float]` | **`None`** until stop-distance budgeting exists |
| `risk_per_trade_pct` | `Optional[float]` | **`None`** until implemented |
| `stop_distance_pct` | `float` | |
| `max_open_positions` | `int` | |
| `open_positions` | `int` | |
| `directional_open` | `int` | |
| `total_exposure_notional` | `float` | `sum(stake * leverage)` |
| `total_exposure_pct` | `Optional[float]` | Only when `wallet_equity` known |
| `wallet_equity` | `Optional[float]` | |
| `sizing_method` | `str` | Default `"freqtrade_proposed"` |
| `config_fingerprint` | `str` | |
| `stage_rejected` | `str` | `entry\|portfolio\|sizing\|leverage\|stoploss\|""` |

**Not implemented (do not document as live):** stop-distance risk budgeting; RiskEngine driving exchange stake/leverage from `auth.stake_amount` / `auth.leverage`. Stoploss updates still flow through Freqtrade `custom_stoploss` → `RiskEngine.get_stoploss()`.

### 3.6 Telemetry

**`TelemetryDecisionRecord`** (frozen) — base decision fields plus:

- `setup_type`, `signal_source`, `policy_version`
- `config_fingerprint`
- `feature_snapshot` (small dict — never full DataFrames)

**`TelemetryTradeRecord`** (frozen) — trade outcome + MFE/MAE; `candidate_id` must be canonical or `candidate_id_missing`.

---

## 4. Contract tests

Structural field / ID checks live in:

- `tests/strategy/test_mtf_data_contracts.py`
- `tests/strategy/test_mtf_candidate_id_hardening.py`
- `tests/strategy/test_mtf_candidate_parity.py`
