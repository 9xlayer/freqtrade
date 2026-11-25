"""
Setup recipes and sub-system registry — mapping atomic sensors to sub-systems.

Ownership: Directional Engine (not SensorSuite).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.directional.long.setups import LONG_SNIPER_PART_NAMES
from strategies.mtf_sniper.directional.short.setups import SHORT_SNIPER_PART_NAMES


SETUP_SNIPER_SHORT = "setup:sniper_short"
SETUP_SNIPER_LONG = "setup:sniper_long"
SETUP_WATERFALL_SHORT = "setup:waterfall_short"


@runtime_checkable
class DirectionalPolicyProtocol(Protocol):
    """Unified evaluation interface every directional policy must satisfy."""

    def evaluate(
        self,
        candidate: TradeCandidate,
        row: Mapping[str, Any],
    ) -> PolicyEvaluationResult: ...


@dataclass(frozen=True)
class SetupRecipe:
    """Named sensor recipe representing one directional sub-system entry condition."""

    name: str
    required_sensors: frozenset[str]
    side: str
    subsystem: str = ""
    is_anomaly: bool = False
    priority: int = 100

    def is_satisfied(self, present: frozenset[str] | set[str]) -> bool:
        return self.required_sensors.issubset(present)


@dataclass(frozen=True)
class SubSystemSpec:
    """Authoritative metadata for each directional sub-system."""

    subsystem_id: str
    side: str
    recipe_name: str
    entry_tag_prefix: str
    policy_module: str
    enabled_by_default: bool = True


SUBSYSTEM_REGISTRY: tuple[SubSystemSpec, ...] = (
    SubSystemSpec(
        subsystem_id="short_sniper",
        side="short",
        recipe_name=SETUP_SNIPER_SHORT,
        entry_tag_prefix="short_",
        policy_module="strategies.mtf_sniper.directional.short.policy.ShortPolicy",
        enabled_by_default=True,
    ),
    SubSystemSpec(
        subsystem_id="long_sniper",
        side="long",
        recipe_name=SETUP_SNIPER_LONG,
        entry_tag_prefix="long_",
        policy_module="strategies.mtf_sniper.directional.long.policy.LongPolicy",
        enabled_by_default=True,
    ),
    SubSystemSpec(
        subsystem_id="waterfall",
        side="short",
        recipe_name=SETUP_WATERFALL_SHORT,
        entry_tag_prefix="jev_hunter_waterfall_collapse",
        policy_module=(
            "strategies.mtf_sniper.directional.short.waterfall_policy.WaterfallShortPolicy"
        ),
        enabled_by_default=True,
    ),
)


def default_sniper_recipes() -> tuple[SetupRecipe, ...]:
    return (
        SetupRecipe(
            name=SETUP_SNIPER_SHORT,
            required_sensors=frozenset(SHORT_SNIPER_PART_NAMES),
            side="short",
            subsystem="short_sniper",
            priority=10,
        ),
        SetupRecipe(
            name=SETUP_SNIPER_LONG,
            required_sensors=frozenset(LONG_SNIPER_PART_NAMES),
            side="long",
            subsystem="long_sniper",
            priority=10,
        ),
    )


def default_waterfall_recipes() -> tuple[SetupRecipe, ...]:
    return (
        SetupRecipe(
            name=SETUP_WATERFALL_SHORT,
            required_sensors=frozenset({"waterfall_momentum"}),
            side="short",
            subsystem="waterfall",
            is_anomaly=True,
            priority=20,
        ),
    )


def all_live_recipes(*, include_waterfall: bool = False) -> tuple[SetupRecipe, ...]:
    base = default_sniper_recipes()
    if include_waterfall:
        return (*base, *default_waterfall_recipes())
    return base


def recipes_grouped_by_side(
    recipes: Sequence[SetupRecipe] | None = None,
) -> dict[str, tuple[SetupRecipe, ...]]:
    """Group multiple sub-system recipes per side ordered by ascending priority."""
    source = tuple(recipes) if recipes is not None else default_sniper_recipes()
    grouped: dict[str, list[SetupRecipe]] = {}
    for recipe in sorted(source, key=lambda r: (r.priority, r.name)):
        grouped.setdefault(recipe.side.lower(), []).append(recipe)
    return {side: tuple(items) for side, items in grouped.items()}


def recipes_by_side() -> dict[str, SetupRecipe]:
    """Primary sniper recipe lookup per side (legacy helper for single-mask callers)."""
    return {r.side: r for r in default_sniper_recipes()}
