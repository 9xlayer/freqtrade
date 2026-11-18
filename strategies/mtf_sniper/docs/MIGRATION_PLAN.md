# MTF SNIPER QUANTITATIVE TRADING ENGINE
## Phased Migration Plan & Quality Gates (P0 $\rightarrow$ P4)
### Location: `strategies/mtf_sniper/docs/MIGRATION_PLAN.md` · Version: 3.2

---

## 1. Migration Overview

To ensure zero downtime, prevent regressions on active live trading, and maintain the proven 5-year statistical edge (+24,282 USDT, PF 1.55 on BTC & ETH), the refactoring follows a strict 5-stage sequential migration plan:

```
+---------------+     +---------------+     +---------------+     +---------------+     +---------------+
|   PHASE P0    | --> |   PHASE P1    | --> |   PHASE P2    | --> |   PHASE P3    | --> |   PHASE P4    |
| Audit & Base- |     | Core Types &  |     | Pipeline &    |     | Risk Engine & |     | Strategy Opt  |
| line Freeze   |     | Sensor Suite  |     | Parity Engine |     | Ledger        |     | (Long Hunter) |
+---------------+     +---------------+     +---------------+     +---------------+     +---------------+
```

---

## 2. Phase-by-Phase Deliverables & Quality Gates

### Phase P0: Forensic Audit & Baseline Freeze
* **Objectives**:
  - Trace the complete AST call graph of `StrategyMTFSniper.py` using GitNexus.
  - Freeze the exact 5-year baseline backtest (2022-01-01 to 2026-11-17) on Binance Futures 10x with zero modifications.
  - Document the exact baseline trade count, profit, drawdown, and annual win rates as benchmark metrics.
* **Target Files**:
  - `memory-bank/activeContext.md`, `memory-bank/progress.md`
  - `strategies/mtf_sniper/docs/`
* **Quality Gate (Definition of Done)**:
  - Baseline metrics saved and verified: Net Profit $\approx +24,282.26$ USDT, Total Trades $= 273$, Max DD $\le 32.0\%$, Profit Factor $= 1.55$.
  - Test suite `tests/strategy/test_jev_architecture.py` passes 56/56 tests.

---

### Phase P1: Core Contracts & Market Sensor Suite ✅ COMPLETE
* **Objectives**:
  - Implement `core/enums.py` and `core/contracts.py`.
  - Extract and flatten sensors into `signals/sensors/`:
    - `VolumeSurgeSensor` (Anomaly)
    - `VolatilityExpansionSensor` (Anomaly)
    - `WaterfallDropSensor` (Anomaly)
    - `A80PlusSensor` (Pattern)
    - `SwingBreakoutSensor` (Pattern)
  - Implement `SensorSuite` registry and `CandidateBuilder` with multi-trigger confluence merging and directional cooldown.
* **Target Files**:
  - `strategies/mtf_sniper/core/`
  - `strategies/mtf_sniper/signals/`
  - `tests/strategy/test_mtf_sniper_sensors.py`
* **Quality Gate (Definition of Done)**:
  - ✅ Sensor + CandidateBuilder unit tests PASS (17/17).
  - ✅ Existing `strategies/active/StrategyMTFSniper.py` remains untouched and functional.
  - ✅ Full strategy suite still green (85 tests incl. JEV + execution safety).

---

### Phase P2: Directional Trading Engine & Behavioral Parity ✅ DONE
* **Objectives**:
  - Implement `directional/engine.py`, `directional/long/`, and `directional/short/`.
  - Implement the thin `strategy_mtf_sniper.py` entrypoint (class `StrategyMTFSniper`) delegating to the new modular engine.
  - Re-run the identical 5-year backtest.
* **Done**:
  - ✅ Live path: sniper masks + hunter_merge + EntryPipeline.apply_parity
  - ✅ Experimental path quarantined: SensorSuite → CandidateBuilder → DirectionalEngine
  - ✅ ARCHITECTURE.md documents LIVE vs EXPERIMENTAL (single source of truth)
  - ✅ Hunter gate constants shared with experimental policies
  - ✅ Parity BT **PASS**: 273 / +24,282.26 / WR 59% / DD 32%
* **Note**: Experimental high-recall catalog still diverges (historical 572 / −796) — not live.
* **Target Files**:
  - `strategies/mtf_sniper/directional/`
  - `strategies/mtf_sniper/strategy_mtf_sniper.py`
