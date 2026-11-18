"""Unit tests for MTF Sniper P3 Risk Engine modules."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from strategies.mtf_sniper.core.enums import RejectionReasonCode
from strategies.mtf_sniper.risk.portfolio_protection import (
    circuit_breaker_blocks,
    directional_lock_blocks,
    distress_guard_blocks,
)
from strategies.mtf_sniper.risk.position_sizing import resolve_leverage, resolve_stake_amount
from strategies.mtf_sniper.risk.risk_engine import RiskEngine
from strategies.mtf_sniper.risk.stoploss_ladder import resolve_custom_stoploss


def _trade(**kwargs):
    defaults = dict(
        strategy="StrategyMTFSniper",
        close_profit=0.0,
        close_date=datetime(2024, 1, 10, tzinfo=timezone.utc),
        is_short=False,
        pair="BTC/USDT:USDT",
        leverage=10.0,
        open_date_utc=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    t = SimpleNamespace(**defaults)
    t.calc_profit_ratio = kwargs.get("calc_profit_ratio", lambda price: 0.0)
    return t


class TestPortfolioProtection(unittest.TestCase):
    def test_circuit_breaker_three_heavy_losses(self):
        now = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
        closed = [
            _trade(close_profit=-0.12, close_date=now - timedelta(hours=1)),
            _trade(close_profit=-0.15, close_date=now - timedelta(hours=2)),
            _trade(close_profit=-0.11, close_date=now - timedelta(hours=3)),
        ]
        self.assertTrue(
            circuit_breaker_blocks(
                closed, strategy_name="StrategyMTFSniper", current_time=now
            )
        )

    def test_circuit_breaker_ignores_other_strategy(self):
        now = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
        closed = [
            _trade(strategy="Other", close_profit=-0.20, close_date=now - timedelta(hours=1)),
            _trade(strategy="Other", close_profit=-0.20, close_date=now - timedelta(hours=2)),
            _trade(strategy="Other", close_profit=-0.20, close_date=now - timedelta(hours=3)),
        ]
        self.assertFalse(
            circuit_breaker_blocks(
                closed, strategy_name="StrategyMTFSniper", current_time=now
            )
        )

    def test_circuit_breaker_resets_on_mild_loss(self):
        now = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
        closed = [
            _trade(close_profit=-0.12, close_date=now - timedelta(hours=1)),
            _trade(close_profit=-0.05, close_date=now - timedelta(hours=2)),  # reset
            _trade(close_profit=-0.15, close_date=now - timedelta(hours=3)),
        ]
        self.assertFalse(
            circuit_breaker_blocks(
                closed, strategy_name="StrategyMTFSniper", current_time=now
            )
        )

    def test_directional_lock(self):
        open_trades = [_trade(is_short=True)]
        self.assertTrue(directional_lock_blocks(open_trades, "long"))
        self.assertFalse(directional_lock_blocks(open_trades, "short"))

    def test_distress_guard(self):
        now = datetime(2024, 1, 10, tzinfo=timezone.utc)
        ot = _trade(pair="ETH/USDT:USDT")
        ot.calc_profit_ratio = lambda price: -0.09
        self.assertTrue(
            distress_guard_blocks(
                [ot],
                pair="BTC/USDT:USDT",
                current_time=now,
                get_price=lambda p, t: 100.0,
            )
        )


class TestPositionSizing(unittest.TestCase):
    def test_flat_default(self):
        self.assertEqual(
            resolve_stake_amount(
                proposed_stake=100.0,
                max_stake=200.0,
                entry_tag="long_A80plus",
                enable_dynamic_sizing=False,
            ),
            100.0,
        )

    def test_dynamic_tag_multiplier(self):
        self.assertAlmostEqual(
            resolve_stake_amount(
                proposed_stake=100.0,
                max_stake=200.0,
                entry_tag="long_A80plus",
                enable_dynamic_sizing=True,
            ),
            110.0,
            places=9,
        )

    def test_leverage_cap(self):
        self.assertEqual(resolve_leverage(5.0), 5.0)
        self.assertEqual(resolve_leverage(20.0), 10.0)


class TestStoplossLadder(unittest.TestCase):
    def test_base_stoploss_positive_relative(self):
        trade = _trade(is_short=False, leverage=10.0)
        sl = resolve_custom_stoploss(
            trade=trade,
            current_time=datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc),
            current_profit=0.0,
            pair_cfg={"stoploss": -0.25},
            default_stoploss=-0.28,
        )
        self.assertGreater(sl, 0.0)


class TestRiskEngine(unittest.TestCase):
    def test_authorize_blocks_on_directional_lock(self):
        engine = RiskEngine()
        now = datetime(2024, 1, 10, tzinfo=timezone.utc)
        auth = engine.authorize_entry(
            strategy_name="StrategyMTFSniper",
            pair="BTC/USDT:USDT",
            side="long",
            current_time=now,
            closed_trades=[],
            open_trades=[_trade(is_short=True)],
            get_price=lambda p, t: 100.0,
            jev_confirm=lambda: True,
        )
        self.assertFalse(auth.is_approved)
        self.assertIn(RejectionReasonCode.RISK_DIRECTIONAL_LOCK.value, auth.rejection_reasons)

    def test_authorize_runs_jev(self):
        engine = RiskEngine()
        now = datetime(2024, 1, 10, tzinfo=timezone.utc)
        called = {"n": 0}

        def jev():
            called["n"] += 1
            return False

        auth = engine.authorize_entry(
            strategy_name="StrategyMTFSniper",
            pair="BTC/USDT:USDT",
            side="long",
            current_time=now,
            closed_trades=[],
            open_trades=[],
            get_price=lambda p, t: 100.0,
            jev_confirm=jev,
        )
        self.assertEqual(called["n"], 1)
        self.assertFalse(auth.is_approved)


if __name__ == "__main__":
    unittest.main()
