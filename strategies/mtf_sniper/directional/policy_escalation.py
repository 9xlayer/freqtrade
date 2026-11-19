"""
Classify policy rejects into hard (drop) vs soft (eligible for JEV Hunter).

Hard = structural / regime / data integrity — never escalate.
Soft = microstructure / chase / score near-miss — Hunter may promote.
"""

from __future__ import annotations

from collections.abc import Iterable

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult
from strategies.mtf_sniper.core.enums import RejectionReasonCode


# Fail-closed: never ask AI to override these.
HARD_REJECT_REASONS: frozenset[str] = frozenset(
    {
        RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value,
        RejectionReasonCode.INSUFFICIENT_HISTORY.value,
        RejectionReasonCode.COOLDOWN_ACTIVE.value,
        RejectionReasonCode.CONTRADICTORY_SIGNALS.value,
        "wrong_side",
    }
)

# Near-miss quality / candle shape — eligible for Hunter escalation.
SOFT_REJECT_REASONS: frozenset[str] = frozenset(
    {
        RejectionReasonCode.REJECT_UPPER_WICK_REJECTION.value,
        RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value,
        RejectionReasonCode.REJECT_CLOSE_POSITION_WEAK.value,
        RejectionReasonCode.REJECT_RSI_OVEREXTENDED.value,
        RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value,
        RejectionReasonCode.REJECT_SCORE_BELOW_THRESHOLD.value,
    }
)


def _normalize_reasons(reasons: Iterable[str]) -> set[str]:
    return {r for r in reasons if r and r != "ACCEPT"}


def is_soft_policy_reject(result: PolicyEvaluationResult) -> bool:
    """
    True when the candidate failed policy but only on soft reasons.

    Any hard reason (or unknown reason) => not escalatable (fail-closed).
    """
    if result.is_valid:
        return False
    reasons = _normalize_reasons(result.reasons)
    if not reasons:
        return False
    if reasons & HARD_REJECT_REASONS:
        return False
    unknown = reasons - SOFT_REJECT_REASONS - HARD_REJECT_REASONS
    if unknown:
        return False
    return bool(reasons & SOFT_REJECT_REASONS)