* **Quality Gate (Definition of Done) - THE ZERO REGRESSION GATE**:
  - **Behavioral Parity Test**: Backtest on 2022–2026 data must yield **EXACTLY 273 trades**, identical entry/exit timestamps, and identical PnL ($\pm 0.0\%$ trade drift).
  - Any discrepancy is treated as a blocking bug.

---

### Phase P3: Centralized Risk Engine & Telemetry Ledger ✅ DONE
* **Objectives**:
  - Implement `risk/risk_engine.py`, `position_sizing.py`, and `portfolio_protection.py`.
  - Move Streak Circuit Breaker, Directional Lock, and Distress Guard out of the strategy class into the Risk Engine.
  - Implement `telemetry/decision_ledger.py` and `telemetry/trade_ledger.py` to record every ACCEPT/REJECT decision with reason codes.
* **Done**:
  - ✅ `risk/` modules + stoploss ladder
  - ✅ Strategy wired through RiskEngine; parity BT PASS 273 / +24,282.26
  - ✅ `tests/strategy/test_mtf_sniper_risk.py`
  - ✅ `telemetry/{decision,trade}_ledger.py` + Strategy `order_filled` / authorize logging (`v3.2.0-p3-telemetry`)
  - ✅ `tests/strategy/test_mtf_sniper_telemetry.py`
  - ✅ Evidence: 302 decision rows (273 ENTER / 29 REJECT w/ reason codes) + 273 trade rows w/ MFE/MAE
* **Still open (optional)**: none (exit extract done in `execution/exit_policy.py`, `v3.2.0-p3-exit`)
* **Target Files**:
  - `strategies/mtf_sniper/risk/`
  - `strategies/mtf_sniper/telemetry/`
  - `strategies/mtf_sniper/execution/exit_policy.py`
  - `tests/strategy/test_mtf_sniper_risk.py`
  - `tests/strategy/test_mtf_sniper_telemetry.py`
  - `tests/strategy/test_mtf_sniper_exit_policy.py`
* **Quality Gate (Definition of Done)**:
  - ✅ Execution safety drills + risk/telemetry/exit unit tests pass.
  - ✅ Decision Ledger logs rejected candidates with structured reason codes.
  - ✅ Behavioral parity BT re-PASS after telemetry + exit extract (`v3.2.0-p3-exit`).

---

### Phase P4: Long Hunter Algorithmic Optimization ✅ GATED
* **Objectives**:
  - Analyze the historical ~92 Long Hunter trades recorded in the Decision/Trade Ledger.
  - Isolate root causes (e.g. overextension, upper wick rejection, chasing beyond EMA200).
  - Develop refined Long Policy setups using chronological train/test split (Walk-Forward Validation).
* **Done**:
  - ✅ Evidence: unfiltered **91 / −702**; portfolio **+5.9k** vs parity **+24.3k**
  - ✅ Root cause: chase / no follow-through (28 losers with MFE &lt; 1% = −2.2k)
  - ✅ `directional/long/hunter_gates.py` + wire in `hunter_merge.py` (`v3.2.0-p4-hunter-gates`)
  - ✅ Post-gate: `hunter_long=true` ⇒ **0** hunter trades; portfolio **identical** to parity 273 / +24,282.26
  - ✅ Default remains `enable_hunter_long=false`
* **Deferred** (optional future):
  - [ ] New detector / relaxed config with proven OOS expectancy &gt; 0
* **Target Files**:
  - `strategies/mtf_sniper/directional/long/hunter_gates.py`
  - `strategies/mtf_sniper/docs/P4_LONG_HUNTER_EVIDENCE.md`
* **Quality Gate (Definition of Done)**:
  - ✅ Enabling hunter long no longer degrades Profit Factor vs parity (gates → no-op).
  - Positive OOS expectancy for a *productive* hunter set remains future work if/when a better detector appears.

---

## 3. P5 — SensorSuite Live Cutover (v4.0.0) ✅

- Live entry = SensorSuite → CandidateBuilder → DirectionalEngine
- Deleted `strategies/active/`; JEV Decision lives under `intelligence/`
- Compose → `--strategy-path …/mtf_sniper`
- Legacy parity checklist (273 trades) retired — new BT baseline is the gate

## 4. Live Verification Checklist (post-parity)

| Check | Requirement |
|---|---|
| 1. Unit suite | `test_mtf_sniper*` + `test_jev_architecture` green |
| 2. Full BT | Record trades / PnL / DD as new baseline |
| 3. Docker | `strategy-path` + `PYTHONPATH=/freqtrade` |
