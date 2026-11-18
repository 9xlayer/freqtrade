# MTF Sniper — Migration Plan (historical + current)

**Location:** `strategies/mtf_sniper/docs/MIGRATION_PLAN.md`  
**Current package:** `STRATEGY_VERSION` in `__init__.py` (`4.4.1-deterministic`)  
**Authoritative live architecture:** `ARCHITECTURE.md` + `STABILIZATION_DETERMINISTIC.md` + `DATA_CONTRACTS.md`

> **How to read this file:** P0–P4 are **historical migration records**. Descriptions of “live path” inside those phases reflect the state **at that time**, not today. **P5+ / Stabilization** is the current live architecture.

```
P0 Audit → P1 Sensors → P2 Parity engine → P3 Risk/Telemetry → P4 Long Hunter gates
        → P5 SensorSuite cutover → Stabilization deterministic (current)
```

---

## Historical phases (archive)

### Phase P0 — Forensic audit & baseline freeze *(historical)*

- Froze early 5Y parity baseline on then-current monolithic strategy.
- Gate numbers from that era (e.g. 273 / +24,282) are **archive benchmarks**, not the Ep46 deterministic baseline.

### Phase P1 — Core contracts & Sensor Suite *(historical, ✅)*

- Introduced `core/enums.py`, `core/contracts.py`, flat sensors, `SensorSuite`, `CandidateBuilder`.

### Phase P2 — Directional engine & behavioral parity *(historical, ✅)*

**At P2 time**, live path was still sniper masks + hunter_merge + parity helpers; SensorSuite→Builder→Directional was experimental.

> Do **not** treat P2 “live path” wording as current. Current live = P5 cutover + deterministic dual path (fast AND + event) in `ARCHITECTURE.md`.

### Phase P3 — Risk engine & telemetry *(historical, ✅)*

- Centralized portfolio guards, ledgers, exit extract. Risk remains an **authorization gate** (see `DATA_CONTRACTS.md` — stop-distance sizing still not live).

### Phase P4 — Long Hunter gates *(historical, ✅ gated)*

- Evidence package: `P4_LONG_HUNTER_EVIDENCE.md` at strategy tag **`v3.2.0-p4-hunter-gates`**.
- Production default stayed `enable_hunter_long=false`.
- **Current package** (`4.4.1-deterministic`) keeps Hunter **off the live entry pipeline** regardless of leftover config flags.

---

## P5 — SensorSuite live cutover *(architectural ancestor of current)* ✅

- Removed `strategies/active/` dependency; package path `strategies/mtf_sniper`.
- Live entry layered through EntryPipeline; JEV under `intelligence/`.
- Legacy “exact 273-trade” checklist retired — new BT baselines are the gate.

**Stabilization delta (current, post-P5):**

| Topic | Current truth |
|---|---|
| Entry | Dual path: vectorized fast AND **or** event SensorSuite→Builder→Composer → shared Policy |
| Hunter | Hard-disabled in `EntryPipeline.live` (`hunter_escalation=None`) |
| Decision | Optional native under `deterministic`; external only in `decision_external` |
| Candidate ID | Canonical `{pair}_{side}_{epoch}_{candle_index}`; registry + DF fallback (6h max-age); Trade custom data |
| Risk | Authorize + SL ladder; **not** stop-distance stake authority |
| Baseline | Ep46 Decision ON: see Memory Bank / `STABILIZATION_DETERMINISTIC.md` |

---

## Live verification checklist (current)

| Check | Requirement |
|---|---|
| 1. Unit / contract suite | `tests/strategy/test_mtf*.py` green (incl. data contracts + candidate parity) |
| 2. Full BT | Record trades / PnL / DD vs Ep46 gate when changing signal path |
| 3. Docker | `strategy-path` → `strategies/mtf_sniper` + overlay `mtf_runtime_mode=deterministic` |
