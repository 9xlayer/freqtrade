"""Unit tests for MTF Sniper P3 Risk Engine modules."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from freqtrade.strategy import stoploss_from_open
from strategies.mtf_sniper.core.enums import RejectionReasonCode
from strategies.mtf_sniper.risk.portfolio_protection import (
    circuit_breaker_blocks,
    directional_lock_blocks,
    distress_guard_blocks,
)
from strategies.mtf_sniper.risk.position_sizing import resolve_leverage, resolve_stake_amount
from strategies.mtf_sniper.risk.risk_engine import RiskEngine
from strategies.mtf_sniper.risk.stoploss_ladder import (
    _ladder_stoploss_for_profit,
    resolve_custom_stoploss,
)


def _trade(**kwargs):
    defaults = {
        "strategy": "StrategyMTFSniper",
        "close_profit": 0.0,
        "close_date": datetime(2024, 1, 10, tzinfo=UTC),
        "is_short": False,
        "pair": "BTC/USDT:USDT",
        "leverage": 10.0,
        "open_date_utc": datetime(2024, 1, 1, tzinfo=UTC),
    }
    defaults.update(kwargs)
    t = SimpleNamespace(**defaults)
    t.calc_profit_ratio = kwargs.get("calc_profit_ratio", lambda price: 0.0)
    return t


class TestPortfolioProtection(unittest.TestCase):
    def test_circuit_breaker_three_heavy_losses(self):
        now = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
        closed = [
            _trade(close_profit=-0.12, close_date=now - timedelta(hours=1)),
            _trade(close_profit=-0.15, close_date=now - timedelta(hours=2)),
            _trade(close_profit=-0.11, close_date=now - timedelta(hours=3)),
        ]
        self.assertTrue(
            circuit_breaker_blocks(closed, strategy_name="StrategyMTFSniper", current_time=now)
        )

    def test_circuit_breaker_ignores_other_strategy(self):
        now = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
        closed = [
            _trade(strategy="Other", close_profit=-0.20, close_date=now - timedelta(hours=1)),
            _trade(strategy="Other", close_profit=-0.20, close_date=now - timedelta(hours=2)),
            _trade(strategy="Other", close_profit=-0.20, close_date=now - timedelta(hours=3)),
        ]
        self.assertFalse(
            circuit_breaker_blocks(closed, strategy_name="StrategyMTFSniper", current_time=now)
        )

    def test_circuit_breaker_resets_on_mild_loss(self):
        now = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
        closed = [
            _trade(close_profit=-0.12, close_date=now - timedelta(hours=1)),
            _trade(close_profit=-0.05, close_date=now - timedelta(hours=2)),  # reset
            _trade(close_profit=-0.15, close_date=now - timedelta(hours=3)),
        ]
        self.assertFalse(
            circuit_breaker_blocks(closed, strategy_name="StrategyMTFSniper", current_time=now)
        )

    def test_directional_lock(self):
        open_trades = [_trade(is_short=True)]
        self.assertTrue(directional_lock_blocks(open_trades, "long"))
        self.assertFalse(directional_lock_blocks(open_trades, "short"))

    def test_distress_guard(self):
        now = datetime(2024, 1, 10, tzinfo=UTC)
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
                entry_tag="long_A80_plus",
                enable_dynamic_sizing=False,
            ),
            100.0,
        )

    def test_dynamic_tag_multiplier(self):
        self.assertAlmostEqual(
            resolve_stake_amount(
                proposed_stake=100.0,
                max_stake=200.0,
                entry_tag="long_A80_plus",
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
            current_time=datetime(2024, 1, 1, 1, 0, tzinfo=UTC),
            current_profit=0.0,
            pair_cfg={"stoploss": -0.25},
            default_stoploss=-0.28,
        )
        self.assertGreater(sl, 0.0)

    def test_ladder_stoploss_tiers_long_and_short(self):
        lev = 10.0
        # Tier 0: < 18% -> None
        for p in [0.0, 0.10, 0.179]:
            self.assertIsNone(_ladder_stoploss_for_profit(p, is_short=False, lev=lev))
            self.assertIsNone(_ladder_stoploss_for_profit(p, is_short=True, lev=lev))

        # Tier 1: 18% to < 30% -> lock +1% (+0.01)
        for p in [0.18, 0.25, 0.299]:
            expected_long = stoploss_from_open(0.01, p, is_short=False, leverage=lev)
            expected_short = stoploss_from_open(0.01, p, is_short=True, leverage=lev)
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=False, lev=lev), expected_long)
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=True, lev=lev), expected_short)

        # Tier 2: 30% to < 50% -> lock +10% (+0.10)
        for p in [0.30, 0.40, 0.499]:
            expected_long = stoploss_from_open(0.10, p, is_short=False, leverage=lev)
            expected_short = stoploss_from_open(0.10, p, is_short=True, leverage=lev)
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=False, lev=lev), expected_long)
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=True, lev=lev), expected_short)

        # Tier 3: 50% to < 80% -> lock +25% (+0.25)
        for p in [0.50, 0.65, 0.799]:
            expected_long = stoploss_from_open(0.25, p, is_short=False, leverage=lev)
            expected_short = stoploss_from_open(0.25, p, is_short=True, leverage=lev)
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=False, lev=lev), expected_long)
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=True, lev=lev), expected_short)

        # Tier 4: 80% to < 120% -> trail -0.25
        for p in [0.80, 1.00, 1.199]:
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=False, lev=lev), -0.25)
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=True, lev=lev), -0.25)

        # Tier 5: >= 120% -> trail -0.20
        for p in [1.20, 1.50, 3.00]:
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=False, lev=lev), -0.20)
            self.assertEqual(_ladder_stoploss_for_profit(p, is_short=True, lev=lev), -0.20)

    def test_resolve_custom_stoploss_full_ladder_lifecycle(self):
        trade_long = _trade(is_short=False, leverage=10.0)
        trade_short = _trade(is_short=True, leverage=10.0)
        now = datetime(2024, 1, 1, 2, 0, tzinfo=UTC)

        # 0% profit: base stoploss
        sl_base_long = resolve_custom_stoploss(
            trade=trade_long,
            current_time=now,
            current_profit=0.0,
            pair_cfg={"stoploss": -0.25},
            default_stoploss=-0.28,
        )
        self.assertAlmostEqual(sl_base_long, 0.25, places=3)

        # 18% profit: lock +1%
        sl_18_long = resolve_custom_stoploss(
            trade=trade_long,
            current_time=now,
            current_profit=0.18,
            pair_cfg={"stoploss": -0.25},
            default_stoploss=-0.28,
        )
        self.assertEqual(
            sl_18_long, stoploss_from_open(0.01, 0.18, is_short=False, leverage=10.0)
        )

        # 30% profit: lock +10%
        sl_30_short = resolve_custom_stoploss(
            trade=trade_short,
            current_time=now,
            current_profit=0.30,
            pair_cfg={"stoploss": -0.25},
            default_stoploss=-0.28,
        )
        self.assertEqual(
            sl_30_short, stoploss_from_open(0.10, 0.30, is_short=True, leverage=10.0)
        )

        # 80% profit: trail -0.25
        sl_80_long = resolve_custom_stoploss(
            trade=trade_long,
            current_time=now,
            current_profit=0.80,
            pair_cfg={"stoploss": -0.25},
            default_stoploss=-0.28,
        )
        self.assertEqual(sl_80_long, -0.25)

        # 120% profit: trail -0.20
        sl_120_short = resolve_custom_stoploss(
            trade=trade_short,
            current_time=now,
            current_profit=1.20,
            pair_cfg={"stoploss": -0.25},
            default_stoploss=-0.28,
        )
        self.assertEqual(sl_120_short, -0.20)


class TestRiskEngine(unittest.TestCase):
    def test_authorize_blocks_on_directional_lock(self):
        engine = RiskEngine()
        now = datetime(2024, 1, 10, tzinfo=UTC)
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
        now = datetime(2024, 1, 10, tzinfo=UTC)
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

    def test_authorize_exposure_honest_without_equity(self):
        engine = RiskEngine(max_open_trades=2)
        now = datetime(2024, 1, 10, tzinfo=UTC)
        open_t = _trade(stake_amount=100.0, leverage=10.0, is_short=False)
        auth = engine.authorize_entry(
            strategy_name="StrategyMTFSniper",
            pair="BTC/USDT:USDT",
            side="long",
            current_time=now,
            closed_trades=[],
            open_trades=[open_t],
            get_price=lambda p, t: 100.0,
            jev_confirm=lambda: True,
            proposed_stake=50.0,
            max_stake=50.0,
            max_leverage=10.0,
        )
        self.assertTrue(auth.is_approved)
        self.assertEqual(auth.sizing_method, "freqtrade_proposed")
        self.assertIsNone(auth.risk_budget_used_pct)
        self.assertIsNone(auth.risk_per_trade_pct)
        self.assertIsNone(auth.total_exposure_pct)
        self.assertIsNone(auth.wallet_equity)
        # open 100*10 + new 50*10
        self.assertAlmostEqual(auth.total_exposure_notional, 1500.0, places=6)

    def test_authorize_exposure_pct_when_equity_known(self):
        engine = RiskEngine()
        now = datetime(2024, 1, 10, tzinfo=UTC)
        auth = engine.authorize_entry(
            strategy_name="StrategyMTFSniper",
            pair="BTC/USDT:USDT",
            side="long",
            current_time=now,
            closed_trades=[],
            open_trades=[],
            get_price=lambda p, t: 100.0,
            jev_confirm=lambda: True,
            proposed_stake=100.0,
            max_stake=100.0,
            max_leverage=10.0,
            wallet_equity=2000.0,
        )
        self.assertTrue(auth.is_approved)
        self.assertEqual(auth.wallet_equity, 2000.0)
        self.assertAlmostEqual(auth.total_exposure_notional, 1000.0, places=6)
        self.assertAlmostEqual(auth.total_exposure_pct or 0.0, 0.5, places=6)


if __name__ == "__main__":
    unittest.main()
