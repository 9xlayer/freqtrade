"""
Composable sensors — build complex detectors from existing BaseSensor units.

Open/Closed: add composites in a catalog; do not edit atomic sensor modules.
Composite still obeys BaseSensor invariants (detect-only, no enter_*/size/JEV).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from enum import Enum

import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor


class CombineMode(str, Enum):
    """How child emissions on the same (candle_index, side) are combined."""

    ALL = "all"  # every child must fire
    ANY = "any"  # at least one child fires
    K_OF_N = "k_of_n"  # at least k distinct children fire


class CompositeSensor(BaseSensor):
    """
    Complex sensor = composition of atomic (or nested composite) sensors.

    Example::

        CompositeSensor(
            name="strict_waterfall",
            children=[MarketRegimeSensor(), WaterfallMomentumSensor()],
            mode=CombineMode.ALL,
            side=TradeSide.SHORT,
        )
    """

    def __init__(
        self,
        *,
        name: str,
        children: Sequence[BaseSensor],
        mode: CombineMode = CombineMode.ALL,
        k: int = 2,
        sensor_type: SensorType = SensorType.PATTERN,
        side: TradeSide | None = None,
        min_history: int | None = None,
    ) -> None:
        if not children:
            raise ValueError(f"CompositeSensor '{name}' requires at least one child")
        if mode == CombineMode.K_OF_N and (k < 1 or k > len(children)):
            raise ValueError(
                f"CompositeSensor '{name}': k={k} invalid for {len(children)} children"
            )

        self.name = name
        self.sensor_type = sensor_type
        self._children: tuple[BaseSensor, ...] = tuple(children)
        self._mode = mode if isinstance(mode, CombineMode) else CombineMode(str(mode))
        self._k = k
        self._side_filter = side
        self.min_history = (
            min_history
            if min_history is not None
            else max(getattr(c, "min_history", 100) for c in self._children)
        )

    @property
    def children(self) -> tuple[BaseSensor, ...]:
        return self._children

    @property
    def mode(self) -> CombineMode:
        return self._mode

    def scan(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        *,
        candle_index: int | None = None,
    ) -> list[TriggerEvent]:
        # Child → events on this scan window
        child_hits: dict[tuple[int, TradeSide], dict[str, TriggerEvent]] = defaultdict(dict)

        for child in self._children:
            for event in child.scan(dataframe, pair, candle_index=candle_index):
                if event.side is None:
                    continue
                if self._side_filter is not None and event.side != self._side_filter:
                    continue
                key = (event.candle_index, event.side)
                # one hit per child name per bar/side
                child_hits[key][child.name] = event

        child_names = [c.name for c in self._children]
        n_children = len(child_names)
        out: list[TriggerEvent] = []

        for (idx, side), hits in sorted(child_hits.items(), key=lambda kv: kv[0][0]):
            hit_count = len(hits)
            if self._mode == CombineMode.ALL and hit_count < n_children:
                continue
            if self._mode == CombineMode.ANY and hit_count < 1:
                continue
            if self._mode == CombineMode.K_OF_N and hit_count < self._k:
                continue

            # Prefer PATTERN child metrics order; merge all
            merged: dict = {
                "composite": True,
                "composite_name": self.name,
                "composite_mode": self._mode.value,
                "composite_k": self._k if self._mode == CombineMode.K_OF_N else None,
                "composite_children": list(hits.keys()),
                "composite_child_types": {name: hits[name].sensor_type.value for name in hits},
            }
            for name in child_names:
                if name in hits:
                    merged.update(hits[name].raw_metrics)

            # Anchor timestamp from first child in registration order that hit
            anchor = next(hits[n] for n in child_names if n in hits)
            out.append(
                self._make_event(
                    side=side,
                    pair=pair,
                    timestamp=anchor.timestamp,
                    candle_index=idx,
                    raw_metrics=merged,
                )
            )
        return out


def compose(
    name: str,
    *children: BaseSensor,
    mode: CombineMode | str = CombineMode.ALL,
    k: int = 2,
    sensor_type: SensorType = SensorType.PATTERN,
    side: TradeSide | None = None,
) -> CompositeSensor:
    """Factory: compose(name, sensor_a, sensor_b, mode=ALL)."""
    return CompositeSensor(
        name=name,
        children=list(children),
        mode=CombineMode(mode) if not isinstance(mode, CombineMode) else mode,
        k=k,
        sensor_type=sensor_type,
        side=side,
    )


def candidate_includes_sensor(primary: str, raw_features: dict, sensor_name: str) -> bool:
    """True if candidate primary or composite children include ``sensor_name``."""
    if primary == sensor_name:
        return True
    children = raw_features.get("composite_children") or []
    if sensor_name in children:
        return True
    sensors = raw_features.get("sensors") or []
    return sensor_name in sensors


def is_legacy_sniper_pass(primary: str, raw_features: dict, side: str) -> bool:
    """
    True when 4a marked a complete sniper recipe.

    Partial part sensors alone are NOT a pass.
    """
    side = side.lower()
    expected = f"setup:sniper_{side}"
    if primary == expected:
        return True
    return bool(
        raw_features.get("setup_complete")
        and (
            raw_features.get("setup_name") == expected
            or raw_features.get("legacy_sniper") == side
        )
    )
