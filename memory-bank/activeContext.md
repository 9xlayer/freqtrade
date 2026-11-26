# Active Context

## Current focus
**Hot-Path Optimizations & Dead-Sensor Cleanup (`v4.7.0-1w-regime`, Nov 26, 2026)**:
- **Hot-path performance optimizations (zero strategy/backtest parity drift):**
  - `signals/sensors/anomaly_waterfall.py`: Cached `WaterfallMomentumSensor._compute_triggers()` on `dataframe.attrs["_mtf_waterfall_triggers_v1"]` so `SetupComposer.compose_suite` avoids running `MomentumRadar.find_candidates()` twice per DataFrame; replaced per-hit `dataframe.iloc[i]` lookups with direct `short_score` NumPy array indexing.
  - `signals/sensors/market_regime.py`: Vectorized column slicing in `MarketRegimeSensor.scan_mask_features()` (`.to_numpy()` once per DataFrame) instead of per-hit `dataframe.iloc[i]`.
  - `intelligence/jev_decision.py`: Sliced only needed trajectory columns in `CandleTrajectoryExtractor.extract_sequence()` before `.copy().ffill().bfill()` and iterated NumPy arrays via `zip(..., strict=True)` instead of `iterrows()`; skipped SQLite initialization and disk I/O in `JevDecisionEngine` when `mode in ("native", "backtest")` unless `use_disk_cache=True` (`mode="external"` keeps SQLite cache active by default).
  - `execution/entry_pipeline.py`: Filled `long_arr` / `short_arr` / `tag_arr` directly in NumPy in `EntryPipeline.run()` instead of per-hit `Series.iloc[pos]` mutations; switched `__init__` default fallback from `SensorSuite.hunter()` to `SensorSuite.full()`.
- **Dead sensor cleanup & Docker context reduction:**
  - Deleted unused experimental modules (`anomaly_volume_surge.py`, `anomaly_volatility.py`, `pattern_a80_confluence.py`, `pattern_swing_breakout.py`, `composite_catalog.py`), removed `WaterfallDropSensor` + `SensorSuite.hunter()` / `experimental()`, cleaned `signals/sensors/__init__.py` & unused `StrategyMTFSniper._dataframe_position()`, and updated unit tests (preserving `CompositeSensor` & SQLite concurrency suite with `use_disk_cache=True`).
  - Excluded `.gitnexus/` (232 MB), `reports/`, `.agents/`, `.claude/`, `.cursor/`, and `memory-bank/` in `.dockerignore`.
- **Verification (post-optimization):**
  - `.venv/bin/python scripts/lint_guardrails.py --verify-all`: **0 violations**, **402 passed, 0 skipped** (`4.23s`).
  - Exact 7Y Fixed-100 Backtest Parity (`20200101–20261124`, `JEV_MODE=native`, `MTF_RUNTIME_MODE=deterministic`, `--stake-amount 100`, `--cache none`, `4:29.27 total` wall / `~11.4s` data+entry signal generation): **435/435 identical trades** (`254L/181S`, `243W/192L`, **55.9% WR**), **+2,336.481 USDT (+233.65%)**, **PF 1.84**, **Wallet Max DD 13.13% (`185.409 USDT`)**, Sharpe `1.20`, Calmar `13.52`.

