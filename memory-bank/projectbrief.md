# Project Brief

## What this is
Freqtrade-based **crypto futures quantitative trading suite** focused on BTC/USDT:USDT and ETH/USDT:USDT, leverage target **10x**, base timeframe **15m** with MTF context **1H / 4H / 1D**.

Primary product surface:
- `strategies/active/StrategyMTFSniper.py` — bi-directional LONG + SHORT
- `strategies/active/StrategyMTFShortSniper.py` — short-only variant
- JEV modules: `jev_entry_hunter.py` (candidate proposer) + `jev_decision.py` (confirm/veto gate)

Repo branch of record for deploy work: **`pi-deploy`**.

## Core goals
1. Systematic trend-following / breakout entries with multi-timeframe regime filters.
2. Separate signal generation from risk/authorization gates.
3. Optional external AI evaluation via Typesafe (`JEV_MODE=external`), with deterministic **native** path for backtests.
4. Operate safely on Raspberry Pi / Docker deploy before scaling live capital.

## Non-goals (current)
- Claiming production-ready edge from a single optimistic backtest report.
- Treating JEV external mode as proven alpha without A/B evidence.
- Adding more indicators/setups before edge proof + risk engine maturity.

## Success criteria
- Reproducible backtests from frozen commit + config + costs.
- Clear attribution by year / side / enter_tag.
- Risk controls commensurate with 10x leverage.
- Live/paper reliability (restart, partial fills, exchange reconciliation) proven before size-up.

## Working language
- Code/comments: English preferred.
- Operator docs / chat: Vietnamese OK.
