# P4 Long Hunter — Evidence & Gate Outcome (2026-11-18)

## Setup
- Strategy: `strategies/mtf_sniper` `v3.2.0-p4-hunter-gates`
- Timerange: `20220101-20261116`
- `JEV_MODE=native`
- Gate module: `directional/long/hunter_gates.py` → wired in `execution/hunter_merge.py`

## Baseline (pre-filter diagnostic, hunter long ON)
| Profile | Trades | Absolute profit | Notes |
|---|---|---|---|
| Parity (`hunter_long=false`) | 273 | **+24,282.26** | Freeze target |
| Unfiltered (`hunter_long=true`) | 348 | **+5,964.72** | −18.3k vs baseline |

### Unfiltered `jev_hunter_breakout_expansion`
| Metric | Value |
|---|---|
| Trades | 91 |
| PnL | **−702.05 USDT** |
| Win rate | 54.9% |
| Losers with MFE &lt; 1% | 28 trades / **−2,241 USDT** (chase / no follow-through) |
| Train 70% | 63 / −792.73 |
| OOS 30% | 28 / +90.67 (small-n) |

## P4 gates (implemented)
`evaluate_long_hunter_breakout` requires (defaults):
- confidence ≥ **0.90** (was 0.85)
- upper wick ≤ **0.20** (was 0.285)
- close position ≥ **0.72** (was 0.65)
- EMA21 distance ≤ **0.8%** (was 1.5%)
- RSI ≤ **65** (was 72)
- bullish body ratio ≥ **0.55**
- HTF momentum: `macdhist_4h > 0` **or** `is_bull_wave_4h`
- `dist_from_ema_200_1d` ≤ **0.45**
- soft `long_score` floor ≥ **50** when present
- plus existing vol / sideway / PDH / macro bull checks

## Post-filter diagnostic (`hunter_long=true` + gates)
| Profile | Trades | Absolute profit | Long hunter trades |
|---|---|---|---|
| `hunter_long=false` | 273 | **+24,282.26** | 0 |
| `hunter_long=true` + P4 gates | 273 | **+24,282.26** | **0** |

Gates reject the entire historical breakout-expansion set under these quality bars → enabling the flag is a **no-op** vs parity (no portfolio damage).

## Decision
- Keep production `enable_hunter_long=false`.
- If an operator enables it, P4 gates prevent the prior −702 / −18k regression.
- Revisit only with a new detector or relaxed config that proves **OOS expectancy &gt; 0** on ledger replay without cutting into A80+/waterfall edge.

## Files
- `strategies/mtf_sniper/directional/long/hunter_gates.py`
- `strategies/mtf_sniper/execution/hunter_merge.py`
- `tests/strategy/test_mtf_sniper_long_hunter_gates.py`
