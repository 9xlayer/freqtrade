# Progress

## What works
- SensorSuite live + **`scoring.CandidateScorer`** (pass/fail) + Hunter escalate (`v4.1.2`).
- Scorer tách package riêng: candidate in → `ScoreVerdict.passed` → chỉ PASS mới JEV.
- Decision Gate vẫn trên confirm.

## Gaps
- `ScoreConfig.pass_threshold` (0.55) chưa calibrate cost/PnL.
- Full 5y baseline vẫn pending.

## Do not regress
- Score FAIL / hard reject không gọi Hunter
- Builder không gọi JEV
- `PYTHONPATH=. --strategy-path strategies/mtf_sniper`
