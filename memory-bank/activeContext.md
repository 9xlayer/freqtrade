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

## Evidence (re-run 2026-11-18, `504bb31`)
- 5Y Decision ON: 204 trades, +3463%, DD 25.4%, WR 60.8%
- 5Y Decision OFF: 224 trades, +4858%, DD 32.7%, WR 61.6%
- Does **not** reproduce report Run1 (+10,854%) — Hunter frozen path removed by design
- Beats report Run2 native (+1,966%) on PnL with Hunter off + SensorSuite

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

## Deploy (Coolify)
- Image bakes `strategies/mtf_sniper` + `docker/config.mtf_sniper.json` overlay
- Compose command: dual `--config` + `--strategy-path …/mtf_sniper`
- Mounted host config may still be stale; overlay forces correct path

## Next
- Redeploy Coolify with `--build` and verify `list-strategies` in container
- Re-enable Hunter/anomaly behind flags after more live soak.
