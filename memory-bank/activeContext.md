# Active Context

## Current focus
**v4.4.1-deterministic** — stabilize deterministic mode (Hunter OFF by design).

## Live path
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

## Stabilize progress
- RuntimeMode `deterministic` + force-native Decision (no external AI)
- Candidate ID UTC contract; policy grade honesty; RiskAuthorization surface
- Docs: `strategies/mtf_sniper/docs/STABILIZATION_DETERMINISTIC.md`
- Still open: full MTF look-ahead suite, stop-distance sizing, BT parity re-run after commit

## Next
- Commit stabilize batches; Coolify redeploy (`:latest` base)
- Phase 8 look-ahead tests + BT parity report vs pre-Ep46 baseline
- Only then consider Hunter re-enable
