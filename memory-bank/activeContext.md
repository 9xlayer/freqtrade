# Active Context

## Current focus
Stabilize honesty of the trading system: **edge proof + risk realism**, not more setups.

GitNexus is set up for this repo (`freqtrade` index). Agent protocol now mirrors IWAS/getiwas:
Cursor alwaysApply rules + session/commit hooks + MCP `gitnexus` + canonical `.agents/skills` symlinks.

Branch: **`pi-deploy`**  
HEAD at memory-bank creation: see `git log -1` (includes JEV gate/hunter fixes + ShortSniper CB/cooldown work).

## Recent changes (2026-11-17 session)
1. Removed `ENABLE_EXTERNAL_JEV`; mode only via `JEV_MODE`.
2. Wired `JEV_MODE` / `JEV_API_URL` / `JEV_FAIL_POLICY` / `JEV_CONFIRM_THRESHOLD` into `docker-compose.yml`.
3. External Hunter model default → `jev-latest` (old hunter-specific aliases 400).
4. Long Hunter aligned to **`breakout_expansion`** + trend-follow prompts.
5. External Decision Gate: threshold **0.60**, hard veto **choppy_ranging**.
6. Circuit breaker isolation / cooldown unification work landed on branch (ShortSniper + tests/specs).

## Latest verified backtest (native, cache none)
- Timerange: `20220101-20261116`
- Result zip: `user_data/backtest_results/backtest-result-2026-11-17_09-20-35.zip`
- Net: **+6,055 USDT (+605%)**, PF **1.52**, WR **58%**, Max DD **27.4%**, trades **326**
- `jev_hunter_breakout_expansion`: **92 trades, −832 USDT** (hurts)
- `jev_hunter_waterfall_collapse`: +925 USDT
- Sniper tags still carry most profit
- Years 2022–2023 negative in this run

Contrast: archived report claimed ~**+108k / PF 3.82** — **do not treat as baseline** until reproduced from frozen commit.

## Active decisions
- External AI may be enabled live via `JEV_MODE=external`, but **edge claims stay native/A-B verified**.
- Prefer trend-follow only; no mean-reversion Hunter longs.
- Next product priority: quantify whether Long Hunter breakout should be disabled/stricter filters.

## Open questions
1. Disable Long Hunter entirely until breakout filters improve?
2. Reproduce old report conditions (funding, params, hunter setups) for forensic delta?
3. Push latest commits + update Pi `.env` (`JEV_MODE`, threshold) and rebuild image?

## Operator reminder
Local `.env` is gitignored. Pi must set its own:
```bash
JEV_MODE=external   # or native
JEV_CONFIRM_THRESHOLD=0.60
JEV_API_KEY=...
```
