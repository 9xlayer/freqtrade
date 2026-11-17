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

7. Forensic audit of profit drop: isolated two causes:
   - Native Hunter Long `breakout_expansion` added 92 trades (−832 USDT), destroying 2022-2023 compounding wallet base.
   - `is_in_macro_bull` rigidly blocked all shorts in 2024 (including ETH +78% ROE on July 24).
8. Parameterized controls added in StrategyMTFSniper: `enable_hunter_long` (default False) and `block_macro_bull_shorts` (default False).
9. Downloaded latest Binance Futures data through 2026-11-17.

## Latest verified backtest (native, cache none, updated data to 2026-11-17)
- Strategy: **`StrategyMTFSniper`**
- Timerange: `20220101-20261117`
- Result: **+24,282.26 USDT (+2,428.23%)**, PF **1.55**, WR **59.0%** (161W / 112L), Max DD **32.0%**, trades **273**
- Sharpe: **1.14**, Sortino: **1.35**, Calmar: **81.44**
- 100% positive across all 5 years:
  - 2022: +6.21 USDT (WR 62.5%)
  - 2023: +422.96 USDT (WR 51.7%)
  - 2024: +957.22 USDT (WR 51.9%)
  - 2025: +5,764.08 USDT (WR 63.3%)
  - 2026: +17,131.78 USDT (WR 65.5%)
- Both pairs positive: BTC +23,638.79 USDT, ETH +643.47 USDT.

## Active decisions
- `enable_hunter_long` set to `False` by default: Long side safely led by `long_A80plus` (63.2% WR, +5,358 USDT).
- `block_macro_bull_shorts` set to `False` by default: allows high-conviction counter-trend shorts (`short_C_sub65`: 64.3% WR, +3,083 USDT).
- External AI may be enabled live via `JEV_MODE=external`, but native backtest is now stabilized at +24.3k USDT.

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