**Dormant JevHunter Stack Removed (`v4.7.0-1w-regime` cleanup, Nov 26, 2026)**:
- **Removed unused Hunter escalation modules (`~2,533` lines code + tests):** Deleted `intelligence/jev_entry_hunter.py`, `intelligence/hunter_escalation.py`, `intelligence/hunter_adapter.py`, `intelligence/post_hunter_gates.py`, `directional/long/hunter_gates.py`, `directional/short/hunter_gates.py`, `directional/policy_escalation.py`, `scoring/`, and 4 Hunter unit test files (plus migrated `TestRealSqliteConcurrency` in `test_jev_architecture.py` onto `JevDecisionEngine`).
- **Preserved live edge & configs:** Kept `MomentumRadar` (`signals/momentum_radar.py`) driving live `WaterfallMomentumSensor`, kept `ShortSetup.WATERFALL_COLLAPSE` (`jev_hunter_waterfall_collapse`) & `JevDecisionEngine` (`user_data/jev_hunter_cache.sqlite` default path) untouched, moved Waterfall threshold defaults (`0.285`, `0.35`, `0.035`) cleanly into `WaterfallFilterConfig` / `ShortPolicyConfig`, and removed stale `enable_jev_hunter` / `enable_hunter_long` runtime switches from Python & Docker Compose while tolerating legacy host JSON keys harmlessly.
- **Verification (post-cleanup):**
  - `.venv/bin/python scripts/lint_guardrails.py --verify-all`: **0 violations**, **419 passed, 0 skipped** (`Ruff` + `Pyrefly` + AST + unit tests).
  - Exact 7Y Fixed-100 Backtest Parity (`20200101–20261124`, `JEV_MODE=native`, `MTF_RUNTIME_MODE=deterministic`, `--stake-amount 100`, `--cache none`): **435/435 identical trades** (`254L/181S`, `243W/192L`, **55.9% WR**), **+2,336.481 USDT (+233.65%)**, **PF 1.84**, **Wallet Max DD 13.13% (`185.409 USDT`)**, Sharpe `1.20`, Calmar `13.52`.

**v4.7.0-1w-regime — Opt-in #2 Activated on Dry-Run (`jev_1w_bear_penalty=0.35`, Nov 26, 2026)**:
- **Weekly MTF & Context Sensor (`market/indicators.py`, `signals/sensors/market_regime.py`)**:
  - Added `@informative("1w")` (`populate_indicators_1w`) computing core EMA/MACD/RSI/ADX/BB-width suite + weekly `market_regime` (`unknown`, `bull_trending`, `chop`, `bear_relief`, `cycle_top`, `bear_trending`).
  - Added `SensorType.CONTEXT`, `TriggerEvent.side: TradeSide | None`, and `MarketRegimeSensor` (`BaseSensor`) emitting bilateral `1W` macro facts (`regime_1w`, `is_bear_wave_1w`, `macdhist_1w`, `adx_1w`, `rsi_1w`) in both `SensorSuite.live()` and `SensorSuite.full()`.
  - Unified enrichment along the single pipeline path: `SetupComposer.compose_suite()` (`_attach_suite_context` fast path) and `CandidateBuilder.process()` (`_attach_context_features` event fallback) attach identical context onto `TradeCandidate.raw_features` and `attrs["mtf_sniper_accepted_candidates"]` without triggering standalone candidates or altering `confluence_count`.
- **JEV Continuous Scoring & Counter-1W Sizing (`intelligence/jev_decision.py`, `risk/position_sizing.py`, `core/config_resolve.py`)**:
  - `CandleTrajectoryExtractor.extract_macro_context()` feeds `1W` context into `JevDecisionEngine._evaluate_native_sequence()` (which also drives `external` hybrid `native_conf`) and isolates SQLite cache tokens (`_cache_threshold_token()`).
  - Added opt-in knobs in `StrategySwitchConfig`: `jev_1w_bear_penalty` (default `0.0`, enabled at **`0.35`** in `docker/config.mtf_sniper.json` & `docker-compose.yml` for Pi dry-run) and `counter_1w_stake_scale` (default `None`). Code/config defaults stay parity-safe when unset.
  - Added opt-in #1 previously in `61b36b2b6`: `risk_per_trade_pct` (default `None`) strict budget ceiling & `min_stake` rejection guard, plus Docker base image fix `23bb98502` (`freqtradeorg/freqtrade:stable`).
- **Walk-Forward A/B Evidence (`20200101–20261124`, `JEV_MODE=native`, `1w` futures downloaded)**:
  - Parity (`jev_1w_bear_penalty=0.0`, `counter_1w_stake_scale=None`): 5Y `334/334` exact (`+184,121.66 USDT`, `14.42% Abs DD`), 7Y Fixed-100 `456/456` exact (`+2,151.14 USDT`, PF `1.71`).
  - `jev_1w_bear_penalty=0.35` Fixed-100: `435` trades, **55.9% WR**, **+2,336.48 USDT**, PF **`1.84`**, SQN **`3.86`** — monotonically improved across all 3 windows (`W1: +330.3 -> +465.7`, `W2: +604.8 -> +639.8`, `W3: +1216.0 -> +1230.9`; 2021 cut `-99.7 -> -47.2`).
  - `jev_1w_bear_penalty=0.35` + `Risk 4%` + `B65=0.50x`: `435` trades, **+17,027.35 USDT** (vs `+12,978.63`), **Max Rel DD 22.82%** (vs `24.77%`), Calmar **100.2**.
