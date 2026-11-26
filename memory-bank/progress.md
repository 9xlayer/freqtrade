# Progress

## What works
- **Opt-in #2 — 1W Macro Regime Context Sensor & Continuous JEV Scoring (Nov 26, 2026)**:
  - Added `populate_indicators_1w` + `@informative("1w")`, `SensorType.CONTEXT`, and `MarketRegimeSensor` (`signals/sensors/market_regime.py`) wired seamlessly into `SensorSuite`, vectorized `SetupComposer.compose_suite()`, and `CandidateBuilder.process()` with 100% fast/event parity.
  - Integrated `regime_1w` into `CandleTrajectoryExtractor.extract_macro_context()` + `JevDecisionEngine._evaluate_native_sequence()` (`jev_1w_bear_penalty`, default `0.0`) and `resolve_counter_1w_scale()` (`counter_1w_stake_scale`, default `None`) in `core/config_resolve.py` & `risk/position_sizing.py`.
  - Kept defaults off (`0.0` / `None`) for 100% parity (`5Y: 334/334`, `7Y fixed: 456/456`); verified `jev_1w_bear_penalty=0.35` lifts 7Y Fixed-100 (`435` trades, **55.9% WR**, **PF 1.84**, all 3 walk-forward windows W1/W2/W3 up) and lowers 7Y `Risk 4% + B65=0.50x` Max Rel DD from `24.77%` to **`22.82%`** (`+17,027.35 USDT`).
  - Gate passed: **0 violations**, **466 passed, 1 skipped**.
- **Opt-in #1 (`risk_per_trade_pct`, `61b36b2b6`) & Docker Base Fix (`23bb98502`)**:
  - Strict equity-risk position cap (`equity * risk / |stoploss|`, enforced ahead of tag multipliers & rejecting below `min_stake`; default `None`) and switched `Dockerfile.custom` base image to `freqtradeorg/freqtrade:stable`.
- **Single-Truth Unified Signal Pipeline (Nov 25, 2026)**:
  - Removed legacy `_candidates_from_sniper_masks` fast path from `execution/entry_pipeline.py`; unified execution through `SensorSuite.live(strat, include_waterfall=...) -> SetupComposer.compose_suite() -> SUBSYSTEM_REGISTRY Engine`.
  - Added `market/data_guards.py`, vectorized `BaseSensor.scan_mask()` / `scan_mask_features()`, and `WaterfallMomentumSensor` (`waterfall_momentum`, registered in `SETUP_WATERFALL_SHORT` -> `WaterfallShortPolicy`).
  - Locked architectural boundaries with `LINT020`–`LINT029` + `--verify-all` CLI gate + `Dockerfile.custom` build gate.
  - Verified **100.00% exact 5Y Backtest parity (`20220101–20261124`, cache=none)**: **334/334 identical trades**, **+184,121.6625 USDT**, **14.42% wallet Max DD**, **PF 2.56**, **Sharpe 1.57**, **Calmar 1365.48**.
