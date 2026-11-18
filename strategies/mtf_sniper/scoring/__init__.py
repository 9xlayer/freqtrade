"""
Candidate scoring subsystem (deterministic, no AI).

Public API:
  CandidateScorer.evaluate(candidate, policy_result, row) -> ScoreVerdict
  - verdict.passed True  → caller may send candidate to JEV Hunter
  - verdict.passed False → drop (do not call JEV)
"""
from strategies.mtf_sniper.scoring.candidate_scorer import (
    CandidateScorer,
    ScoreConfig,
    ScoreVerdict,
)

__all__ = ["CandidateScorer", "ScoreConfig", "ScoreVerdict"]
