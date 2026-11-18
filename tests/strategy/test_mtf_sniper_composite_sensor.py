"""Unit tests for flexible CompositeSensor composition."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.base_sensor import BaseSensor
from strategies.mtf_sniper.signals.composite_catalog import example_hunter_strict_short
from strategies.mtf_sniper.signals.composite_sensor import (
    CombineMode,
    CompositeSensor,
    candidate_includes_sensor,
    compose,
    is_legacy_sniper_pass,
)


class _AlwaysShort(BaseSensor):
    name = "always_short"
    sensor_type = SensorType.ANOMALY
    min_history = 5

    def scan(self, dataframe, pair, *, candle_index=None):
        indices = self._resolve_indices(dataframe, candle_index)
        return [
            self._make_event(
                side=TradeSide.SHORT,
                pair=pair,
                timestamp=self._timestamp_at(dataframe, i),
                candle_index=i,
                raw_metrics={"src": self.name},
            )
            for i in indices
        ]


class _Never(BaseSensor):
    name = "never"
    sensor_type = SensorType.ANOMALY
    min_history = 5

    def scan(self, dataframe, pair, *, candle_index=None):
        return []


class _OddBarsShort(BaseSensor):
    name = "odd_bars"
    sensor_type = SensorType.PATTERN
    min_history = 5

    def scan(self, dataframe, pair, *, candle_index=None):
        indices = [i for i in self._resolve_indices(dataframe, candle_index) if i % 2 == 1]
        return [
            self._make_event(
                side=TradeSide.SHORT,
                pair=pair,
                timestamp=self._timestamp_at(dataframe, i),
                candle_index=i,
                raw_metrics={"odd": True},
            )
            for i in indices
        ]


def _df(n: int = 12) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
            "close": np.full(n, 100.0),
        }
    )


class TestCompositeSensor(unittest.TestCase):
    def test_all_requires_every_child(self):
        combo = compose("c_all", _AlwaysShort(), _Never(), mode=CombineMode.ALL)
        self.assertEqual(combo.scan(_df(), "BTC/USDT:USDT"), [])

    def test_all_passes_when_both_fire(self):
        combo = compose(
            "c_all",
            _AlwaysShort(),
            _OddBarsShort(),
            mode=CombineMode.ALL,
            side=TradeSide.SHORT,
        )
        events = combo.scan(_df(12), "BTC/USDT:USDT")
        idxs = {e.candle_index for e in events}
        self.assertTrue(idxs)
        self.assertTrue(all(i % 2 == 1 for i in idxs))
        self.assertEqual(events[0].sensor_name, "c_all")
        self.assertTrue(events[0].raw_metrics["composite"])
        self.assertIn("always_short", events[0].raw_metrics["composite_children"])
        self.assertIn("odd_bars", events[0].raw_metrics["composite_children"])

    def test_any_mode(self):
        combo = compose("c_any", _AlwaysShort(), _Never(), mode=CombineMode.ANY)
        events = combo.scan(_df(10), "BTC/USDT:USDT")
        self.assertGreaterEqual(len(events), 1)

    def test_k_of_n(self):
        combo = CompositeSensor(
            name="c_k",
            children=[_AlwaysShort(), _Never(), _OddBarsShort()],
            mode=CombineMode.K_OF_N,
            k=2,
        )
        events = combo.scan(_df(12), "BTC/USDT:USDT")
        self.assertTrue(all(e.candle_index % 2 == 1 for e in events))

    def test_candidate_includes_sensor_helper(self):
        self.assertTrue(
            candidate_includes_sensor(
                "setup:sniper_short",
                {"sensors": ["short_structure", "short_mtf"]},
                "short_structure",
            )
        )
        self.assertFalse(
            candidate_includes_sensor(
                "setup:sniper_short",
                {"sensors": ["volume_surge"]},
                "short_structure",
            )
        )

    def test_is_legacy_sniper_pass_requires_setup_complete(self):
        self.assertTrue(
            is_legacy_sniper_pass(
                "setup:sniper_short",
                {"setup_complete": True, "setup_name": "setup:sniper_short", "legacy_sniper": "short"},
                "short",
            )
        )
        self.assertTrue(
            is_legacy_sniper_pass(
                "setup:sniper_long",
                {"setup_complete": True, "legacy_sniper": "long"},
                "long",
            )
        )
        # Partial parts alone are NOT a pass
        self.assertFalse(
            is_legacy_sniper_pass(
                "short_structure",
                {"sensors": ["short_structure", "short_mtf"], "legacy_sniper": "short"},
                "short",
            )
        )
        self.assertFalse(
            is_legacy_sniper_pass(
                "composite_hunter_strict_short",
                {"composite_children": ["volume_surge", "waterfall_drop"]},
                "short",
            )
        )

    def test_example_catalog_builds_without_editing_atomics(self):
        sensor = example_hunter_strict_short()
        self.assertEqual(sensor.name, "composite_hunter_strict_short")
        self.assertEqual(len(sensor.children), 2)


if __name__ == "__main__":
    unittest.main()
