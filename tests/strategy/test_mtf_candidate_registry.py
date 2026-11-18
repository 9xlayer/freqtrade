"""Candidate ID lifecycle: remember → confirm lookup → trade bind → exit unbind."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from strategies.mtf_sniper.core.candidate_id import make_candidate_id
from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry
from strategies.mtf_sniper.core.contracts import TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide


class TestCandidateRegistryLifecycle(unittest.TestCase):
    def test_remember_lookup_bind_unbind(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        cand = TradeCandidate(
            candidate_id=cid,
            pair="BTC/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="setup:sniper_short",
            secondary_sensors=[],
            confluence_count=4,
            timestamp=ts,
            candle_index=42,
            raw_features={},
        )
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80plus")
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=ts,
            ),
            cid,
        )
        reg.bind_trade(7, cid)
        self.assertEqual(reg.lookup_trade(7), cid)
        reg.unbind_trade(7)
        self.assertIsNone(reg.lookup_trade(7))


if __name__ == "__main__":
    unittest.main()
