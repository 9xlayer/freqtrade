"""Candidate ID lifecycle: remember → confirm lookup → trade bind → exit unbind."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from strategies.mtf_sniper.core.candidate_id import make_candidate_id
from strategies.mtf_sniper.core.candidate_registry import (
    DEFAULT_LOOKUP_MAX_AGE,
    CandidateRegistry,
)
from strategies.mtf_sniper.core.contracts import TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide


def _cand(ts: datetime, candle_index: int, pair: str = "BTC/USDT:USDT") -> TradeCandidate:
    cid = make_candidate_id(pair, TradeSide.SHORT, ts, candle_index)
    return TradeCandidate(
        candidate_id=cid,
        pair=pair,
        side=TradeSide.SHORT,
        primary_sensor="setup:sniper_short",
        secondary_sensors=[],
        confluence_count=4,
        timestamp=ts,
        candle_index=candle_index,
        raw_features={},
    )


class TestCandidateRegistryLifecycle(unittest.TestCase):
    def test_remember_lookup_bind_unbind(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cand = _cand(ts, 42)
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80plus")
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=ts,
            ),
            cand.candidate_id,
        )
        reg.bind_trade(7, cand.candidate_id)
        self.assertEqual(reg.lookup_trade(7), cand.candidate_id)
        reg.unbind_trade(7)
        self.assertIsNone(reg.lookup_trade(7))

    def test_lookup_before_candidate_returns_none(self):
        """current_time earlier than all candidates → None (no future fallback)."""
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cand = _cand(ts, 10)
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80plus")
        earlier = ts - timedelta(minutes=15)
        self.assertIsNone(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=earlier,
            )
        )

    def test_lookup_equal_timestamp_matches(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cand = _cand(ts, 10)
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80plus")
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=ts,
            ),
            cand.candidate_id,
        )

    def test_lookup_after_picks_nearest_not_future(self):
        t0 = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        t2 = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)
        c0, c1, c2 = _cand(t0, 1), _cand(t1, 2), _cand(t2, 3)
        reg = CandidateRegistry()
        for c in (c0, c1, c2):
            reg.remember(c, "short_A80plus")
        # At t1: nearest <= t1 is c1 (not future c2, not stale preference for c0)
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=t1,
            ),
            c1.candidate_id,
        )
        # Between t1 and t2 still c1
        mid = t1 + timedelta(hours=1)
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=mid,
            ),
            c1.candidate_id,
        )

    def test_lookup_rejects_stale_outside_max_age(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cand = _cand(ts, 5)
        reg = CandidateRegistry(lookup_max_age=timedelta(hours=1))
        reg.remember(cand, "short_A80plus")
        # Outside 1h window → None
        self.assertIsNone(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=ts + timedelta(hours=2),
            )
        )
        # Within 1h still OK
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=ts + timedelta(minutes=30),
            ),
            cand.candidate_id,
        )
        # Default window still accepts ages under DEFAULT_LOOKUP_MAX_AGE
        reg_default = CandidateRegistry()
        reg_default.remember(cand, "short_A80plus")
        almost_stale = ts + DEFAULT_LOOKUP_MAX_AGE - timedelta(minutes=1)
        self.assertEqual(
            reg_default.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=almost_stale,
            ),
            cand.candidate_id,
        )

    def test_lookup_without_current_time_fail_closed(self):
        t0 = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        c0, c1 = _cand(t0, 1), _cand(t1, 2)
        reg = CandidateRegistry()
        reg.remember(c0, "short_A80plus")
        reg.remember(c1, "short_A80plus")
        # No current_time → refuse unbounded stale risk
        self.assertIsNone(
            reg.lookup(pair="BTC/USDT:USDT", side="short", entry_tag="short_A80plus")
        )
        # Explicit diagnostics opt-in
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                allow_unbounded=True,
            ),
            c1.candidate_id,
        )

    def test_remember_replaces_mismatched_provided_id(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        expected = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 5)
        wrong = make_candidate_id("ETH/USDT:USDT", TradeSide.LONG, ts, 9)
        cand = TradeCandidate(
            candidate_id=wrong,
            pair="BTC/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="setup:sniper_short",
            secondary_sensors=[],
            confluence_count=4,
            timestamp=ts,
            candle_index=5,
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
            expected,
        )

    def test_remember_skips_negative_candle_index(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cand = TradeCandidate(
            candidate_id="ignored",
            pair="BTC/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="setup:sniper_short",
            secondary_sensors=[],
            confluence_count=4,
            timestamp=ts,
            candle_index=-1,
            raw_features={},
        )
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80plus")
        self.assertIsNone(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80plus",
                current_time=ts,
            )
        )


if __name__ == "__main__":
    unittest.main()
