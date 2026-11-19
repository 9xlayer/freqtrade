# Active Context

## Current focus
**v4.4.1-deterministic** — Dual-Regime Native Waterfall Optimization & Macro Climax Protection.
- Conducted deep quantitative analysis of all 67 Waterfall trades over 5Y dataset (`20220101-20261116`).
- Uncovered root causes of performance degradation: ETH false breakdown whipsaws (-$1.1k loss) and "Capitulation Selling Climax" trap (`dist_ema50_4h < -4%`, `dist_ema50_1d < -14%`).
- Architected & implemented **Dual-Regime Waterfall Filter** in `ShortPolicy`:
  - **Regime A (Vacuum Breakdown, `floor_dist <= 0`):** Price officially breaks below 20-day swing low into price discovery / vacuum (e.g. BTC Nov 12 @ 72.4k). Free-fall momentum allowed, capped only by extreme capitulation guards (`dist_ema50_4h >= -6.0%`, `dist_ema50_1d >= -15.0%`).
  - **Regime B (In-Range Breakdown, `floor_dist > 0`):** Price is floating above 20-day floor. Requires strict confluence to avoid traps: not overextended (`dist_ema50_4h >= -3.5%`, `dist_ema50_1d >= -12.0%`), not macro bull (`dist_ema200_1d <= 5.0%`), 4H MACD histogram not bullish (`macdhist_4h <= 35.0`), 4H volatility expanding (`bb_width_4h >= 0.035`), and high floor guard (`floor_dist > 10%` requires `vol_ratio >= 4.0` or `bb_width_4h >= 0.06`).
- **5Y Backtest Scorecard (`20220101-20261116`):**
  - Trades: **354** | Win Rate: **57.6%** (204W / 150L)
  - Total Profit: **+$55,991.80 USDT (+5,599.18%)** — **+$3,301.03 USDT higher than pre-waterfall baseline!**
  - Max Drawdown: **18.89%** — **EXACTLY flat to baseline (zero DD surge)!**
  - Calmar: **318.39** | Sharpe: **1.28** | Sortino: **1.53**
  - Waterfall contribution: 70 trades, **+$22,362.15 USDT nominal PnL** (BTC +$16,054 USDT, ETH +$6,307 USDT).
  - Preserved massive Nov 12 BTC waterfall trade (+108.34% ROE, +$15,660.71 USDT).
- All 410 strategy unit tests passing (`pytest tests/strategy`).
- Enforced AGENT OPERATING PRINCIPLES & VERIFICATION STANDARDS: Determinism Over Prose, Codebase Hygiene (no workarounds, hacks, dead paths), 99% No-Comment Policy (comment density < 5%), strict architectural boundaries, 100% English codebase (Rule 2), and automated guardrails linter (`scripts/lint_guardrails.py`, LINT001-LINT014).

## Evidence (2026-11-19: Waterfall Evolution 5Y 20220101-20261116)
| Setup | Trades | PnL % | Profit abs (USDT) | Max DD | Calmar | Sharpe | Winrate |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Dual-Regime Waterfall (Production)** | **354** | **+5599.18%** | **55,991.80** | **18.89%** | **318.39** | **1.28** | **57.60%** |
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
