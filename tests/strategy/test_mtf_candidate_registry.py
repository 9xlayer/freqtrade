"""Candidate ID lifecycle: remember → confirm lookup → trade bind → exit unbind."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

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
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        cand = _cand(ts, 42)
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80_plus")
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
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
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        cand = _cand(ts, 10)
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80_plus")
        earlier = ts - timedelta(minutes=15)
        self.assertIsNone(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=earlier,
            )
        )

    def test_lookup_equal_timestamp_matches(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        cand = _cand(ts, 10)
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80_plus")
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts,
            ),
            cand.candidate_id,
        )

    def test_lookup_after_picks_nearest_not_future(self):
        t0 = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
        t1 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        t2 = datetime(2024, 6, 1, 14, 0, tzinfo=UTC)
        c0, c1, c2 = _cand(t0, 1), _cand(t1, 2), _cand(t2, 3)
        reg = CandidateRegistry()
        for c in (c0, c1, c2):
            reg.remember(c, "short_A80_plus")
        # At t1: nearest <= t1 is c1 (not future c2, not stale preference for c0)
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
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
                entry_tag="short_A80_plus",
                current_time=mid,
            ),
            c1.candidate_id,
        )

    def test_lookup_rejects_stale_outside_max_age(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        cand = _cand(ts, 5)
        reg = CandidateRegistry(lookup_max_age=timedelta(hours=1))
        reg.remember(cand, "short_A80_plus")
        # Outside 1h window → None
        self.assertIsNone(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts + timedelta(hours=2),
            )
        )
        # Within 1h still OK
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts + timedelta(minutes=30),
            ),
            cand.candidate_id,
        )
        # Default window still accepts ages under DEFAULT_LOOKUP_MAX_AGE
        reg_default = CandidateRegistry()
        reg_default.remember(cand, "short_A80_plus")
        almost_stale = ts + DEFAULT_LOOKUP_MAX_AGE - timedelta(minutes=1)
        self.assertEqual(
            reg_default.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=almost_stale,
            ),
            cand.candidate_id,
        )

    def test_lookup_without_current_time_fail_closed(self):
        t0 = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
        t1 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        c0, c1 = _cand(t0, 1), _cand(t1, 2)
        reg = CandidateRegistry()
        reg.remember(c0, "short_A80_plus")
        reg.remember(c1, "short_A80_plus")
        # No current_time → refuse unbounded stale risk
        self.assertIsNone(
            reg.lookup(pair="BTC/USDT:USDT", side="short", entry_tag="short_A80_plus")
        )
        # Explicit diagnostics opt-in
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                allow_unbounded=True,
            ),
            c1.candidate_id,
        )

    def test_remember_replaces_mismatched_provided_id(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
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
        reg.remember(cand, "short_A80_plus")
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts,
            ),
            expected,
        )

    def test_remember_skips_negative_candle_index(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
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
        reg.remember(cand, "short_A80_plus")
        self.assertIsNone(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts,
            )
        )

    def test_remember_skips_float_and_bool_index(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        reg = CandidateRegistry()
        for bad_idx in (12.0, 12.9, True):
            cand = TradeCandidate(
                candidate_id="",
                pair="BTC/USDT:USDT",
                side=TradeSide.SHORT,
                primary_sensor="setup:sniper_short",
                secondary_sensors=[],
                confluence_count=4,
                timestamp=ts,
                candle_index=bad_idx,  # type: ignore[arg-type]
                raw_features={},
            )
            reg.remember(cand, "short_A80_plus")
        self.assertIsNone(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts,
            )
        )

    def test_remember_fail_closed_on_malformed_identity_no_raise(self):
        """Malformed identity fields must not raise or store; bad ID alone still canonicizes."""
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        reg = CandidateRegistry()
        # Identity-field failures → no registry write
        bad_cases = [
            None,
            SimpleNamespace(
                pair=None,
                side=TradeSide.SHORT,
                timestamp=ts,
                candle_index=1,
                candidate_id="",
            ),
            SimpleNamespace(
                pair=42,
                side=TradeSide.SHORT,
                timestamp=ts,
                candle_index=1,
                candidate_id="",
            ),
            SimpleNamespace(
                pair="BTC/USDT:USDT",
                side=None,
                timestamp=ts,
                candle_index=1,
                candidate_id="",
            ),
            SimpleNamespace(
                pair="BTC/USDT:USDT",
                side="invalid",
                timestamp=ts,
                candle_index=1,
                candidate_id="",
            ),
            SimpleNamespace(
                pair="BTC/USDT:USDT",
                side=object(),
                timestamp=ts,
                candle_index=1,
                candidate_id="",
            ),
            SimpleNamespace(
                pair="BTC/USDT:USDT",
                side=TradeSide.SHORT,
                timestamp=1717243200,
                candle_index=1,
                candidate_id="",
            ),
        ]
        for bad in bad_cases:
            try:
                reg.remember(bad, "short_A80_plus")
            except Exception as exc:  # pragma: no cover - must not happen
                self.fail(f"remember raised on malformed candidate: {exc!r}")
        self.assertIsNone(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts,
            )
        )
        # Non-str candidate_id with valid identity → no raise, store canonical
        expected = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 1)
        try:
            reg.remember(
                SimpleNamespace(
                    pair="BTC/USDT:USDT",
                    side=TradeSide.SHORT,
                    timestamp=ts,
                    candle_index=1,
                    candidate_id=123,
                ),
                "short_A80_plus",
            )
        except Exception as exc:  # pragma: no cover
            self.fail(f"remember raised on non-str candidate_id: {exc!r}")
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts,
            ),
            expected,
        )

    def test_remember_none_candidate_id_uses_canonical(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        expected = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 3)
        reg = CandidateRegistry()
        reg.remember(
            SimpleNamespace(
                pair="BTC/USDT:USDT",
                side=TradeSide.SHORT,
                timestamp=ts,
                candle_index=3,
                candidate_id=None,
            ),
            "short_A80_plus",
        )
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts,
            ),
            expected,
        )

    def test_remember_mismatched_id_with_valid_fields_stores_canonical(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        expected = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 4)
        cand = TradeCandidate(
            candidate_id="BTC/USDT:USDT_short_999_0",
            pair="BTC/USDT:USDT",
            side=TradeSide.SHORT,
            primary_sensor="setup:sniper_short",
            secondary_sensors=[],
            confluence_count=4,
            timestamp=ts,
            candle_index=4,
            raw_features={},
        )
        reg = CandidateRegistry()
        reg.remember(cand, "short_A80_plus")
        self.assertEqual(
            reg.lookup(
                pair="BTC/USDT:USDT",
                side="short",
                entry_tag="short_A80_plus",
                current_time=ts,
            ),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
