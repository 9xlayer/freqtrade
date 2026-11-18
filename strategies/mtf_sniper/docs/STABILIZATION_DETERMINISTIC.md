# Stabilization — Deterministic MTF Sniper

**Branch:** `pi-deploy` · **Package:** `strategies/mtf_sniper` · **Phase:** Hunter OFF by design

## Runtime modes

| Mode | Hunter | Decision | External AI |
|---|---|---|---|
| `deterministic` (default) | forced OFF | optional native heuristics | **blocked** |
| `decision_native` | forced OFF | optional native | blocked |
| `decision_external` | still default OFF this phase | optional | allowed if `JEV_MODE=external` |

Set via `strategy_params.mtf_runtime_mode` or env `MTF_RUNTIME_MODE`.

Coolify overlay: `docker/config.mtf_sniper.json` → `"mtf_runtime_mode": "deterministic"`.

## Call graph (deterministic)

```
populate_entry_trend
  → EntryPipeline.live (Hunter=None)
      → vectorized AND parts  OR  SensorSuite events → CandidateBuilder
      → SetupComposer (drop anomalies)
      → Long/Short Policy (setup eligibility ≠ quality grade)
      → strategy cooldown → enter_*
confirm_trade_entry
  → RiskEngine.authorize_entry
      → portfolio guards (circuit / directional / distress / max open)
      → JevDecisionAdapter(force_native=True) if enable_jev_decision
      → explicit RiskAuthorization (+ fingerprint)
```

## Defects fixed in this pass

1. **Parallel hyperopt pickle** — ledger locks (prior) + telemetry off in HO.
2. **Short/Long grade remap** — setup ACCEPT no longer remaps `REJECT` grade → fake `B`/`A`.
3. **Candidate ID drift** — shared `make_candidate_id` + UTC normalize for fast path + builder.
4. **No explicit runtime mode** — added `RuntimeMode` + startup fingerprint log.
5. **External Decision under “deterministic” intent** — forced native; spy-tested.
6. **RiskAuthorization incomplete surface** — explicit sizing/exposure fields; documents that stake is **not** stop-distance-based (`sizing_method=freqtrade_proposed`).
7. **Config divergence** — `EffectiveConfig` + warnings when pair overrides mask Parameters.

## Effective configuration

Authoritative resolver: `strategies/mtf_sniper/core/config_resolve.py`.

Startup logs `[EffectiveConfig] {…, fingerprint}` and pair-override warnings.

Known intentional mask: `custom_pair_params` long/short body & dist override Parameter values (HO Ep46 kept body/dist at 0.63/3.3).

## Telemetry schema (Decision ledger)

`TelemetryDecisionRecord` now includes optional:

- `setup_type`, `signal_source`, `policy_version`
- `config_fingerprint`
- `feature_snapshot` (small dict — never full DataFrames)

## Remaining risks / limitations

- Stop-distance-based risk budget **not** implemented (honest `sizing_method`).
- Margin/liquidation buffer check enum exists but not fully wired to exchange margin.
- Fast-path vs event-scan full feature parity for non-part suites still falls back to event scan.
- Look-ahead / MTF alignment suite needs dedicated Phase 8 expansion (indicator unit tests exist partially).
- JEV Hunter / Momentum Radar expansion remain **out of scope**.

## Tests executed

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.strategy.test_mtf_runtime_deterministic \
  tests.strategy.test_mtf_candidate_parity \
  tests.strategy.test_mtf_policy_grade_semantics \
  tests.strategy.test_mtf_sniper_directional \
  tests.strategy.test_mtf_sniper_risk \
  tests.strategy.test_telemetry_pickle
```
