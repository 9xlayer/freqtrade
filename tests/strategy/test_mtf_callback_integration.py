"""Freqtrade callback integration (strategy methods) without full BT loop."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from strategies.mtf_sniper.core.candidate_id import (
    CANDIDATE_ID_CUSTOM_DATA_KEY,
    CANDIDATE_ID_MISSING,
    is_canonical_candidate_id,
    make_candidate_id,
)
from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.core.runtime_mode import InvalidRuntimeModeError
from strategies.mtf_sniper.execution.exit_policy import ALWAYS_ALLOW_EXIT_REASONS
from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper


class _FakeTrade:
    def __init__(self, **kwargs):
        self._fail_persist = bool(kwargs.pop("_fail_persist", False))
        self.__dict__.update(kwargs)
        self._custom = {}

    def set_custom_data(self, key, value):
        if self._fail_persist:
            raise RuntimeError("simulated custom_data failure")
        self._custom[key] = value

    def get_custom_data(self, key, default=None):
        return self._custom.get(key, default)


class TestCandidateIdRestartSafe(unittest.TestCase):
    def test_persisted_id_survives_registry_clear_on_exit(self):
        """Restart simulation: empty RAM registry; Trade custom data still has ID."""
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat._candidate_registry = CandidateRegistry()
        strat.enable_telemetry = True
        strat._trade_ledger_recorded_ids = set()

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        trade = _FakeTrade(
            id=7,
            pair="BTC/USDT:USDT",
            is_short=True,
            enter_tag="short_A80plus",
            open_date_utc=ts,
            close_date_utc=ts,
            is_open=True,
            amount=1.0,
            exit_side="buy",
            open_rate=100.0,
            close_rate=95.0,
            max_rate=101.0,
            min_rate=94.0,
            stake_amount=100.0,
            leverage=10.0,
            close_profit=0.05,
            close_profit_abs=5.0,
            exit_reason="roi",
        )
        ok = strat._persist_candidate_id(trade, cid)
        self.assertTrue(ok)
        self.assertEqual(trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY), cid)

        # Simulate restart — new empty registry
        strat._candidate_registry = CandidateRegistry()
        ledger = MagicMock()
        strat._trade_ledger = ledger
        order = SimpleNamespace(ft_order_side="buy", filled=1.0, safe_amount=1.0, average=95.0)
        strat.order_filled("BTC/USDT:USDT", trade, order, ts)

        self.assertTrue(ledger.record.called)
        rec = ledger.record.call_args[0][0]
        self.assertEqual(rec.candidate_id, cid)

    def test_persist_returns_false_on_set_failure(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        trade = _FakeTrade(id=1, _fail_persist=True)
        self.assertFalse(strat._persist_candidate_id(trade, cid))
        self.assertIsNone(trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY))

    def test_persist_rejects_non_canonical(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        trade = _FakeTrade(id=1)
        self.assertFalse(strat._persist_candidate_id(trade, "not-a-canonical-id"))
        self.assertFalse(strat._persist_candidate_id(trade, CANDIDATE_ID_MISSING))

    def test_read_persisted_rejects_non_canonical(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        trade = _FakeTrade(id=1)
        trade.set_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY, "legacy_garbage_id")
        self.assertIsNone(strat._read_persisted_candidate_id(trade))

    def test_order_filled_entry_binds_ram_even_if_persist_fails(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat._candidate_registry = CandidateRegistry()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        trade = _FakeTrade(
            id=9,
            is_short=True,
            enter_tag="short_A80plus",
            open_date_utc=ts,
            is_open=True,
            amount=1.0,
            exit_side="buy",
            _fail_persist=True,
        )
        strat._read_persisted_candidate_id = lambda t: None
        strat._resolve_candidate_id = lambda *a, **k: cid
        order = SimpleNamespace(ft_order_side="sell", filled=1.0, safe_amount=1.0)
        strat.order_filled("BTC/USDT:USDT", trade, order, ts)
        self.assertEqual(strat._candidate_registry.lookup_trade(9), cid)
        self.assertIsNone(trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY))

    def test_resolve_uses_absolute_dataframe_index_not_hits_relative(self):
        """hits iloc[-1] may be index 15 of 20 hits; candle_index must be DF position."""
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat._candidate_registry = CandidateRegistry()
        strat.timeframe = "15m"
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        n = 100
        dates = [base + timedelta(minutes=15 * i) for i in range(n)]
        enter = [False] * n
        tags = [""] * n
        # Sparse hits at absolute positions 10, 40, 70 — last hit position 70
        for pos in (10, 40, 70):
            enter[pos] = True
            tags[pos] = "short_A80plus"
        df = pd.DataFrame(
            {
                "date": dates,
                "enter_short": enter,
                "enter_tag": tags,
            }
        )
        strat.dp = MagicMock()
        strat.dp.get_analyzed_dataframe.return_value = (df, None)
        cid = strat._resolve_candidate_id(
            "BTC/USDT:USDT",
            "short",
            "short_A80plus",
            dates[70],
        )
        self.assertIsNotNone(cid)
        self.assertTrue(is_canonical_candidate_id(cid))
        # Must encode absolute index 70, not len(hits)-1 == 2
        self.assertTrue(cid.endswith("_70"), cid)
        self.assertFalse(cid.endswith("_2"), cid)

    def test_resolve_missing_returns_none_not_ad_hoc(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat._candidate_registry = CandidateRegistry()
        strat.dp = MagicMock()
        strat.dp.get_analyzed_dataframe.return_value = (pd.DataFrame(), None)
        strat.timeframe = "15m"
        missing = strat._resolve_candidate_id(
            "BTC/USDT:USDT",
            "short",
            "short_A80plus",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        self.assertIsNone(missing)

    def test_resolve_duplicate_index_uses_mask_absolute_position(self):
        """Duplicate index labels must not confuse candle_index (use mask positions)."""
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat._candidate_registry = CandidateRegistry()
        strat.timeframe = "15m"
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # Two rows share label 7; only the second has the enter signal.
        dates = [
            base,
            base + timedelta(minutes=15),
            base + timedelta(minutes=30),
            base + timedelta(minutes=45),
        ]
        df = pd.DataFrame(
            {
                "date": dates,
                "enter_short": [False, False, True, True],
                "enter_tag": ["", "", "short_A80plus", "short_A80plus"],
            },
            index=[5, 7, 7, 9],
        )
        strat.dp = MagicMock()
        strat.dp.get_analyzed_dataframe.return_value = (df, None)

        # Only first duplicate (iloc 2, label 7) would be wrong if we took first label 7;
        # newest matching absolute position is 3.
        cid = strat._resolve_candidate_id(
            "BTC/USDT:USDT",
            "short",
            "short_A80plus",
            dates[3],
        )
        self.assertIsNotNone(cid)
        self.assertTrue(cid.endswith("_3"), cid)

        # Decisive mid-duplicate: only iloc[2] signals (label 7), not last row.
        df_mid = pd.DataFrame(
            {
                "date": dates,
                "enter_short": [False, False, True, False],
                "enter_tag": ["", "", "short_A80plus", ""],
            },
            index=[5, 7, 7, 9],
        )
        strat.dp.get_analyzed_dataframe.return_value = (df_mid, None)
        cid_mid = strat._resolve_candidate_id(
            "BTC/USDT:USDT",
            "short",
            "short_A80plus",
            dates[3],
        )
        self.assertIsNotNone(cid_mid)
        self.assertTrue(cid_mid.endswith("_2"), cid_mid)

        # Single signal on second duplicate label only
        df2 = pd.DataFrame(
            {
                "date": dates,
                "enter_short": [False, False, False, True],
                "enter_tag": ["", "", "", "short_A80plus"],
            },
            index=[7, 7, 7, 7],
        )
        strat.dp.get_analyzed_dataframe.return_value = (df2, None)
        cid2 = strat._resolve_candidate_id(
            "BTC/USDT:USDT",
            "short",
            "short_A80plus",
            dates[3],
        )
        self.assertIsNotNone(cid2)
        self.assertTrue(cid2.endswith("_3"), cid2)

        # No signal
        df3 = df2.copy()
        df3["enter_short"] = False
        strat.dp.get_analyzed_dataframe.return_value = (df3, None)
        self.assertIsNone(
            strat._resolve_candidate_id(
                "BTC/USDT:USDT", "short", "short_A80plus", dates[3]
            )
        )


class TestRuntimeModeStartupFailFast(unittest.TestCase):
    def test_load_external_config_raises_on_typo_mode(self):
        """Invalid mode must abort strategy init / bot_start — no silent default."""
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat.config = {
            "strategy_params": {"mtf_runtime_mode": "pure_determinstic"},
        }
        with self.assertRaises(InvalidRuntimeModeError):
            StrategyMTFSniper._load_external_config(strat)


class TestRiskAuthPairStoploss(unittest.TestCase):
    def test_pair_stoploss_passed_on_confirm(self):
        captured = {}

        def authorize_entry(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(is_approved=True)

        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat.stoploss = -0.28
        strat.custom_pair_params = {
            "BTC/USDT:USDT": {"stoploss": -0.25},
            "ETH/USDT:USDT": {"stoploss": -0.28},
        }
        strat._get_pair_params = StrategyMTFSniper._get_pair_params.__get__(strat)
        strat._resolve_candidate_id = lambda *a, **k: "BTC/USDT:USDT_short_1_1"
        strat._jev_confirm_entry = lambda *a, **k: True
        strat._get_risk_engine = lambda: SimpleNamespace(authorize_entry=authorize_entry)
        strat.dp = MagicMock()
        strat.dp.get_analyzed_dataframe.return_value = (
            pd.DataFrame({"date": [], "close": []}),
            None,
        )

        import strategies.mtf_sniper.strategy_mtf_sniper as mod

        orig = mod.Trade
        mod.Trade = SimpleNamespace(get_trades_proxy=lambda **kw: [])
        try:
            ok = StrategyMTFSniper.confirm_trade_entry(
                strat,
                pair="BTC/USDT:USDT",
                order_type="limit",
                amount=1.0,
                rate=100.0,
                time_in_force="GTC",
                current_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
                entry_tag="short_A80plus",
                side="short",
            )
        finally:
            mod.Trade = orig
        self.assertTrue(ok)
        self.assertEqual(captured.get("default_stoploss"), -0.25)


class TestConfirmExitHardPaths(unittest.TestCase):
    def test_strategy_confirm_trade_exit_never_blocks_hard(self):
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat.dp = None
        trade = SimpleNamespace(is_short=False, calc_profit_ratio=lambda r: 0.6)
        for reason in ALWAYS_ALLOW_EXIT_REASONS:
            self.assertTrue(
                StrategyMTFSniper.confirm_trade_exit(
                    strat,
                    pair="BTC/USDT:USDT",
                    trade=trade,
                    order_type="market",
                    amount=1.0,
                    rate=100.0,
                    time_in_force="GTC",
                    exit_reason=reason,
                    current_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
                )
            )


if __name__ == "__main__":
    unittest.main()
