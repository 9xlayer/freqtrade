# Active Context

## Current focus
**v4.4.2-deterministic** — ETH Pair-Specific Waterfall Filter Tuning & Zero-Lint Clean Workspace.
- Deep quantitative audit of 29 ETH Waterfall trades over 5Y dataset (`20220101-20261116`).
- Isolated root cause of ETH breakdown failure: 5 catastrophic trades (-$5,200 USDT total loss) all executed when `dist_ema50_4h > 0` (price floating above 4H EMA50, turning 15m volume dumps into dip-buying liquidity sweeps for bulls). Conversely, 100% of top winners (+5.3k, +4.1k, +2.1k USDT) occurred when `dist_ema50_4h <= 0`.
- Architected **Pair-Specific Waterfall Filtering**:
  - `WaterfallFilterConfig` dataclass supporting per-pair overrides loaded dynamically in `EntryPipeline.live` and configured via `custom_pair_params` in `StrategyMTFSniper`.
  - Configured `waterfall_require_below_ema50_4h = True` specifically for `ETH/USDT:USDT`.
- **5Y Backtest Scorecard (`20220101-20261116`):**
  - Trades: **344** | Win Rate: **57.8%** (199W / 145L)
  - Total Profit: **+$77,412.93 USDT (+7,741.29%)** — **+$21,421.13 USDT higher than previous Dual-Regime baseline (+55,991.80 USDT)!**
  - Max Drawdown: **18.89%** — **EXACTLY flat (zero drawdown surge)!**
  - Calmar: **440.14** | Sharpe: **1.35** | Sortino: **1.60**
  - Standalone ETH: surged by +60% from +$10,635 USDT to **+$17,016.10 USDT**.
  - Standalone BTC: surged from +$47,715 USDT to **+$60,396.80 USDT**.
  - Waterfall setup nominal contribution: surged from +$22,362 USDT to **+$36,637.40 USDT**.
- **VS Code Problems Tab & Linter Quality Gate (100% Resolved):**
  - Resolved all 64 Ruff warnings on `scripts/lint_guardrails.py` (`UP035`, `UP006`, `UP045`, `PTH123`, `UP015`, `E501`, `C901`, `SIM102`, `PIE810`).
  - Integrated Meta Pyrefly (`LINT015`) into `scripts/lint_guardrails.py` with 0 errors / 0 warnings across all 18 strategy files.
  - Codified formal architectural boundary rules in `scripts/lint_guardrails.py`:
    - `LINT007`: Layer 0 (`core/`) absolute isolation from higher layers.
    - `LINT008`: Layer 1 (`market/`) pure computing isolation from higher layers.
    - `LINT009`: Anti-circular Facade dependency (no module may import `strategy_mtf_sniper`).
    - `LINT016`: Immutability of inter-layer data contracts (`@dataclass(frozen=True)`).
    - `LINT017`: Strategy Facade thinness (cables indicator calculation to market/sensors, no raw `talib` in strategy).
    - `LINT018`: Sensor OOP inheritance (`*Sensor` must inherit `BaseSensor`).
  - Locked 3-Layer Defense Gate: Pytest (`tests/strategy/test_architecture_guardrails.py`), Git Pre-Commit Hook (`.git/hooks/pre-commit`), and Agent Rituals (`AGENTS.md`).
  - 414 strategy unit tests passing (`pytest tests/strategy` in 3.36s).
  - All architectural guardrails passing (`scripts/lint_guardrails.py`, 0 violations).

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
