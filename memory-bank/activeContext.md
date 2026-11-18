# Active Context

## Current focus
**v4.1.2 scoring package** — `CandidateScorer` tách riêng: candidate in → pass/fail → chỉ PASS mới vào JEV.

## Live path
```
soft-reject → scoring.CandidateScorer.evaluate(candidate) → ScoreVerdict
  FAIL → drop (no AI)
  PASS → JEV Hunter → promote?
→ cooldown → JEV Decision + Risk
```

## Manage scoring here
- Package: `strategies/mtf_sniper/scoring/`
- API: `CandidateScorer` / `ScoreConfig` / `ScoreVerdict`
- Threshold: `ScoreConfig.pass_threshold` (default 0.55)
