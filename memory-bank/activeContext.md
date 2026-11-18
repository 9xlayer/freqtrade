# Active Context

## Current focus
**v4.4.0-deterministic** — validated AND-mask parity + BT perf cleanup.

## Live path
```
EntryPipeline.live:
  vectorized part AND (fast) → Policy setup-only ACCEPT
  (event scan fallback if suite ≠ part sensors)
→ strategy cooldown → enter_*
Hunter/anomaly OFF on live; Decision Gate optional on confirm.
```

## Evidence
- 5Y Decision OFF: new ≡ old (234 trades, same PnL/DD)
- 5Y Decision ON (new): 213 trades, DD ~22% (veto filter)
- BT wall ~seconds after vectorized path (was ~22s/confirm thrash)

## Versions
- Single hub: `strategies/mtf_sniper/__init__.py`
- `STRATEGY_VERSION` / Decision `2.7.0` / Hunter `2.7.0` — bump only there

## Cleanup done
- Removed `strategies/compare_old/` (temp BT mirror)
- Removed dead `sniper_hardpass`, `pattern_sniper_mask`
- Slimmed legacy mask aliases; tests aligned to setup recipes

## Defaults
- `enable_jev_hunter=False` (matches live pipeline)
- Version hub: `strategies/mtf_sniper/__init__.py`

## Next
Re-enable Hunter/anomaly behind flags after more live soak.
