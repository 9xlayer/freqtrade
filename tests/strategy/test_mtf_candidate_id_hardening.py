"""Final Candidate ID correlation hardening (max-age, parse, DF position, persist)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pandas as pd

from freqtrade.persistence import Order, Trade, disable_database_use, enable_database_use
from freqtrade.persistence.custom_data import CustomDataWrapper
from freqtrade.persistence.trade_model import LocalTrade
from strategies.mtf_sniper.core.candidate_id import (
    CANDIDATE_ID_CUSTOM_DATA_KEY,
    CANDIDATE_ID_MISSING,
    candidate_id_matches_trade,
    is_canonical_candidate_id,
    make_candidate_id,
    parse_candidate_id,
)
from strategies.mtf_sniper.core.candidate_registry import (
    DEFAULT_LOOKUP_MAX_AGE,
    CandidateRegistry,
)
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper


def _empty_registry_strat() -> StrategyMTFSniper:
    strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
    strat._candidate_registry = CandidateRegistry()
    strat.timeframe = "15m"
    return strat


def _df_with_signal(
    *,
    signal_at: datetime,
    current_span_bars: int = 20,
    tag: str = "short_A80_plus",
    future_signal_at: datetime | None = None,
) -> tuple[pd.DataFrame, datetime]:
    """Build a small analyzed DF with one (optional two) enter_short hits."""
    # Align grid so signal_at lands on a bar.
    start = signal_at - timedelta(minutes=15 * (current_span_bars // 2))
    dates = [start + timedelta(minutes=15 * i) for i in range(current_span_bars + 5)]
    # Ensure signal_at is in the grid
    if signal_at not in dates:
        dates.append(signal_at)
        dates = sorted(dates)
    enter = []
    tags = []
    for d in dates:
        hit = d == signal_at or (future_signal_at is not None and d == future_signal_at)
        enter.append(hit)
        tags.append(tag if hit else "")
    df = pd.DataFrame({"date": dates, "enter_short": enter, "enter_tag": tags})
    return df, signal_at


class TestDataFrameFallbackMaxAge(unittest.TestCase):
    def test_recent_matching_signal_resolves(self):
        strat = _empty_registry_strat()
        signal_at = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        df, _ = _df_with_signal(signal_at=signal_at)
        strat.dp = MagicMock()
        strat.dp.get_analyzed_dataframe.return_value = (df, None)
        now = signal_at + timedelta(minutes=30)
        cid = strat._resolve_candidate_id("BTC/USDT:USDT", "short", "short_A80_plus", now)
        self.assertIsNotNone(cid)
        self.assertTrue(is_canonical_candidate_id(cid))
        parsed = parse_candidate_id(cid)
        assert parsed is not None
        self.assertEqual(parsed.pair, "BTC/USDT:USDT")
        self.assertEqual(parsed.side, "short")
        self.assertEqual(parsed.epoch, int(signal_at.timestamp()))

    def test_signal_older_than_max_age_returns_none(self):
        strat = _empty_registry_strat()
        signal_at = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        df, _ = _df_with_signal(signal_at=signal_at)
        strat.dp = MagicMock()
        strat.dp.get_analyzed_dataframe.return_value = (df, None)
        now = signal_at + DEFAULT_LOOKUP_MAX_AGE + timedelta(minutes=1)
        cid = strat._resolve_candidate_id("BTC/USDT:USDT", "short", "short_A80_plus", now)
        self.assertIsNone(cid)

    def test_future_signal_never_selected(self):
        strat = _empty_registry_strat()
        past = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
        future = datetime(2024, 6, 1, 14, 0, tzinfo=UTC)
        # Only a future hit exists (past intentionally not an enter)
        dates = [
            past - timedelta(minutes=15),
            past,
            past + timedelta(minutes=15),
            future,
        ]
        df = pd.DataFrame(
            {
                "date": dates,
                "enter_short": [False, False, False, True],
                "enter_tag": ["", "", "", "short_A80_plus"],
            }
        )
        strat.dp = MagicMock()
        strat.dp.get_analyzed_dataframe.return_value = (df, None)
        cid = strat._resolve_candidate_id(
            "BTC/USDT:USDT", "short", "short_A80_plus", past + timedelta(minutes=15)
        )
        self.assertIsNone(cid)


class TestCanonicalCandidateIdValidation(unittest.TestCase):
    def test_valid_long_short_ids(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        for side in (TradeSide.LONG, TradeSide.SHORT):
            cid = make_candidate_id("ETH/USDT:USDT", side, ts, 7)
            self.assertTrue(is_canonical_candidate_id(cid))
            parsed = parse_candidate_id(cid)
            assert parsed is not None
            self.assertEqual(parsed.pair, "ETH/USDT:USDT")
            self.assertEqual(parsed.side, side.value)
            self.assertEqual(parsed.candle_index, 7)

    def test_rejects_bad_side_and_pair(self):
        self.assertFalse(is_canonical_candidate_id("BTC/USDT:USDT_mid_1_1"))
        self.assertFalse(is_canonical_candidate_id("BTCUSDT_long_1_1"))  # no /
        self.assertFalse(is_canonical_candidate_id("legacy_garbage"))
        self.assertFalse(is_canonical_candidate_id(CANDIDATE_ID_MISSING))
        self.assertFalse(is_canonical_candidate_id(""))

    def test_rejects_side_object_with_str_short(self):
        """Objects that stringify to 'short'/'long' must not pass type contract."""
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)

        class _LooksLikeShort:
            def __str__(self) -> str:
                return "short"

        with self.assertRaises(ValueError):
            make_candidate_id("BTC/USDT:USDT", _LooksLikeShort(), ts, 1)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            make_candidate_id("BTC/USDT:USDT", None, ts, 1)  # type: ignore[arg-type]
        # Canonical strings still accepted
        cid = make_candidate_id("BTC/USDT:USDT", "short", ts, 1)
        self.assertTrue(cid.endswith("_1"))

    def test_matches_trade_context(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 3)
        self.assertTrue(candidate_id_matches_trade(cid, pair="BTC/USDT:USDT", side="short"))
        self.assertFalse(candidate_id_matches_trade(cid, pair="ETH/USDT:USDT", side="short"))
        self.assertFalse(candidate_id_matches_trade(cid, pair="BTC/USDT:USDT", side="long"))


class TestDataFrameFallbackDuplicateIndex(unittest.TestCase):
    def test_mid_duplicate_label_signal_uses_absolute_iloc(self):
        """
        Decisive case vs old hits.index[-1] → label→position path.

        Only iloc[2] has the signal; its index label is 7 (duplicated). Old
        code taking hits.index[-1] == 7 then resolving label 7 could pick the
        wrong absolute row. Mask positions must yield candle_index == 2.
        """
        strat = _empty_registry_strat()
        base = datetime(2024, 1, 1, tzinfo=UTC)
        dates = [
            base,
            base + timedelta(minutes=15),
            base + timedelta(minutes=30),
            base + timedelta(minutes=45),
        ]
        df = pd.DataFrame(
            {
                "date": dates,
                "enter_short": [False, False, True, False],
                "enter_tag": ["", "", "short_A80_plus", ""],
            },
            index=[5, 7, 7, 9],
        )
        strat.dp = MagicMock()
        strat.dp.get_analyzed_dataframe.return_value = (df, None)
        cid = strat._resolve_candidate_id(
            "BTC/USDT:USDT",
            "short",
            "short_A80_plus",
            dates[3],
        )
        self.assertIsNotNone(cid)
        self.assertTrue(cid.endswith("_2"), cid)
        # Must not encode first occurrence of label 7 (iloc 1) or last DF row.
        self.assertFalse(cid.endswith("_1"), cid)
        self.assertFalse(cid.endswith("_3"), cid)


class TestFreqtradePersistenceCorrelation(unittest.TestCase):
    """Use LocalTrade + CustomDataWrapper (Freqtrade in-memory persistence)."""

    def setUp(self):
        disable_database_use("15m")
        Trade.reset_trades()
        CustomDataWrapper.reset_custom_data()

    def tearDown(self):
        CustomDataWrapper.reset_custom_data()
        Trade.reset_trades()
        enable_database_use()

    def _make_local_trade(self, **kwargs) -> LocalTrade:
        ts = kwargs.pop("open_date", datetime(2024, 6, 1, 12, 0, tzinfo=UTC))
        defaults = {
            "id": kwargs.pop("id", 42),
            "pair": "BTC/USDT:USDT",
            "exchange": "binance",
            "is_open": True,
            "is_short": True,
            "open_rate": 100.0,
            "amount": 1.0,
            "stake_amount": 100.0,
            "fee_open": 0.0005,
            "fee_close": 0.0005,
            "open_date": ts.replace(tzinfo=None) if ts.tzinfo else ts,
            "enter_tag": "short_A80_plus",
            "leverage": 10.0,
            "trading_mode": "futures",
        }
        defaults.update(kwargs)
        return LocalTrade(**defaults)

    def test_entry_fill_persists_and_exit_reuses_same_id(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat._candidate_registry = CandidateRegistry()
        strat.enable_telemetry = True
        strat._trade_ledger_recorded_ids = set()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        trade = self._make_local_trade(id=7, open_date=ts)
        # entry fill (short → sell)
        strat._resolve_candidate_id = lambda *a, **k: cid
        entry_order = SimpleNamespace(ft_order_side="sell", filled=1.0, safe_amount=1.0)
        strat.order_filled(
            "BTC/USDT:USDT", cast(Trade, trade), cast(Order, entry_order), ts
        )
        self.assertEqual(trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY), cid)

        # Simulate restart: wipe RAM registry, keep LocalTrade custom data
        strat._candidate_registry = CandidateRegistry()
        ledger = MagicMock()
        strat._trade_ledger = ledger
        # Full exit fill
        trade.is_open = True
        trade.close_rate = 95.0
        trade.max_rate = 101.0
        trade.min_rate = 94.0
        trade.close_profit = 0.05
        trade.close_profit_abs = 5.0
        trade.exit_reason = "roi"
        trade.close_date = ts.replace(tzinfo=None)
        exit_order = SimpleNamespace(
            ft_order_side="buy", filled=1.0, safe_amount=1.0, average=95.0
        )
        strat.order_filled(
            "BTC/USDT:USDT", cast(Trade, trade), cast(Order, exit_order), ts
        )
        self.assertTrue(ledger.record.called)
        rec = ledger.record.call_args[0][0]
        self.assertEqual(rec.candidate_id, cid)

    def test_partial_exit_does_not_record_ledger(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat._candidate_registry = CandidateRegistry()
        strat.enable_telemetry = True
        strat._trade_ledger = MagicMock()
        strat._trade_ledger_recorded_ids = set()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        trade = self._make_local_trade(id=8, open_date=ts, amount=2.0)
        trade.set_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY, cid)
        # Partial exit: filled 1.0 of amount 2.0 while still open
        partial = SimpleNamespace(
            ft_order_side="buy", filled=1.0, safe_amount=1.0, average=95.0
        )
        strat.order_filled(
            "BTC/USDT:USDT", cast(Trade, trade), cast(Order, partial), ts
        )
        strat._trade_ledger.record.assert_not_called()
        # Correlation intact
        self.assertEqual(strat._read_persisted_candidate_id(cast(Trade, trade)), cid)

    def test_read_rejects_pair_side_mismatch(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        wrong = make_candidate_id("ETH/USDT:USDT", TradeSide.LONG, ts, 1)
        trade = self._make_local_trade(id=9, open_date=ts, is_short=True)
        trade.set_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY, wrong)
        self.assertIsNone(strat._read_persisted_candidate_id(cast(Trade, trade)))

    def test_persist_rejects_mismatched_id(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        wrong = make_candidate_id("ETH/USDT:USDT", TradeSide.SHORT, ts, 1)
        trade = self._make_local_trade(id=10, open_date=ts)
        self.assertFalse(strat._persist_candidate_id(cast(Trade, trade), wrong))
        self.assertIsNone(trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY))


if __name__ == "__main__":
    unittest.main()