- **Verification**: `.venv/bin/python scripts/lint_guardrails.py --verify-all` → **0 violations**, **466 passed, 1 skipped** (`Ruff` + `Pyrefly` + AST + unit tests).

**Single-Truth Architecture Migration + Sub-System Hard-Lock (Nov 25, 2026)**:
- **Unified Single-Truth Execution (`signals/` -> `directional/setup_composer.py` -> `execution/entry_pipeline.py`)**:
  - Moved DataFrame readiness & finite-numeric helpers into pure Market layer (`strategies/mtf_sniper/market/data_guards.py`), imported by `directional/common_gates.py` and sensors (`LINT008`/`LINT020` clean).
  - Added vectorized mask protocol on `BaseSensor` (`scan_mask()`, `scan_mask_features()`), implemented on `LegacySniperPartSensor` and new `WaterfallMomentumSensor` (`waterfall_momentum`, registered in `SETUP_WATERFALL_SHORT`, `SensorSuite.live(strat, include_waterfall=enable_waterfall)`, and `SensorSuite.full()`).
  - Added `SetupComposer.compose_suite()` for zero-loop NumPy recipe intersection (`SUBSYSTEM_REGISTRY`) with opposite-side Sniper conflict cancellation and automatic `TriggerEvent -> CandidateBuilder -> compose` fallback when any custom sensor returns `scan_mask() is None`.
  - Removed legacy `_candidates_from_sniper_masks` / `_candidates_for_mask` fast-path bypass from `execution/entry_pipeline.py`; locked cleanly via `LINT029` (plus `LINT020`–`LINT028` + `.venv/bin/python scripts/lint_guardrails.py --verify-all` in pre-commit and `Dockerfile.custom`).
  - Verified **100.00% exact 5Y Backtest parity** (`20220101–20261124`, `JEV_MODE=native`, `MTF_RUNTIME_MODE=deterministic`, `--cache none`): **334/334 identical trades**, **+184,121.6625 USDT (+18,412.17%)**, **14.42% wallet Max DD**, **PF 2.56**, **Sharpe 1.57**, **Calmar 1365.48**.

**v4.6.0-counter1d** — System Review Hardening, Counter-1D Sizing (`0.50x`), Standardized Config & Live Exchange Sync (Nov 25, 2026):
- **Portfolio Protection (`risk/portfolio_protection.py`)**:
  - Normalized naive UTC `close_date` vs UTC-aware `current_time` (`_to_utc_timestamp` / `close_date_utc`) so circuit-breaker pause windows never shift by local OS timezone.
  - Included backtest `LocalTrade` rows with `strategy == ""` in `filter_strategy_trades` (keeping `strategy=None` and foreign strategy names excluded) to restore backtest–live parity.
  - Fixed Circuit Breaker design flaws (`_compute_pause_until`, `_is_correlated_loss`): reset streak counter after triggering a pause window (preventing 1-loss cascading re-bans) and deduplicated simultaneous cross-pair entries on the same side (`<= 1h` open window).
- **Entry Pipeline & Waterfall Policy (`execution/entry_pipeline.py`, `directional/short/policy.py`, `directional/*/setups.py`)**:
  - Wired strategy policy parameters into `EntryPipeline.live` (`filter_wick_rejection`, `max_rejection_wick_ratio`, `max_close_pos_ratio_*`, RSI/EMA distance caps, PDH filter).
  - Moved `_remember_accepted_candidates` & `mtf_sniper_accepted_candidates` filtering after `apply_cooldown` and Long/Short conflict cancellation so suppressed bars never overwrite active `candidate_id`s in `CandidateRegistry`.
  - Fixed Waterfall scoring paradox in `ShortPolicy.evaluate` (`base = max(0.70, raw_base)` when `short_score > 0`) so passing waterfall collapses are not rejected below missing-score (`0 -> 0.85`).
  - Converted `tag_short_sniper` / `tag_long_sniper` to positional numpy indexing (safe under duplicate DataFrame index labels).
