# Stabilization — Deterministic MTF Sniper

**Branch:** `pi-deploy` · **Package:** `strategies/mtf_sniper` · **Phase:** Hunter OFF by design

## Runtime modes

| Mode | Hunter | Decision | External AI |
|---|---|---|---|
| `pure_deterministic` (alias `pure`) | forced OFF | **forced OFF** | blocked |
| `deterministic` (alias `deterministic_native`) | forced OFF | optional native | blocked |
| `decision_native` | forced OFF | optional native | blocked |
| `decision_external` | still default OFF this phase | optional | allowed |

Set via `strategy_params.mtf_runtime_mode` or env `MTF_RUNTIME_MODE`.

- **Coolify / Ep46 production overlay:** `docker/config.mtf_sniper.json` → `"mtf_runtime_mode": "deterministic"` + `"enable_jev_decision": true` (native Decision ON).
- **Sniper-only measurement:** `"mtf_runtime_mode": "pure_deterministic"` (Decision cannot affect BT/live confirm).

## Call graph (deterministic)

Two **implementation** paths share one Policy contract (see `ARCHITECTURE.md`):

```
populate_entry_trend
  → EntryPipeline.live (always hunter_escalation=None — hard-disabled on live)
      Fast:  cached Sniper Parts → vectorized AND → TradeCandidate
      Event: SensorSuite → CandidateBuilder → SetupComposer → TradeCandidate
      → Long/Short Policy (setup eligibility ≠ quality grade)
      → strategy cooldown → enter_*
      → CandidateRegistry.remember(canonical candidate_id)
confirm_trade_entry
  → resolve candidate_id:
       registry.lookup (max-age 6h, no future)
       → else DataFrame enter_* reconstruct (same max-age 6h)
       → else candidate_id_missing
  → RiskEngine.authorize_entry
      → portfolio guards (circuit / directional / distress / max open)
      → JevDecisionAdapter(force_native=True) if enable_jev_decision
      → RiskAuthorization (gate only; stake/leverage echoes not applied to order)
```

### Hunter config vs execution

`RuntimeProfile.enable_jev_hunter` may be `True` under `decision_external` if config requests it.
**Live entry still forces `hunter_escalation=None`.** Treat startup `Hunter=True` as config intent only — pipeline does not escalate. Prefer keeping Hunter flags false until an explicit re-enable project.

## Defects fixed in this pass

1. **Parallel hyperopt pickle** — ledger locks (prior) + telemetry off in HO.
2. **Short/Long grade remap** — setup ACCEPT no longer remaps `REJECT` grade → fake `B`/`A`.
3. **Candidate ID drift** — shared `make_candidate_id` + UTC normalize for fast path + builder.
4. **No explicit runtime mode** — added `RuntimeMode` + startup fingerprint log.
5. **External Decision under “deterministic” intent** — forced native; spy-tested.
6. **RiskAuthorization incomplete surface** — explicit sizing/exposure fields; documents that stake is **not** stop-distance-based (`sizing_method=freqtrade_proposed`).
7. **Config divergence** — `EffectiveConfig` + warnings when pair overrides mask Parameters.
8. **Fast vs event candidate semantics** — SetupComposer normalizes part order / confluence to match vectorized AND; integration test compares contract + Policy.
9. **`pure_deterministic`** — Decision forced OFF for Sniper isolation measurement.
10. **Decision adapter env side-effect** — no longer writes `os.environ["JEV_MODE"]`; mode passed to engine ctor.
11. **Candidate ID through confirm** — `CandidateRegistry` + `_resolve_candidate_id` (no ad-hoc confirm-time ID).
12. **Candidate ID through trade ledger** — entry `order_filled` binds `trade_id→candidate_id`; exit ledger reuses it (unbind on record).
13. **Strategy startup** — no longer mutates global `JEV_MODE` either (adapter-only).
14. **RiskAuthorization honesty** — `risk_budget_used_pct` / `risk_per_trade_pct` / `total_exposure_pct` are `None` when unavailable; `total_exposure_notional` always computed; pct only with `wallet_equity`.
15. **Restart-safe Candidate ID** — entry fill persists `mtf_candidate_id` on Trade custom data; exit prefers custom data over RAM registry.
16. **No non-canonical ID invent** — missing correlation → `candidate_id_missing` (never `{pair}_{side}_{tag}_{ts}`).
17. **Invalid runtime mode** — misspellings raise `InvalidRuntimeModeError` (no silent privilege escalation).
18. **Decision Engine dotenv** — parsed to local dict; does not write `os.environ`.
19. **Pair stoploss in RiskAuth** — `confirm_trade_entry` passes effective pair `stoploss` into `authorize_entry`.
20. **Registry time window** — `CandidateRegistry.lookup` never returns a future candidate; no newest-fallback when none match; optional max-age window (default 6h).
21. **Candle index reconstruction** — `_dataframe_position` uses absolute DF index (`get_indexer` / `get_loc`); never `len(hits)-1`.
22. **Persist verify** — `_persist_candidate_id` returns `bool`, re-reads custom data, logs `candidate_id_persistence_failed`; read path validates canonical format.
23. **Startup fail-fast proof** — `_load_external_config` re-raises `InvalidRuntimeModeError` (tested via strategy load path).
24. **DataFrame fallback max-age** — reconstruct path uses the same `DEFAULT_LOOKUP_MAX_AGE` as registry (no arbitrarily old signal).
25. **Persisted ID context check** — `parse_candidate_id` / `candidate_id_matches_trade` validate side + pair; read/persist reject mismatches.
26. **`_dataframe_position`** — `get_loc` first (last duplicate), absolute row only; covered for Range/Datetime/sparse/duplicate indexes.
27. **Canonical ID hardening** — reject negative `candle_index`; `remember()` validates / replaces mismatched provided IDs.
28. **RiskAuth docs honesty** — confirm path does not pass `proposed_stake`; `stake_amount`/`leverage` are not live order echoes.

