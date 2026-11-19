"""
4a SetupComposer — combine fused sensor facts into sniper setups.

Sensors emit singles; this layer matches recipes (ALL parts = legacy AND-mask).
Incomplete part-only candidates are dropped. Anomaly candidates are dropped
(deterministic phase — anomalies not on live suite).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from strategies.mtf_sniper.core.contracts import TradeCandidate
from strategies.mtf_sniper.directional.long.setups import LONG_SNIPER_PART_NAMES
from strategies.mtf_sniper.directional.setup_recipes import (
    SETUP_SNIPER_LONG,
    SETUP_SNIPER_SHORT,
    SetupRecipe,
    default_sniper_recipes,
    recipes_by_side,
)
from strategies.mtf_sniper.directional.short.setups import SHORT_SNIPER_PART_NAMES


_ALL_PARTS: set[str] = set(SHORT_SNIPER_PART_NAMES) | set(LONG_SNIPER_PART_NAMES)
_ANOMALY_SENSORS: set[str] = {
    "volume_surge",
    "volatility_expansion",
    "waterfall_drop",
}


class SetupComposer:
    """Directional 4a: recipe matcher on candidate.sensors."""

    def __init__(
        self,
        recipes: Sequence[SetupRecipe] | None = None,
        *,
        drop_anomalies: bool = True,
    ) -> None:
        self._recipes = tuple(recipes) if recipes is not None else default_sniper_recipes()
        self._by_side = {r.side: r for r in self._recipes} if recipes else recipes_by_side()
        # False when Hunter escalation is wired (soft anomaly rejects may promote).
        self.drop_anomalies = drop_anomalies

    def compose(
        self,
        candidates: Sequence[TradeCandidate],
        *,
        dropped: list[dict] | None = None,
    ) -> list[TradeCandidate]:
        """
        Annotate complete sniper setups; drop incomplete part-only candidates.

        Mutates raw_features via replace (frozen TradeCandidate).
        """
        out: list[TradeCandidate] = []
        for candidate in candidates:
            sensors = _sensor_names(candidate)
            side_key = candidate.side.value.lower()
            recipe = self._by_side.get(side_key)

            if recipe is not None and recipe.is_satisfied(sensors):
                out.append(_mark_setup_complete(candidate, recipe))
                continue

            is_anomaly = bool(sensors & _ANOMALY_SENSORS)
            if is_anomaly and not self.drop_anomalies:
                out.append(candidate)
                continue

            # Incomplete parts / anomaly / anything not a full recipe → drop
            reason = "ANOMALY_DROPPED" if is_anomaly else "INCOMPLETE_SETUP"
            if dropped is not None:
                dropped.append(
                    {
                        "pair": candidate.pair,
                        "candle_index": candidate.candle_index,
                        "side": candidate.side.value,
                        "reason": reason,
                        "sensors": sorted(sensors),
                        "missing": sorted(recipe.required_sensors - sensors) if recipe else [],
                    }
                )
        return out


def _sensor_names(candidate: TradeCandidate) -> set[str]:
    feats = candidate.raw_features or {}
    names: set[str] = set()
    for key in ("sensors", "composite_children"):
        for n in feats.get(key) or []:
            if isinstance(n, str):
                names.add(n)
    if candidate.primary_sensor:
        names.add(candidate.primary_sensor)
    for n in candidate.secondary_sensors or []:
        names.add(n)
    return names


def _mark_setup_complete(candidate: TradeCandidate, recipe: SetupRecipe) -> TradeCandidate:
    """Normalize composed candidate to match vectorized AND-path semantics."""
    # Stable recipe order (same as EntryPipeline._candidates_for_mask part_names)
    if recipe.name == SETUP_SNIPER_SHORT:
        ordered_parts = list(SHORT_SNIPER_PART_NAMES)
    elif recipe.name == SETUP_SNIPER_LONG:
        ordered_parts = list(LONG_SNIPER_PART_NAMES)
    else:
        ordered_parts = sorted(recipe.required_sensors)

    feats = dict(candidate.raw_features or {})
    feats["setup_complete"] = True
    feats["setup_name"] = recipe.name
    feats["legacy_sniper"] = recipe.side
    feats["mask"] = f"legacy_and_{recipe.side}"
    feats["recipe_sensors"] = list(ordered_parts)
    feats["sensors"] = list(ordered_parts)
    # Provenance: event path (fast path sets vectorized_and itself)
    feats.setdefault("signal_source", "event_scan")
    return replace(
        candidate,
        primary_sensor=recipe.name,
        secondary_sensors=list(ordered_parts),
        confluence_count=len(ordered_parts),
        raw_features=feats,
    )


def is_setup_complete(primary: str, raw_features: dict) -> bool:
    """Policy helper: True when 4a marked a full sniper recipe."""
    if raw_features.get("setup_complete") is True:
        return True
    return primary in (SETUP_SNIPER_SHORT, SETUP_SNIPER_LONG)