- **JEV Fail Policy, Telemetry & Ops Hardening (`strategy_mtf_sniper.py`, `decision_adapter.py`, `jev_decision.py`, `risk_engine.py`, `telemetry/`, `docker-compose.yml`)**:
  - Propagated `jev_fail_policy` from `StrategyMTFSniper` through `JevDecisionAdapter` to `JevDecisionEngine` and aligned `"safe"` fail-closed semantics.
  - Replaced `"ETH" in pair` substring matching with base-asset check (`ETH` only, preventing false match on `ETHFI`, etc.).
  - Enriched `RiskEngine.authorize_entry` with structured AI verdict telemetry (`ai_verdict`, `ai_confidence`, `ai_model_version`, `market_regime`, `policy_score`), real `proposed_stake` & `wallet_equity` from `confirm_trade_entry`, and deduplicated repeated reject bursts within the same 15m candle.
  - Bounded `DecisionLedger` / `TradeLedger` RAM buffers (`deque(maxlen=2000)`) and added 10 MiB JSONL rotation (`telemetry/jsonl_io.py`); lowered default `JEV_API_TIMEOUT` in `docker-compose.yml` from `10`s to `4`s to avoid blocking the 5s throttle loop.
- **Dead/Bad Code Cleanup (Nov 25, 2026)**:
  - Removed dead `evaluate_custom_exit()` from `execution/exit_policy.py`, `execution/__init__.py`, and `tests/strategy/test_mtf_sniper_exit_policy.py` (runtime uses `risk/stoploss_ladder.py` + `exit_policy.confirm_exit_allowed`).
  - Removed 3 unused `RejectionReasonCode` enum members (`AI_VETO_CHOPPY_REGIME`, `AI_FAIL_SAFE_REJECT`, `RISK_INSUFFICIENT_LIQUIDATION_BUFFER`) from `core/enums.py`.
  - Extracted `MomentumRadar` out of dormant `intelligence/jev_entry_hunter.py` into `signals/momentum_radar.py` (with compatibility re-export kept in `jev_entry_hunter.py`), decoupling `execution/entry_pipeline.py` and `intelligence/hunter_adapter.py`.
  - Fixed `E402`, `F841`, `I001`, `PTH`, and silent-except `S112` lint debt in `intelligence/jev_decision.py` / `jev_entry_hunter.py` and removed `"PTH"`, `"E402"`, `"F841"` from `pyproject.toml` per-file-ignores.
  - Added unit tests for `core/config_resolve.py` in `tests/strategy/test_mtf_config_resolve.py`.
- **1D Counter-Trend Stake Scaling & Config Standardization (Nov 25, 2026)**:
  - Implemented `COUNTER_1D_TREND_STAKE_SCALE = 0.50` via `resolve_counter_1d_scale` in `strategies/mtf_sniper/risk/position_sizing.py`, exposed `counter_1d_stake_scale` (`DecimalParameter(0.25, 1.00, default=0.50, decimals=2, space="buy", optimize=True)` + `strategy_mtf_sniper.json`), plus centralized `resolve_strategy_switches`, `merge_pair_overrides`, `tag_multipliers`, `default_leverage`, and expanded `EffectiveConfig` (`enable_waterfall`, `filter_params`, `sizing_params`) in `core/config_resolve.py` (hyperopt-safe: `strategy_params.counter_1d_stake_scale` does not clobber hyperopt trials).
- **Fail-Closed & Data-Integrity Hardening (Nov 25, 2026)**:
  - Fixed `NaN`/`Inf` fail-open bypasses in `ShortPolicy` & `LongPolicy` (`directional/common_gates.py`: `has_invalid_numeric`, `read_finite_float`, `finite_series_mask`) and guarded `close <= 0` division.
  - Gated `MomentumRadar` waterfall scan in `EntryPipeline` by `data_ready_mask` + finite waterfall indicators, and deduplicated same-bar `accepted` entries per `(side, pos)` so duplicate bars never overwrite `CandidateRegistry`.
  - Fixed fake vacuum classification in `CandleTrajectoryExtractor.extract_macro_context` & `JevDecisionEngine._evaluate_native_sequence` via `floor_20d_valid` when `swing_low_20d_1d` is missing/`NaN`/`<= 0`.
  - Hardened `_compute_pause_until` & `distress_guard_blocks` in `risk/portfolio_protection.py` to skip `None`/`NaN` `close_profit` or non-finite prices instead of resetting streak or evaluating `NaN`.
