# Progress

## What works
- Part sensors + SetupComposer recipes (`setup:sniper_short/long`)
- Vectorized AND fast path in `EntryPipeline` (BT-safe)
- Policy setup-only ACCEPT; live Hunter off
- **Dual-Regime Native Waterfall + ETH Pair Tuning**:
  - Regime A (Vacuum Breakdown `floor_dist <= 0`): Price breaks 20-day swing low, free-fall momentum permitted unless in capitulation selling climax (`dist_ema50_4h >= -6.0%`, `dist_ema50_1d >= -15.0%`).
  - Regime B (In-Range Breakdown `floor_dist > 0`): High-confidence momentum filters: not overextended (`dist_ema50_4h >= -3.5%`, `dist_ema50_1d >= -12.0%`), not macro bull (`dist_ema200_1d <= 5.0%`), 4H MACD histogram <= 35.0, 4H volatility expanding (`bb_width_4h >= 0.035`), high floor guard.
  - **Pair-Specific Filter Architecture**: Dynamic `WaterfallFilterConfig` per-pair overrides loaded via `custom_pair_params` in `StrategyMTFSniper`. ETH configured with `waterfall_require_below_ema50_4h = True` to eliminate false whipsaws above 4H EMA50.
- **BTC Nov 12, 2026 Crash Captured**: Entered at $72,417.20, exited at trailing stop for **+108.34% ROE** (+$15,660.71 USDT in 5Y compound).
- **5Y 2022-2026 Backtest Proven (`20220101-20261116`)**: 344 trades, **+77,412.93 USDT (+7,741.29%)**, **Max DD: 18.89%**, **WR: 57.8%**. Outperforms pre-waterfall baseline (+52,690 USDT) by **+$24,722.16 USDT (+2,472 pp)** with ZERO increase in Drawdown (18.89% flat)! Calmar surges to **440.14**! Sharpe: **1.35**, Sortino: **1.60**. Waterfall trades contributed **+$36,637.40 USDT nominal PnL**.
- **Architectural Guardrails Linter & Quality Standards**: Enforces AGENT OPERATING PRINCIPLES with automated checks (`scripts/lint_guardrails.py`, LINT001-LINT018) covering 100% English codebase (Rule 2), 99% No-Comment Policy (<5% density), anti-lookahead bias (`shift(-k)`), anti-silent-exception-swallowing, directional isolation, redundant type-cast prevention (`LINT014`), Meta Pyrefly strict type checking (`LINT015`), immutable data contracts (`LINT016`), Facade thinness (`LINT017`), and sensor OOP inheritance (`LINT018`).
- **Linter & VS Code Problems Tab Fully Clean**: Zero errors across Ruff (`.venv/bin/ruff check`), Pyrefly (`pyrefly check`), Pyright, and Python compiler. `scripts/lint_guardrails.py` refactored to modular architecture (McCabe complexity <= 12, line length <= 100). All 414 strategy unit tests passing.
- Version sync: package/strategy `4.4.1-deterministic`; Decision engine `2.7.2` (richctx + prompt variants); Hunter `2.7.0`
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
- External Decision: no Pareto-feasible prompt on 5Y (richctx best PnL but ~99% confirm; calibrated has bite but −PnL) — do not default `decision_external`

## BT evidence (wallet 1000, hunter off, commit `504bb31`, timerange `20220101-20261116`)
- Live Decision ON: 204 trades, +3462.93% (+34,629 USDT), WR 60.8%, DD 25.43%, PF 2.18
- Decision OFF: 224 trades, +4858.45% (+48,584 USDT), WR 61.6%, DD 32.72%, PF 2.10
- vs report Run1 (+10,854% / DD 21.95%): not matched — that run used frozen Hunter cache
- vs report Run2 (+1,966% / DD 30.9%): new Decision ON beats PnL; DD similar band
- Nov 12 waterfall trade absent (Hunter off); Nov 5 short_B65plus still captured

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