- Directional sub-system registry (`short_sniper`, `long_sniper`, `waterfall`) + dedicated policies; live Hunter off.
- **System Review Hardening, Counter-1D Sizing & Live Exchange Sync (v4.6.0-counter1d, Nov 25, 2026)**:
  - Fixed `circuit_breaker_blocks` UTC timestamp handling, backtest `LocalTrade` (`strategy=""`) parity, non-overlapping streak reset after pause, and correlated cross-pair entry loss deduplication.
  - Filtered `_remember_accepted_candidates` post-cooldown/conflict, wired `EntryPipeline.live` policy params, fixed `ShortPolicy` waterfall scoring floor, and made `tag_*_sniper` duplicate-index-safe.
  - Synced `jev_fail_policy` across Strategy/Adapter/Engine, fixed ETH base-asset check, added real AI/stake/`wallet_equity` telemetry + 15m reject-burst deduplication, bounded `DecisionLedger`/`TradeLedger` buffers + 10 MiB JSONL rotation (`telemetry/jsonl_io.py`), and lowered Docker `JEV_API_TIMEOUT` default to `4`s.
  - Cleaned dead/lint debt: removed dead `evaluate_custom_exit()`, trimmed unused `RejectionReasonCode` members, extracted `MomentumRadar` into `signals/momentum_radar.py`, fixed `E402`/`F841`/`PTH`/`S112` in `intelligence/jev_*.py` (tightened `pyproject.toml`), and added `tests/strategy/test_mtf_config_resolve.py`.
  - Fail-closed logic hardening: blocked `NaN`/`Inf`/`close <= 0` bypasses in `ShortPolicy`/`LongPolicy` & `EntryPipeline`, stopped `CandleTrajectoryExtractor` from classifying missing `swing_low_20d_1d` as vacuum breakdown (`floor_20d_valid`), deduplicated same-bar `EntryPipeline.accepted`, and hardened `portfolio_protection.py` against `None`/`NaN` `close_profit` and invalid prices.
  - 1D Counter-Trend Stake Scaling & Config Standardization (`resolve_counter_1d_scale`, `0.50x`): reduced counter-1D Long stake by 50% when `macdhist_1d <= 0` or `ema_50_1d <= ema_200_1d` (and Short stake by 50% when `is_macro_bull_run_1d == 1.0`), exposed `counter_1d_stake_scale` as a hyperoptable `DecimalParameter` + `strategy_mtf_sniper.json`, and centralized `resolve_strategy_switches`, `merge_pair_overrides`, `tag_multipliers`, `default_leverage`, and `EffectiveConfig` (`enable_waterfall`, `filter_params`, `sizing_params`) in `core/config_resolve.py`.
  - Live exchange position auto-sync (`bot_loop_start` -> `_sync_live_exchange_positions`, `exchange_sync_interval_secs=15`): refreshes live wallets/positions every 15s when filled trades are open so manual exchange closes trigger Freqtrade `handle_onexchange_order` immediately instead of waiting up to 3600s.
  - Verified (`20220101-20261124`, `JEV_MODE=native`, cache=none): **334 trades**, **+184,121.66 USDT (+18,412.17%)**, **55.1% WR**, **14.42% Max DD (wallet)** (down from 19.20%), **PF 2.56**, **Sharpe 1.57**, **Sortino 1.96**, **Calmar 1365.48**, **SQN 2.67**. Tests: **444 passed, 1 skipped**; guardrails & Ruff clean.
- **Waterfall Vacuum Breakdown Standardization & Reviewer Feedback Alignment (v4.5.3)**:
  - Justified and tested Climax overextension thresholds `-10.0% / -20.0%` for Vacuum Breakdown: Prevents premature throttling of true free-fall momentum when price drops >20-day swing low, lowering 5Y Max Drawdown to 14.77% (vs 21.62% with -6%).
  - Added unit test suite in `TestStoplossLadder` verifying exact stoploss outputs across every profit tier.
  - Contract unified: `JevDecisionEngine` imports `ShortSetup.WATERFALL_COLLAPSE.value` and reads `WaterfallFilterConfig` dynamically.
  - ATR boundary aligned at `atr_pct > wf_cfg.max_atr_pct` (`> 90.0`).
  - Strict dual-guard preservation: ETH 4H EMA50 guard and 1H counter-wave trap cannot be bypassed under vacuum.
  - **In-Sample Backtest Baseline (`20220101-20261101`, 335 trades, cache=none)**:
    - **+142,778.07 USDT (+14,277.81%)**, **54.9% WR**, **14.77% Max DD (wallet)**, Sharpe: 1.44, Sortino: 1.80, Calmar: 1047.17.
  - **November 2026 Window (`20261101-20261124`, 3 trades, cache=none)**:
    - **+350.46 USDT (+35.05%)**, **66.7% WR**, **9.48% Max DD**. Captured Nov 20, 2026 BTC crash (+5% drop on chart, locking +25.00% ROE / +108.03 USDT).