- **Live Exchange Position Auto-Sync (Nov 25, 2026)**:
  - Added `bot_loop_start()` -> `_sync_live_exchange_positions()` in `strategies/mtf_sniper/strategy_mtf_sniper.py` controlled by `exchange_sync_interval_secs` (`15s` default in `config_resolve.py`, `user_data/config.json`, `docker/config.mtf_sniper.json`). When running `live` (`dry_run=False`) with at least one filled open position, refreshes `self.wallets.update(require_update=True)` every 15s before `manage_open_orders` / `exit_positions` so manual exchange closes trigger `handle_onexchange_order` immediately instead of waiting for Freqtrade's 3600s cache TTL.
- **Verification (Nov 25, 2026)**:
  - `pytest -q tests/strategy/`: **444 passed, 1 skipped**; `scripts/lint_guardrails.py`: **0 violations**; `ruff check`: clean.
  - **Honest Live-Parity Backtest (`20220101-20261124`, `JEV_MODE=native`, `MTF_RUNTIME_MODE=deterministic`, cache=none)**:
    - 334 trades (**55.1% WR**: 184W/150L), **+184,121.66 USDT (+18,412.17%)**, **14.42% Max DD (wallet)** (down from 19.20%), **PF 2.56**, **Sharpe 1.57**, **Sortino 1.96**, **Calmar 1365.48**, **SQN 2.67**.
  - Earlier baseline (`20220101-20261101`, `JEV_MODE=native`, `MTF_RUNTIME_MODE=deterministic`, cache=none):
    - 330 trades (**55.2% WR**: 182W/148L), **+149,375.82 USDT (+14,937.58%)**, **14.77% Max DD (wallet)** / 21.35% closed-trade DD, PF **2.03**, Sharpe **1.45**, Sortino **1.81**, Calmar **1095.64**.
    - Circuit Breaker fired **7** times cleanly (eliminating cascading 1-loss re-bans & BTC+ETH double-counting that had cut PnL to +70,230 USDT on 323 trades), outperforming the CB-disabled `v4.5.3` run (+142,778 USDT / 54.9% WR) with identical 14.77% wallet Max DD and true live parity.

