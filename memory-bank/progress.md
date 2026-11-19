# Progress

## What works
- Part sensors + SetupComposer recipes (`setup:sniper_short/long`)
- Vectorized AND fast path in `EntryPipeline` (BT-safe)
- Policy setup-only ACCEPT; live Hunter off
- 5Y Decision-OFF parity with legacy AND-mask system
- Version sync: package/strategy `4.4.0-deterministic`; Decision/Hunter engine `2.7.0`

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
- Hunter OFF; Sniper entry/exit/thresholds/risk multipliers unchanged
- Ep46 BT ON on `2eb6607`: 299 / **+3866.6%** / **DD 18.89%** / PF 1.71
- Hunter / Momentum Radar still out of scope

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