## Semantics: Policy vs Decision

| Field | Meaning |
|---|---|
| `PolicyEvaluationResult.is_valid` | Setup **eligibility** (may enter pipeline) |
| `quality_grade` | Setup **quality** (A/B/C/REJECT) — informational at Policy layer |
| Decision native | Uses candle/macro **scores** in payload — does **not** read `quality_grade` |

A setup with `is_valid=True` and `quality_grade=REJECT` can still reach `confirm_trade_entry`; Decision then applies its own score heuristics (when enabled).

## Effective configuration

Authoritative resolver: `strategies/mtf_sniper/core/config_resolve.py`.

Startup logs `[EffectiveConfig] {…, fingerprint}` and pair-override warnings.

Known intentional mask: `custom_pair_params` long/short body & dist override Parameter values (HO Ep46 kept body/dist at 0.63/3.3).

## Telemetry schema (Decision ledger)

`TelemetryDecisionRecord` now includes optional:

- `setup_type`, `signal_source`, `policy_version`
- `config_fingerprint`
- `feature_snapshot` (small dict — never full DataFrames)

Canonical `candidate_id` should match populate → confirm when registry is warm.

## Remaining risks / limitations

- Stop-distance-based risk budget **not** implemented (honest `sizing_method`).
- Margin/liquidation buffer check enum exists but not fully wired to exchange margin.
- Full Freqtrade order lifecycle (partial fill / stoploss fill / live restart) still a verification gap vs unit callback stubs.
- Informative merge/alignment beyond unit prefix tests still needs expansion.
- JEV Hunter / Momentum Radar expansion remain **out of scope**.

## Tests executed

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.strategy.test_mtf_data_contracts \
  tests.strategy.test_mtf_candidate_id_hardening \
  tests.strategy.test_mtf_runtime_deterministic \
  tests.strategy.test_mtf_candidate_parity \
  tests.strategy.test_mtf_candidate_registry \
  tests.strategy.test_mtf_callback_integration \
  tests.strategy.test_mtf_policy_grade_semantics \
  tests.strategy.test_mtf_policy_decision_semantics \
  tests.strategy.test_mtf_sniper_directional \
  tests.strategy.test_mtf_sniper_mask_parity \
  tests.strategy.test_mtf_sniper_risk \
  tests.strategy.test_mtf_sniper_exit_policy \
  tests.strategy.test_telemetry_pickle \
  tests.strategy.test_mtf_lookahead_indicators
```