**v4.5.3-standardized-waterfall-vacuum** — Quantitative Standardization, Reviewer Alignment & Guardrail Hardening:
- **Reviewer Feedback Addressed & Fully Resolved (Nov 24, 2026):**
  1. *Climax Threshold Spec Justification & Boundary Testing*:
     - `WaterfallFilterConfig.capitulation_climax_dist_ema50_4h = -10.0%` and `capitulation_climax_dist_ema50_1d = -20.0%`.
     - *Quantitative Rationale*: Under genuine Vacuum Breakdown (`floor_dist <= 0`), price breaks a 20-day structural floor. The lagging 4H EMA50 (200 hours / 8.3 days) naturally expands to -7% to -9% during free-fall momentum. A strict -6% cap chokes high-momentum continuation (such as the Nov 20, 2026 BTC crash where price dropped 5% on chart).
     - *Unit Test Guardrail Added*: `test_waterfall_vacuum_deep_runway_accepted` asserts -8.0% 4H / -18.0% 1D is accepted, while `test_waterfall_climax_overextended_rejected` asserts -10.5% 4H / -20.6% 1D is rejected. Both sides of the boundary are rigorously tested.
  2. *Stoploss Ladder Unit Test Coverage*:
     - Added comprehensive unit tests in `TestStoplossLadder` (`tests/strategy/test_mtf_sniper_risk.py`) locking each exact step for both long and short positions:
       - `< 18% ROE -> None`
       - `18% - 30% -> lock +1%`
       - `30% - 50% -> lock +10%`
       - `50% - 80% -> lock +25%`
       - `80% - 120% -> trail -0.25`
       - `>= 120% -> trail -0.20`
  3. *Decision Gate Config & Setup Contract Alignment*:
     - JEV Decision Gate now strictly matches setup using `ShortSetup.WATERFALL_COLLAPSE.value` (and canonical aliases).
     - Dynamically pulls `vacuum_floor_dist_max` and `vacuum_min_vol_ratio` from `WaterfallFilterConfig` (`DEFAULT_WATERFALL_FILTER` / `DEFAULT_ETH_WATERFALL_FILTER`) instead of relying on hardcoded defaults.
  4. *ATR 90 Boundary Condition Asymmetry Fixed*:
     - Policy rejected when `atr_pct > 90.0`, while Decision penalized when `atr_pct >= 90.0`.
     - Decision updated to `atr_pct > wf_cfg.max_atr_pct` (strictly `> 90.0`), aligning the boundary condition on both sides.
  5. *Dual Guardrail Preservation Under Vacuum*:
     - **ETH 4H EMA50 Guard**: Even during vacuum breakdown (clean floor breakdown or near-floor buffer with surge), ETH entries are strictly rejected if price is above 4H EMA50 (`require_below_ema50_4h and dist_ema50_4h > 0.0`).
     - **1H Counter-Trend Wave Trap**: Decoupled from floor distance trap. In Decision Gate, `dist_ema50_1h > 0.0 and rsi_1h > 50.0` is NEVER waived (penalizes -0.10 regardless of vacuum). Only the floor distance trap (`floor_dist < 8.0`) is waived on confirmed vacuum.
  6. *Honest Window-Separated Empirical Backtest Evidence*:
     - **In-Sample Baseline (`20220101-20261101`, 1,765 days, cache=none)**:
       - Trades: 335 | Win Rate: 54.9% (184W / 151L)
       - Total Profit: **+$142,778.07 USDT (+14,277.81%)** (unlimited compounding, initial wallet 1,000 USDT)
       - Max Drawdown (wallet balance): **14.77%** ($19,720.09 USDT) — *Significantly safer than -6% cap (which suffered 21.62% DD)*
       - Daily Wallet Sharpe: **1.44** | Sortino: **1.80** | Calmar: **1047.17**
     - **November 2026 Window (`20261101-20261124`, 24 days, cache=none)**:
       - Trades: 3 | Win Rate: 66.7% (2W / 1L)
       - Total Profit: **+$350.46 USDT (+35.05%)** (initial wallet 1,000 USDT)
       - Max Drawdown: **9.48%** ($94.78 USDT)
       - Captured Nov 20, 2026 BTC crash (+5% drop on chart, locking +25.00% ROE / +108.03 USDT).
- **Quality & Guardrails Gate:**
  - `pytest tests/strategy/`: 421 passed, 1 skipped.
  - Ruff lint clean (0 violations).
  - Architecture guardrails clean (0 violations).

## Evidence (2026-11-19: Waterfall Evolution 5Y 20220101-20261116)
| Setup | Trades | PnL % | Profit abs (USDT) | Max DD | Calmar | Sharpe | Winrate |
|---|---:|---:|---:|---:|---:|---:|---:|
| **ETH 4H Regime Guard (v4.4.2 Production)** | **344** | **+7741.29%** | **77,412.93** | **18.89%** | **440.14** | **1.35** | **57.80%** |
| Dual-Regime Waterfall Baseline (v4.4.1) | 354 | +5599.18% | 55,991.80 | 18.89% | 318.39 | 1.28 | 57.60% |
| Pre-Waterfall Baseline | 291 | +5269.08% | 52,690.77 | 18.89% | 299.14 | 1.25 | 58.80% |
| Naive Waterfall (Unfiltered) | 370 | +1390.04% | 13,900.35 | 35.88% | 48.61 | 0.88 | 52.40% |
| Decision OFF (pure) | 320 | +5096.55% | 50,965.50 | 18.89% | 1.670 | 59.10% |
| Hybrid Baseline (w=0.10, cp=0.05) | 302 | +4074.85% | 40,748.53 | 18.89% | 1.722 | 58.94% |
| Native Decision (Ep46) | 299 | +3866.60% | 38,666.00 | 18.89% | 1.710 | 58.50% |

