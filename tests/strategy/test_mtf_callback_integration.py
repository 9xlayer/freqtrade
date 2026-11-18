"""Freqtrade callback integration (strategy methods) without full BT loop."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from strategies.mtf_sniper.core.candidate_id import (
    CANDIDATE_ID_CUSTOM_DATA_KEY,
    make_candidate_id,
)
from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.execution.exit_policy import ALWAYS_ALLOW_EXIT_REASONS
from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper


class _FakeTrade:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self._custom = {}

    def set_custom_data(self, key, value):
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
        strat._persist_candidate_id(trade, cid)
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
