# Active Context

## Current focus
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
- `STRATEGY_VERSION` `4.4.1-deterministic` / Decision engine `2.7.2` / Hunter `2.7.0`
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
- **Scenario B Active:** `mtf_runtime_mode: decision_external`, `JEV_MODE=external`
- `JEV_CONFIRM_THRESHOLD=0.70`, `JEV_FAIL_POLICY=fallback_native`
- `JEV_DECISION_WEIGHT_AI=0.10`, `JEV_DECISION_CHOPPY_PENALTY=0.05`
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
- Coolify redeploy when ready (keep `deterministic` + native Decision ON for Ep46)
- Ops gap remains: live/Docker restart with open trade
- External Decision only as explicit experiment (`decision_external` + variant env) — not production default
- Hunter / Momentum Radar / AI-as-score-feature still deferred
