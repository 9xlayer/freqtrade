# Product Context

## Why it exists
Manual discretionary crypto futures trading at 10x is fragile. This suite aims to encode a **convex trend-following** process: many controlled small losses, a minority of large trend captures that dominate PnL.

## Problems it tries to solve
- Entering against higher-timeframe regime (long in bear / short in mega-bull).
- Over-trading chop / low-quality wicks.
- Coupling “nice 15m candle” with portfolio permission to open risk.
- Mixing AI marketing with unverified statistical edge.

## How it should work (product flow)
```text
MTF indicators + regime
        ↓
Sniper signals  +  MomentumRadar events
        ↓
JEV Hunter (propose candidate / no_trade)
        ↓
Strategy filters (macro, wick, floor/PDH, cooldown)
        ↓
Candidate pool → Risk gates (circuit breaker, directional lock, distress)
        ↓
JEV Decision Gate (CONFIRM / VETO)
        ↓
Execution + custom stoploss ladder / trend runner / timeout
```

## User experience goals
- Operator can see clearly: mode (`native` vs `external`), which tags traded, why vetoed.
- Backtests are honest and reproducible — not optimized narrative screenshots.
- Live bot fails safely: missing AI / API should follow explicit `JEV_FAIL_POLICY`.

## Product positioning (accurate)
**Quantitative rule-based trading system + optional external AI evaluation.**  
Not “self-learning AI that found alpha” unless proven by controlled A/B tests.
