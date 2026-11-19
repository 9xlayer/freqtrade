"""Stake and leverage helpers (parity with active StrategyMTFSniper)."""

from __future__ import annotations


DEFAULT_LEVERAGE = 10.0

_TAG_MULTIPLIERS = (
    ("long_A80_plus", 1.10),
    ("long_A80plus", 1.10),
    ("short_A80_plus", 1.10),
    ("short_A80plus", 1.10),
    ("short_B65_plus", 1.00),
    ("short_B65plus", 1.00),
    ("short_C_sub65", 0.85),
    ("jev_hunter", 1.00),
)


def resolve_leverage(max_leverage: float | None, target: float = DEFAULT_LEVERAGE) -> float:
    if max_leverage:
        return min(target, float(max_leverage))
    return target


def resolve_stake_amount(
    *,
    proposed_stake: float,
    max_stake: float,
    entry_tag: str | None,
    enable_dynamic_sizing: bool,
) -> float:
    """
    Flat 1.0x by default. When dynamic sizing is on, apply tag multipliers
    then cap at max_stake.
    """
    if not enable_dynamic_sizing:
        return min(proposed_stake, max_stake)

    multiplier = 1.0
    if entry_tag:
        for needle, mult in _TAG_MULTIPLIERS:
            if needle in entry_tag:
                multiplier = mult
                break
    return min(proposed_stake * multiplier, max_stake)
