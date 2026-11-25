# Tech Context

## Stack
- **Runtime**: Python 3.12, Freqtrade (dev build on `pi-deploy`)
- **Exchange**: Binance USDT-M futures, isolated margin
- **Data**: local feather under `user_data/data/binance/futures/`
- **Deploy**: Docker Compose (`docker-compose.yml`), Pi-oriented volume `/data/freqtrade/user_data`
- **AI vendor**: Typesafe SystemOne `https://api.typesafe.ai/v1/systemone`

## Important paths
| Path | Role |
|---|---|
| `strategies/mtf_sniper/` | Live SensorSuite strategy + JEV Decision |
| `user_data/config.json` | Runtime config (pairs, stake, strategy_params) |
| `docker-compose.yml` | Coolify/Pi compose: env + trade command |
| `docker/Dockerfile.custom` | Bakes `strategies/` + overlay config into image |
| `docker/config.mtf_sniper.json` | Forces `StrategyMTFSniper` + `/freqtrade/strategies/mtf_sniper` |
| `docker/README.md` | Coolify deploy checklist |
| `.env` | Secrets + mode (gitignored) |
| `reports/` | Audit reports (treat as claims, verify) |
| `tests/strategy/test_jev_architecture.py` | JEV architecture tests |
| `reports/BACKTEST_SPECIFICATION.md` | Canonical backtest command recipe |

## Coolify / Docker load path
- Strategies are **not** on the volume — they come from the image `COPY strategies/`.
- Trade command loads two configs: mounted `user_data/config.json` then overlay `config.mtf_sniper.json` (overlay wins for strategy path).
- `PYTHONPATH=/freqtrade` required for package imports.
- Do not point Coolify custom command at removed `strategies/active`.

## Config knobs
### `user_data/config.json` → `strategy_params`
- `enable_jev_decision` (default true)
- `enable_jev_hunter` (default **false** — deterministic phase; live pipeline ignores Hunter)
- `enable_dynamic_sizing` (config may be true; sizing still not full risk-budget)

### Environment (must be in compose `environment:` to reach container)
```bash
JEV_API_KEY=...
JEV_API_URL=https://api.typesafe.ai/v1/systemone
MTF_RUNTIME_MODE=decision_external   # Docker production; use deterministic for local BT
JEV_MODE=external                    # native|external|backtest (external in Docker)
JEV_FAIL_POLICY=fallback_native      # allow|reject|fallback_native
JEV_CONFIRM_THRESHOLD=0.70
ENABLE_JEV_DECISION=true
ENABLE_JEV_HUNTER=false
ENABLE_WATERFALL=true
ENABLE_DYNAMIC_SIZING=true
JEV_HUNTER_MODEL_VERSION=jev-latest   # optional override when Hunter re-enabled
```

**Note:** Compose `.env` only substitutes `${VAR}` — it does **not** auto-inject all keys into the container.

## Valid external model
- Working alias: **`jev-latest`**
- Broken aliases (HTTP 400 Unknown model): `jev-hunter-v1.0`, `jev-hunter-latest`, `jev-hunter-v2.6.2`

## Backtest recipe (offline / native)
```bash
PYTHONPATH=. JEV_MODE=native JEV_FAIL_POLICY=allow ENABLE_TELEMETRY=true \
.venv/bin/freqtrade backtesting \
  --config user_data/config.json \
  --strategy StrategyMTFSniper \
  --strategy-path strategies/mtf_sniper \
  --timerange 20220101-20261116 \
  --breakdown year \
  --cache none
```
Needs network once for Binance `load_markets` even with local OHLCV.
Ledgers (when enabled): `user_data/telemetry/decision_ledger.jsonl`, `trade_ledger.jsonl`.

## Dev commands
```bash
.venv/bin/python -m unittest tests.strategy.test_jev_architecture
.venv/bin/python -m unittest tests.strategy.test_telemetry_pickle
# optional if present:
.venv/bin/python -m unittest tests.strategy.test_execution_safety

# Hyperopt (parallel OK after ledger pickle fix); Decision OFF overlay recommended
PYTHONPATH=. JEV_MODE=native PYTHONUNBUFFERED=1 \
.venv/bin/freqtrade hyperopt \
  --config user_data/config.json \
  --strategy StrategyMTFSniper --strategy-path strategies/mtf_sniper \
  --spaces buy --hyperopt-loss SharpeHyperOptLossDaily \
  --epochs 100 -j -2 --disable-param-export
```

## GitNexus + agent protocol (mirrors IWAS/getiwas)
- Indexed repo name: **freqtrade**
- Index dir: `.gitnexus/` (gitignored; regenerate locally)
- Runner: `gitnexus` (global) or `node .gitnexus/run.cjs …`
- Stats at first index (2026-11-17): ~11,580 nodes, ~32,436 edges, 464 clusters, 739 flows
- **Rules:** `.cursor/rules/memory-bank.mdc`, `.cursor/rules/gitnexus.mdc` (`alwaysApply`)
- **Hooks:** `.cursor/hooks.json` → `session-start.sh`, `after-git-commit.sh`
- **MCP:** `.cursor/mcp.json` includes `gitnexus` (`npx gitnexus mcp`) + TradingView
- **Skills:** canonical `.agents/skills/gitnexus-*`; symlinks in `.claude/skills` + `.cursor/skills`
- **Protocol doc:** `AGENTS.md`

```bash
gitnexus status
gitnexus analyze          # full/reindex
gitnexus analyze --force  # force rebuild
```

## Constraints
- Do not commit `.env` (contains API key).
- Do not commit `.gitnexus/` binary index.
- External mode in backtests will hammer API + destroy determinism — use `native`/`backtest`.
- Strategy files are baked via Docker image build unless otherwise mounted; Pi deploy needs rebuild/recreate after strategy changes.
