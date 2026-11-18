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

## BT evidence (wallet 1000, hunter off)
- 5Y OFF new=old: 234 trades, +12307%, DD 43.8%
- 5Y ON new: 213 trades, +7905%, DD 21.9%
- 2026 ON: 31 trades, +209%, DD 21.8%

## Do not regress
- Incomplete parts ≠ enter_*
- Single EntryPipeline path
- Open/Closed: recipes in directional, not strategy facade