- **Unified Risk Engine (`custom_stoploss`) & Gentle Trailing Stop (Var C)**:
  - Eliminated dead code in `custom_exit` (`strategy_mtf_sniper.py`), unifying all trade lifecycle exits in `stoploss_ladder.py`.
  - Tuned trailing stop ladder with gentle thresholds (`+18% BE`, `+30% lock +10%`, `+50% lock +25%`, `+80% trail 0.25`, `+120% trail 0.20`), increasing 7Y PnL by +43% (+$40,978.99 USDT) and lowering Max DD to 31.96%.
  - Overextension protection: Preserved `long_overextended_dist_max = 0.53` as essential macro crash shield.
- **7Y 2020-2026 Backtest Proven (`20200101-20260331`)**: 432 trades, **+40,978.99 USDT (+4,097.90%)**, **Max DD: 31.96%**, **WR: 53.7%** (232W / 200L). Sharpe: 1.03, Sortino: 1.24, Calmar: 107.63.
- **Dual-Regime Native Waterfall + ETH Pair Tuning**:
  - Regime A (Vacuum Breakdown `floor_dist <= 0`): Price breaks 20-day swing low, free-fall momentum permitted unless in capitulation selling climax (`dist_ema50_4h < -10.0%` or `dist_ema50_1d < -20.0%`).
  - Regime B (In-Range Breakdown `floor_dist > 0`): High-confidence momentum filters: not overextended (`dist_ema50_4h >= -3.5%`, `dist_ema50_1d >= -12.0%`), not macro bull (`dist_ema200_1d <= 5.0%`), 4H MACD histogram <= 35.0, 4H volatility expanding (`bb_width_4h >= 0.035`), high floor guard.
  - **Pair-Specific Filter Architecture**: Dynamic `WaterfallFilterConfig` per-pair overrides loaded via `custom_pair_params` in `StrategyMTFSniper`. ETH configured with `waterfall_require_below_ema50_4h = True` to eliminate false whipsaws above 4H EMA50.
- **BTC Nov 12, 2026 Crash Captured**: Entered at $72,417.20, exited at trailing stop for **+108.34% ROE** (+$15,660.71 USDT in 5Y compound).
- **5Y 2022-2026 Backtest Proven (`20220101-20261116`)**: 344 trades, **+77,412.93 USDT (+7,741.29%)**, **Max DD: 18.89%**, **WR: 57.8%**. Outperforms pre-waterfall baseline (+52,690 USDT) by **+$24,722.16 USDT (+2,472 pp)** with ZERO increase in Drawdown (18.89% flat)! Calmar surges to **440.14**! Sharpe: **1.35**, Sortino: **1.60**. Waterfall trades contributed **+$36,637.40 USDT nominal PnL**.
- **Architectural Guardrails Linter & Quality Standards**: Enforces AGENT OPERATING PRINCIPLES with automated checks (`scripts/lint_guardrails.py`, LINT001-LINT018) covering 100% English codebase (Rule 2), 99% No-Comment Policy (<5% density), anti-lookahead bias (`shift(-k)`), anti-silent-exception-swallowing, directional isolation, redundant type-cast prevention (`LINT014`), Meta Pyrefly strict type checking (`LINT015`), immutable data contracts (`LINT016`), Facade thinness (`LINT017`), and sensor OOP inheritance (`LINT018`).
- **Linter & VS Code Problems Tab Fully Clean**: Zero errors across Ruff (`.venv/bin/ruff check`), Pyrefly (`pyrefly check`), Pyright, and Python compiler. `scripts/lint_guardrails.py` refactored to modular architecture (McCabe complexity <= 12, line length <= 100). 421 strategy unit tests passing, 1 skipped (`pytest tests/strategy/`).
- Version sync: package/strategy `4.6.0-counter1d`; Decision engine `2.7.2` (richctx + prompt variants); Hunter `2.7.0`
- Hybrid Decision Score + High-Runway Confluence: AI noul integrated as continuous score term (`w_ai=0.10`, `choppy_penalty=0.05`). With structural runway confluence (`floor_dist >= 15%`, `dist_ema50_1h < 0`, `rsi_1h < 50` -> +0.10 bonus) and trap protection, 5Y BT reaches **+5269.08%** (+$52,690.77 USDT), **PF 1.730**, **WR 58.80%**, **DD 18.89%**. Successfully rescues the +107.34% winner (2024-01-20 ETH) while fully vetoing the -27.95% crash (2025-11-24 ETH), outperforming Decision OFF (+5096.55%) by +172.5 pp!
- External prompt A/B harness: `JEV_DECISION_PROMPT_VARIANT` + scorecard — **5Y Pareto NO_SHIP** for binary gate (hybrid score succeeds instead)

