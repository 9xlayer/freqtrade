"""
Setup recipes — which atomic sensor sets form a legacy sniper strategy.

Ownership: Directional Engine (not SensorSuite).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple

from strategies.mtf_sniper.directional.long.setups import LONG_SNIPER_PART_NAMES
from strategies.mtf_sniper.directional.short.setups import SHORT_SNIPER_PART_NAMES


SETUP_SNIPER_SHORT = "setup:sniper_short"
SETUP_SNIPER_LONG = "setup:sniper_long"


@dataclass(frozen=True)
class SetupRecipe:
    """Named ALL-of set of sensor names (legacy AND-mask parity)."""

    name: str
    required_sensors: FrozenSet[str]
    side: str  # "short" | "long"

    def is_satisfied(self, present: FrozenSet[str] | set[str]) -> bool:
        return self.required_sensors.issubset(present)


def default_sniper_recipes() -> Tuple[SetupRecipe, ...]:
    return (
        SetupRecipe(
            name=SETUP_SNIPER_SHORT,
            required_sensors=frozenset(SHORT_SNIPER_PART_NAMES),
            side="short",
        ),
        SetupRecipe(
            name=SETUP_SNIPER_LONG,
            required_sensors=frozenset(LONG_SNIPER_PART_NAMES),
            side="long",
        ),
    )


def recipes_by_side() -> Dict[str, SetupRecipe]:
    return {r.side: r for r in default_sniper_recipes()}
