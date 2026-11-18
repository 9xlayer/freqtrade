# Docker / Coolify deploy (MTF Sniper)

## How strategy loading works

| Layer | Role |
|---|---|
| Image build (`Dockerfile.custom`) | Copies `strategies/` + `config.mtf_sniper.json` |
| Volume `/data/freqtrade/user_data` | Secrets, pairs, SQLite, logs only |
| Compose `command` / image `CMD` | Forces `--strategy-path` + overlay config |

Overlay `/freqtrade/config.mtf_sniper.json` **overrides** a stale `strategy_path` in the mounted `user_data/config.json` (e.g. old `strategies/active`).

`PYTHONPATH=/freqtrade` is required so `from strategies.mtf_sniper…` imports resolve.

## Coolify checklist

1. Build from repo root with `docker-compose.yml` (or Dockerfile `docker/Dockerfile.custom`).
   Base image: `ghcr.io/freqtrade/freqtrade:latest` (avoid broken `:stable` digests on GHCR).
2. Keep volume mount: `/data/freqtrade/user_data` → `/freqtrade/user_data`.
3. Do **not** replace the compose `command` unless you keep:
   - `--config /freqtrade/config.mtf_sniper.json`
   - `--strategy-path /freqtrade/strategies/mtf_sniper`
   - `--strategy StrategyMTFSniper`
4. Set env in Coolify (same keys as compose): `JEV_*`, `ENABLE_JEV_*`, proxies.
5. After first deploy, optionally update host `config.json` to `strategy_path: strategies/mtf_sniper` (local template already does).

## Verify inside container

```bash
docker exec freqtrade freqtrade list-strategies \
  --strategy-path /freqtrade/strategies/mtf_sniper
# expect: StrategyMTFSniper

ls /freqtrade/strategies/mtf_sniper/strategy_mtf_sniper.py
ls /freqtrade/config.mtf_sniper.json
echo "$PYTHONPATH"   # /freqtrade
```