**Key findings & Rescue Analysis:**
- **Candidate A Rescued:** 2024-01-20 01:00 ETH SHORT (+107.34%, +$346.21 USDT) successfully admitted via High-Runway Confluence (`floor_dist >= 15%`, `dist_ema50_1h < 0`, `rsi_1h < 50` -> +0.10 native bonus -> hybrid score 0.74 >= 0.70 CONFIRM).
- **Candidate B Vetoed:** 2025-11-24 09:15 ETH SHORT (-27.95% crash, -$3,605.85 USDT) penalized and rejected (`floor_dist < 8%`, `dist_ema50_1h > 0`, `rsi_1h > 50` -> -0.10 penalty -> hybrid score 0.54 < 0.70 VETO).
- **Beats Decision OFF:** Total net profit reached **+$52,690.77 USDT (+5,269.08%)**, exceeding Decision OFF by **+$1,725.29 USDT (+172.5 pp)** while maintaining the exact same **Max DD of 18.89%**!
- Top-grade native signals (0.85+) cannot be vetoed by AI, and poor signals (0.50) cannot be forced through.
- Fail-safe fallback to native heuristics preserved (`fallback_native`).
```
EntryPipeline.live:
  vectorized part AND (fast) → Policy setup-only ACCEPT
  (event scan fallback if suite ≠ part sensors)
→ strategy cooldown → enter_*
Hunter/anomaly OFF on live; Decision Gate optional on confirm.
```

## Evidence (2026-11-18)
- **Shipped HO Ep46** long params (body/dist stay pair-override 0.63/3.3)
- 5Y Decision ON: 299 trades, **+3867%**, **DD 18.9%**
- 5Y Decision OFF: 320 trades, **+5097%**, **DD 18.9%**
- Prior defaults: ON +3463%/DD25.4% · OFF +4858%/DD32.7%
- Does **not** reproduce report Run1 (+10,854%) — Hunter frozen path removed by design

## Versions
- Single hub: `strategies/mtf_sniper/__init__.py`
- `STRATEGY_VERSION` `4.6.0-counter1d` / Decision engine `2.7.2` / Hunter `2.7.0`
- External prompt A/B via `JEV_DECISION_PROMPT_VARIANT` (default `richctx`); cache identity per suffix

## External Decision prompt A/B (2026-11-19, 5Y `20220101-20261116`)
Pareto required: PnL ≥ max(OFF, native), DD ≤ OFF+0.5pp, confirm_rate ∈ [55%, 90%].
| Variant | Trades | PnL | DD | PF | Confirm rate | Pareto |
|---|---:|---:|---:|---:|---:|---|
| OFF | 320 | +5096.6% | 18.89% | 1.67 | — | baseline |
| Native | 299 | +3866.6% | 18.89% | 1.71 | — | baseline |
| richctx | 316 | **+5481%** | 18.89% | 1.74 | 98.8% | no (no bite) |
| richctx-calibrated | 273 | +2594% | 18.88% | 1.76 | **83.7%** | no (PnL) |
| richctx-selective | 312 | +5441% | 18.89% | 1.75 | 97.5% | no |
| richctx-asymmetric | 309 | +3101% | 18.89% | 1.60 | 95.7% | no |
- **Decision: NO_SHIP** external as default. Artifacts: `user_data/telemetry/prompt_ab_*.json`, scorecard `score_decision_prompt_ab.py`
- Closest with bite: `richctx-calibrated`. Best experimental PnL: `richctx` (passthrough).

## Cleanup done
- Removed `strategies/compare_old/` (temp BT mirror)
- Removed dead `sniper_hardpass`, `pattern_sniper_mask`
- Slimmed legacy mask aliases; tests aligned to setup recipes

## Defaults
- `enable_jev_hunter=False` (matches live pipeline)
- Version hub: `strategies/mtf_sniper/__init__.py`

## Deploy (Coolify / Docker Compose)
- Image bakes `strategies/mtf_sniper` + `docker/config.mtf_sniper.json` overlay
- **Docker Production Profile (Intentional):** `mtf_runtime_mode: decision_external`, `JEV_MODE=external`, `ENABLE_WATERFALL=true`, `ENABLE_DYNAMIC_SIZING=true`
- `JEV_CONFIRM_THRESHOLD=0.70`, `JEV_FAIL_POLICY=fallback_native` (Hybrid AI score with native heuristic fallback)
- `JEV_DECISION_WEIGHT_AI=0.10`, `JEV_DECISION_CHOPPY_PENALTY=0.05`
- Local backtests stay deterministic (`JEV_MODE=native` / `MTF_RUNTIME_MODE=deterministic`)
- Compose command: dual `--config` + `--strategy-path …/mtf_sniper`
- Mounted host config may still be stale; overlay forces correct path and runtime mode