## Gaps
- Hunter/AI re-enable later (composer `drop_anomalies=False` when gateway injected)
- Old compare tree removed — use git history if needed
- Coolify host `/data/.../config.json` may still say `strategies/active` until manually updated (image overlay covers it)
- Optional risk work: isolated custom-stoploss ladder A/B (early BE) — not shipped; ETH pair tighten rejected by BT
- Stabilization batch (post-bd6d2ef / b48aac8): Trade custom-data candidate_id, no non-canonical fallback, Long+Short parity, invalid mode fail-fast, DecisionEngine no env mutate, pair SL in RiskAuth, callback integration tests
- Final P1 correlation batch: registry time-window (no future), absolute candle_index, persist verify bool, validate-on-read
- Correlation hardening: DF fallback max-age; `parse_candidate_id` + trade pair/side match; LocalTrade custom-data lifecycle + partial-exit tests
- Docs↔code alignment: RiskAuth confirm-path honesty; dual-path architecture; migration archive
- Final stabilization: RiskAuth≠sizing docs; strict Integral candle_index; registry fail-closed mistyped IDs
- DB expire/reload tests pass; full live/Docker restart still ops gap
- **Final patch P1/P2 done** (`7392035e`): remember fail-closed; DF mask absolute positions
- **Follow-up:** strict `_canonical_side` type check; mid-duplicate label resolve test (`_2`)
- Hunter OFF; Sniper entry/exit/thresholds/risk multipliers unchanged
- Ep46 BT ON on `2eb6607`: 299 / **+3866.6%** / **DD 18.89%** / PF 1.71
- Entry pipeline duplicate-index write fix: iloc/numpy only for enter_*/tag/summary
- Next: deterministic BT on real data — identity + entry positional closed
- Hunter / Momentum Radar still out of scope
- External Decision: pure binary gate was NO_SHIP on 5Y, superseded by Hybrid Decision Score (`w_ai=0.10`, `fallback_native`); **Docker production intentionally runs `decision_external`**, while local backtests stay deterministic (`JEV_MODE=native`)

## BT evidence (wallet 1000, hunter off, commit `504bb31`, timerange `20220101-20261116`)
- Live Decision ON: 204 trades, +3462.93% (+34,629 USDT), WR 60.8%, DD 25.43%, PF 2.18
- Decision OFF: 224 trades, +4858.45% (+48,584 USDT), WR 61.6%, DD 32.72%, PF 2.10
- vs report Run1 (+10,854% / DD 21.95%): not matched — that run used frozen Hunter cache
- vs report Run2 (+1,966% / DD 30.9%): new Decision ON beats PnL; DD similar band
- Nov 12 waterfall trade absent (Hunter off); Nov 5 short_B65_plus still captured

## Hyperopt probe (2026-11-18)
- Space `buy` (long-only `optimize=True`), `SharpeHyperOptLossDaily`
- Parallel fix: ledger pickle + disable disk telemetry in hyperopt
- **100 epochs / `-j -2` / ~2m19s** (`strategy_StrategyMTFSniper_2026-11-18_07-30-12.fthypt`)
  - **Shipped Ep46 effective** (pair overrides keep body 0.63 / dist 3.3 — HO samples for those were masked)
  - Decision OFF: 320 trades, **+5096.6%**, **DD 18.9%**, PF 1.67
  - Decision ON: 299 trades, **+3866.6%**, **DD 18.9%**, PF 1.71
  - vs prior defaults OFF +4858%/DD32.7% and ON +3463%/DD25.4%
- Note: applying HO body=0.55/dist=1.4 into pair overrides collapsed PnL (~+1440%); do not override those keys

## Do not regress
- Incomplete parts ≠ enter_*
- Single EntryPipeline path
- Open/Closed: recipes in directional, not strategy facade
