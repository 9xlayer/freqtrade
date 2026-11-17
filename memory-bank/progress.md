# Progress

## What works
- **GitNexus indexed** (`gitnexus analyze` OK).
- **IWAS-style agent protocol ported:** `.cursor/rules/{memory-bank,gitnexus}.mdc`, hooks, MCP, `AGENTS.md`, skills under `.agents/skills` with symlinks.
- MTF Sniper bi-directional pipeline with candidate pool + risk gates.
- Hunter Radar → propose → strategy filters path.
- Native JEV Decision/Hunter deterministic offline evaluation.
- External Typesafe path works with **`jev-latest`** when `JEV_MODE=external` + API key.
- Docker env wiring for JEV mode/threshold (compose updated).
- Unit suite `test_jev_architecture` green after gate/setup contract fixes (56 tests at last run).
- Local 5y native backtest runnable (needs Binance markets network).

## What is incomplete / weak
- **Statistical edge proof** incomplete; report +108k not reproduced.
- **Long Hunter `breakout_expansion`** currently negative expectancy in latest native BT.
- Risk engine incomplete for 10x (no true %-equity stop sizing, daily loss limit, correlation caps, liq guards as product features).
- Production reliability drills (restart / partial fill / SL on exchange / dual-writer SQLite) not fully proven.
- A/B value of Hunter vs Gatekeeper vs Sniper-only not completed.
- Docs/reports max DD inconsistencies historically (e.g. 21.95% vs 20.69%).

## Latest status snapshot
| Area | Status |
|---|---|
| Architecture | Mature enough to extend |
| Signal MTF | Solid direction |
| JEV integration | Functional; value unproven |
| Risk for live size | Not ready |
| Evidence quality | Improving after reality-check BT |
| Live capital readiness | Not production-ready |

Informal score retained from audit framing: **~6.5/10 design vs evidence gap**.

## Priority roadmap
### P0 — Prove / protect edge
- [ ] Freeze baseline commit + config + cost assumptions
- [ ] Ledger export + year/side/tag attribution
- [ ] A/B: Sniper-only vs +Hunter vs +Gate
- [ ] Decision on Long Hunter: disable vs stricter filters (after −832 BT evidence)

### P1 — Risk engine
- [ ] Risk-based sizing from stop distance + equity %
- [ ] Daily / portfolio loss limits
- [ ] Exposure / correlation guards
- [ ] Liquidation distance + exchange SL verification

### P1 — JEV hygiene
- [x] Single mode switch (`JEV_MODE`)
- [x] Working external model alias
- [x] Stricter confirm threshold + choppy veto
- [x] Long setup contract = `breakout_expansion`
- [ ] Immutable model version from API response in cache policy
- [ ] True concurrency tests for SQLite cache

### P2 — Production validation
- [ ] Paper trading soak
- [ ] Restart / outage / partial fill drills
- [ ] Small capital low leverage only after soak metrics

## Known issues log
| Issue | Impact | Notes |
|---|---|---|
| Old report PnL concentration in 2026 | Misleading headline | ~85% of claimed profit |
| Unknown model 400 on hunter aliases | External hunter spam failures | Fixed via `jev-latest` |
| Long Hunter setup mismatch | Dead or wrong longs | Fixed contract; now live but losing in BT |
| Gate threshold 0.25 | Near pass-through | Raised to 0.60 + choppy veto |
| `.env` not in container automatically | Mode stays native on Pi | Must wire compose env |

## Do not regress
- Do not reintroduce `ENABLE_EXTERNAL_JEV`.
- Do not set Hunter external model to non-existent `jev-hunter-*` aliases without verifying API.
- Do not lower Gate threshold below ~0.50 without measuring confirm rate on choppy regimes.
- Do not treat archived `REPORT.md` headline numbers as ground truth without re-run.
