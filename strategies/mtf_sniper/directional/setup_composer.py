"""
4a SetupComposer — combine atomic sensor facts into directional sub-system setups.

Sensors emit independent TriggerEvents; this layer matches registered recipes per
side so multiple sub-systems (short_sniper, long_sniper, waterfall, etc.) coexist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.candidate_id import (
    make_candidate_id,
    normalize_utc,
)
from strategies.mtf_sniper.core.contracts import (
    TradeCandidate,
    TriggerEvent,
)
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.directional.long.setups import LONG_SNIPER_PART_NAMES
from strategies.mtf_sniper.directional.setup_recipes import (
    SETUP_SNIPER_LONG,
    SETUP_SNIPER_SHORT,
    SETUP_WATERFALL_SHORT,
    SetupRecipe,
    all_live_recipes,
    recipes_grouped_by_side,
)
from strategies.mtf_sniper.directional.short.setups import SHORT_SNIPER_PART_NAMES


_ANOMALY_SENSORS: set[str] = {
    "volume_surge",
    "volatility_expansion",
    "waterfall_drop",
    "waterfall_momentum",
}


class SetupComposer:
    """Directional 4a: multi-subsystem recipe matcher on candidate sensors."""

    def __init__(
        self,
        recipes: Sequence[SetupRecipe] | None = None,
        *,
        drop_anomalies: bool = True,
    ) -> None:
        default_tuple = all_live_recipes(include_waterfall=not drop_anomalies)
        self._recipes = tuple(recipes) if recipes is not None else default_tuple
        self._recipes_by_side = recipes_grouped_by_side(self._recipes)
        self.drop_anomalies = drop_anomalies

    def compose(
        self,
        candidates: Sequence[TradeCandidate],
        *,
        dropped: list[dict] | None = None,
    ) -> list[TradeCandidate]:
        """Match candidates against all sub-system recipes registered for their side."""
        out: list[TradeCandidate] = []
        for candidate in candidates:
            sensors = _sensor_names(candidate)
            side_key = candidate.side.value.lower()
            side_recipes = self._recipes_by_side.get(side_key, ())

            matched = [r for r in side_recipes if r.is_satisfied(sensors)]
            if matched:
                for recipe in matched:
                    if recipe.is_anomaly and self.drop_anomalies:
                        continue
                    out.append(_mark_setup_complete(candidate, recipe))
                if any(not (r.is_anomaly and self.drop_anomalies) for r in matched):
                    continue

            is_anomaly = bool(sensors & _ANOMALY_SENSORS)
            if is_anomaly and not self.drop_anomalies:
                out.append(candidate)
                continue

            reason = "ANOMALY_DROPPED" if is_anomaly else "INCOMPLETE_SETUP"
            if dropped is not None:
                primary_req = side_recipes[0].required_sensors if side_recipes else frozenset()
                dropped.append(
                    {
                        "pair": candidate.pair,
                        "candle_index": candidate.candle_index,
                        "side": candidate.side.value,
                        "reason": reason,
                        "sensors": sorted(sensors),
                        "missing": sorted(primary_req - sensors) if primary_req else [],
                    }
                )
        return out

    def compose_suite(
        self,
        sensor_suite: Any,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candidate_builder: Any | None = None,
        dropped: list[dict] | None = None,
        trigger_events_out: list[TriggerEvent] | None = None,
    ) -> list[TradeCandidate]:
        """Compose registered setups from a SensorSuite via vectorized masks or event fallback."""
        sensors = list(sensor_suite.sensors)
        if not sensors:
            return []

        sensor_masks: dict[str, np.ndarray] = {}
        for sensor in sensors:
            mask = sensor.scan_mask(dataframe, pair)
            if mask is None:
                return self._compose_suite_via_events(
                    sensor_suite,
                    dataframe,
                    pair,
                    candidate_builder=candidate_builder,
                    dropped=dropped,
                    trigger_events_out=trigger_events_out,
                )
            sensor_masks[sensor.name] = np.asarray(mask, dtype=bool)

        return self._compose_from_masks(sensors, sensor_masks, dataframe, pair)

    def _compose_suite_via_events(
        self,
        sensor_suite: Any,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candidate_builder: Any | None = None,
        dropped: list[dict] | None = None,
        trigger_events_out: list[TriggerEvent] | None = None,
    ) -> list[TradeCandidate]:
        from strategies.mtf_sniper.signals.candidate_builder import CandidateBuilder

        events = sensor_suite.scan(dataframe, pair)
        if trigger_events_out is not None:
            trigger_events_out.extend(events)
        builder = (
            candidate_builder
            if candidate_builder is not None
            else CandidateBuilder(cooldown_bars=0)
        )
        builder.reset()
        raw_candidates = builder.process(events, rejected=dropped)
        return self.compose(raw_candidates, dropped=dropped)

    def _compose_from_masks(
        self,
        sensors: Sequence[Any],
        sensor_masks: dict[str, np.ndarray],
        dataframe: pd.DataFrame,
        pair: str,
    ) -> list[TradeCandidate]:
        sensors_by_name = {s.name: s for s in sensors}
        n = len(dataframe)
        active_recipes = [
            r for r in self._recipes if not (r.is_anomaly and self.drop_anomalies)
        ]

        recipe_masks: dict[str, np.ndarray] = {}
        recipe_by_name: dict[str, SetupRecipe] = {}
        for recipe in active_recipes:
            if not recipe.required_sensors.issubset(sensor_masks):
                continue
            combined = np.ones(n, dtype=bool)
            for req in sorted(recipe.required_sensors):
                combined &= sensor_masks[req]
            recipe_masks[recipe.name] = combined
            recipe_by_name[recipe.name] = recipe

        short_mask = recipe_masks.get(SETUP_SNIPER_SHORT)
        long_mask = recipe_masks.get(SETUP_SNIPER_LONG)
        if short_mask is not None and long_mask is not None:
            both = short_mask & long_mask
            if both.any():
                recipe_masks[SETUP_SNIPER_SHORT] = short_mask & ~both
                recipe_masks[SETUP_SNIPER_LONG] = long_mask & ~both

        has_date = "date" in dataframe.columns
        default_ts = normalize_utc(datetime(1970, 1, 1, tzinfo=UTC))
        sortable: list[tuple[int, TradeCandidate]] = []

        for recipe_name, mask in recipe_masks.items():
            hits = np.flatnonzero(mask)
            if len(hits) == 0:
                continue
            recipe = recipe_by_name[recipe_name]
            ordered_parts = _ordered_recipe_parts(recipe)
            extra_by_sensor = {
                req: sensors_by_name[req].scan_mask_features(dataframe, hits)
                for req in ordered_parts
                if req in sensors_by_name
            }
            for idx in hits:
                i = int(idx)
                ts = normalize_utc(dataframe.iloc[i]["date"]) if has_date else default_ts
                cand = _build_vectorized_candidate(
                    recipe=recipe,
                    ordered_parts=ordered_parts,
                    extra_by_sensor=extra_by_sensor,
                    dataframe=dataframe,
                    pair=pair,
                    candle_index=i,
                    timestamp=ts,
                )
                sortable.append((recipe.priority, cand))

        sortable.sort(key=lambda item: (item[1].candle_index, item[1].side.value, item[0]))
        candidates = [item[1] for item in sortable]
        return _attach_suite_context(
            candidates,
            [s for s in sensors if s.sensor_type == SensorType.CONTEXT],
            sensor_masks,
            dataframe,
        )


def _attach_suite_context(
    candidates: list[TradeCandidate],
    context_sensors: Sequence[Any],
    sensor_masks: dict[str, np.ndarray],
    dataframe: pd.DataFrame,
) -> list[TradeCandidate]:
    if not candidates or not context_sensors:
        return candidates

    hit_idx_set = sorted({c.candle_index for c in candidates})
    context_by_idx: dict[int, dict[str, Any]] = {}
    ctx_names_by_idx: dict[int, list[str]] = {}
    for sensor in context_sensors:
        mask = sensor_masks.get(sensor.name)
        if mask is None:
            continue
        active = np.asarray([i for i in hit_idx_set if mask[i]], dtype=int)
        if len(active) == 0:
            continue
        feats_map = sensor.scan_mask_features(dataframe, active)
        for i, feats in feats_map.items():
            context_by_idx.setdefault(i, {}).update(feats)
            names = ctx_names_by_idx.setdefault(i, [])
            if sensor.name not in names:
                names.append(sensor.name)

    if not context_by_idx:
        return candidates

    enriched: list[TradeCandidate] = []
    for cand in candidates:
        ctx = context_by_idx.get(cand.candle_index)
        if not ctx:
            enriched.append(cand)
            continue
        merged = dict(cand.raw_features)
        merged.update(ctx)
        merged["context_sensors"] = list(ctx_names_by_idx.get(cand.candle_index, []))
        enriched.append(replace(cand, raw_features=merged))
    return enriched


def _ordered_recipe_parts(recipe: SetupRecipe) -> list[str]:
    if recipe.name == SETUP_SNIPER_SHORT:
        return list(SHORT_SNIPER_PART_NAMES)
    if recipe.name == SETUP_SNIPER_LONG:
        return list(LONG_SNIPER_PART_NAMES)
    return sorted(recipe.required_sensors)


def _build_vectorized_candidate(
    *,
    recipe: SetupRecipe,
    ordered_parts: list[str],
    extra_by_sensor: dict[str, dict[int, dict[str, Any]]],
    dataframe: pd.DataFrame,
    pair: str,
    candle_index: int,
    timestamp: datetime,
) -> TradeCandidate:
    side = TradeSide.LONG if recipe.side == "long" else TradeSide.SHORT
    feats: dict[str, Any] = {}
    for req in ordered_parts:
        part_feats = extra_by_sensor.get(req, {}).get(candle_index)
        if part_feats:
            feats.update(part_feats)

    feats["subsystem"] = recipe.subsystem or recipe.name
    feats["recipe_sensors"] = list(ordered_parts)
    feats["sensors"] = list(ordered_parts)

    if recipe.name == SETUP_WATERFALL_SHORT:
        feats.setdefault("setup_name", "waterfall_collapse")
        feats.setdefault("signal_source", "momentum_radar")
        return TradeCandidate(
            candidate_id=make_candidate_id(pair, side, timestamp, candle_index),
            pair=pair,
            side=side,
            primary_sensor="waterfall_drop",
            secondary_sensors=[],
            confluence_count=1,
            timestamp=timestamp,
            candle_index=candle_index,
            raw_features=feats,
            cooldown_bars=0,
        )

    score_key = "long_score" if side == TradeSide.LONG else "short_score"
    feats.setdefault(score_key, float(dataframe.iloc[candle_index].get(score_key, 0.0) or 0.0))
    feats["setup_complete"] = True
    feats["setup_name"] = recipe.name
    feats["legacy_sniper"] = recipe.side
    feats["mask"] = f"legacy_and_{recipe.side}"
    feats["signal_source"] = "vectorized_compose"
    return TradeCandidate(
        candidate_id=make_candidate_id(pair, side, timestamp, candle_index),
        pair=pair,
        side=side,
        primary_sensor=recipe.name,
        secondary_sensors=list(ordered_parts),
        confluence_count=len(ordered_parts),
        timestamp=timestamp,
        candle_index=candle_index,
        raw_features=feats,
        cooldown_bars=0,
    )


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
    """Normalize composed candidate while preserving sub-system lineage."""
    if recipe.name == SETUP_SNIPER_SHORT:
        ordered_parts = list(SHORT_SNIPER_PART_NAMES)
    elif recipe.name == SETUP_SNIPER_LONG:
        ordered_parts = list(LONG_SNIPER_PART_NAMES)
    else:
        ordered_parts = sorted(recipe.required_sensors)

    feats = dict(candidate.raw_features or {})
    feats["subsystem"] = recipe.subsystem or recipe.name
    feats["setup_name"] = recipe.name
    feats["recipe_sensors"] = list(ordered_parts)
    feats["sensors"] = list(ordered_parts)
    feats.setdefault("signal_source", "event_scan")

    if recipe.name == SETUP_WATERFALL_SHORT:
        return replace(
            candidate,
            primary_sensor="waterfall_drop",
            secondary_sensors=list(ordered_parts),
            confluence_count=len(ordered_parts),
            raw_features=feats,
        )

    feats["setup_complete"] = True
    feats["legacy_sniper"] = recipe.side
    feats["mask"] = f"legacy_and_{recipe.side}"
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