## Stabilize progress
- RuntimeMode matrix + **fail-fast** on invalid mode (no silent fallback)
- Fast/event parity: Short **and** Long + fuller Policy semantic compare
- Candidate ID: Trade custom data persist (restart-safe) → registry cache → reconstruct → else `candidate_id_missing`
- No global env mutation (adapter, strategy, DecisionEngine dotenv)
- RiskAuthorization honesty + pair effective stoploss on confirm
- Callback integration tests (persist/restart, hard exits, pair SL)
- **Final P1 batch (post-b48aac8):** registry no future/stale fallback; absolute candle_index; persist bool+verify; validate-on-read; startup fail-fast via `_load_external_config`
- **Correlation hardening (post-2eb6607):** DF fallback max-age; stronger canonical parse + pair/side match; LocalTrade persist lifecycle tests
- Ep46 BT ON reconfirm on `2eb6607`: 299 trades, **+3866.6%**, **DD 18.89%**, PF 1.71
- Docs: `strategies/mtf_sniper/docs/STABILIZATION_DETERMINISTIC.md`

## Rejected opt (evidence 2026-11-18)
Feedback batch (tag multipliers / early BE ladder / ETH tighten) vs **wrong baseline** (pre-Ep46 DD25.4%/PF2.18).
- `#1` tag multipliers: inert (`enable_dynamic_sizing=False`); tag narrative ≠ Ep46 ENTER TAG
- `#2` ladder: defer isolated A/B only
- `#3` ETH tighten (`SL -0.22`, dist `0.9`, body `0.68`): **regressed** ON BT → +3197%/DD22.3%/PF1.65/ETH+131% vs Ep46 +3867%/DD18.9%/PF1.71/ETH+502% — **reverted**

## Hygiene
- Retargeted broken `user_data/strategies/*` symlinks away from deleted `strategies/active/`
- Removed non-strategy `jev_*.py` symlinks from that dir (tripped lookahead search)

## Docs / identity hardening (post-0666c1a / final)
- RiskAuth vs FT sizing callbacks documented separately (`leverage` / `custom_stake_amount`)
- Canonical ID: Integral index only (no float/bool/str); timestamp datetime/Timestamp only
- Registry fail-closed on invalid identity; lookup needs `current_time`
- DB persist expire/reload covered; full process/Docker restart still ops-only

## Final stabilization patch (on `pi-deploy`)
- P1: `CandidateRegistry.remember()` validates pair/side/ts/index before any strip/key write; outer except fail-closed; mismatch → canonical when fields valid
- P2: `_resolve_candidate_id` DF fallback uses `np.flatnonzero(mask)` absolute positions (safe under duplicate index)
- Follow-up: `_canonical_side` accepts only `TradeSide|str` (no `str()` coerce); decisive mid-duplicate DF test (`endswith("_2")`)
- Verification: related suites green after follow-up (59 on identity cluster)
- Fast Path / Event Path / Policy / sizing untouched

## Entry pipeline positional fix
- Root cause: `candle_index` is iloc, but `run` / `apply_entry_pipeline` / `_summarize_live_entries` wrote via index labels → duplicate labels multi-marked
- Fix: `.iloc` / numpy mask writes; cooldown already positional (unchanged logic)
- Regression: `test_mtf_entry_pipeline_duplicate_index.py`

## Next
- Coolify redeploy when ready (`decision_external` + Hybrid Decision Score with `fallback_native`, `enable_waterfall=true`, `enable_dynamic_sizing=true`)
- Ops gap remains: live/Docker restart with open trade
- Keep local backtests on `JEV_MODE=native` / `MTF_RUNTIME_MODE=deterministic` for reproducibility
- Hunter / Momentum Radar / AI-as-score-feature still deferred on live entry pipeline
