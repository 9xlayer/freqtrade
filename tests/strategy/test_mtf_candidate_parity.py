"""Phase 2 — fast-path and builder candidate IDs must share the same contract."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pandas as pd

from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.signals.candidate_builder import CandidateBuilder


class TestCandidateIdContract(unittest.TestCase):
    def test_normalize_naive_to_utc(self):
        ts = normalize_utc(datetime(2024, 1, 1, 12, 0, 0))
        self.assertEqual(ts.tzinfo, timezone.utc)

    def test_make_candidate_id_stable(self):
        ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        a = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        b = make_candidate_id("BTC/USDT:USDT", "short", ts, 42)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("BTC/USDT:USDT_short_"))

    def test_builder_uses_canonical_id(self):
        ts = datetime(2024, 6, 1, 15, 0, tzinfo=timezone.utc)
        events = [
            TriggerEvent(
                sensor_name="short_structure",
                sensor_type=SensorType.PATTERN,
                side=TradeSide.SHORT,
                pair="BTC/USDT:USDT",
                timestamp=ts,
                candle_index=10,
                raw_metrics={"part": "structure"},
            ),
            TriggerEvent(
                sensor_name="short_mtf",
                sensor_type=SensorType.PATTERN,
                side=TradeSide.SHORT,
                pair="BTC/USDT:USDT",
                timestamp=ts,
                candle_index=10,
                raw_metrics={"part": "mtf"},
            ),
        ]
        # Incomplete recipe → still builds candidate (composer drops later)
        builder = CandidateBuilder(cooldown_bars=0)
        cands = builder.process(events)
        self.assertEqual(len(cands), 1)
        expected = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 10)
        self.assertEqual(cands[0].candidate_id, expected)
        self.assertEqual(cands[0].timestamp.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
