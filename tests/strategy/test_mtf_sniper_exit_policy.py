"""Unit tests for MTF Sniper exit policy (custom_exit / confirm_trade_exit)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from strategies.mtf_sniper.execution.exit_policy import (
    confirm_exit_allowed,
    evaluate_custom_exit,
    resolve_market_row,
)


def _trade(*, is_short: bool, open_date: datetime):
    t = SimpleNamespace(is_short=is_short, open_date_utc=open_date)
    t.calc_profit_ratio = lambda rate: 0.55
    return t


class TestResolveMarketRow(unittest.TestCase):
    def test_exact_and_fallback(self):
        t0 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 1, 1, 0, 15, tzinfo=timezone.utc)
        df = pd.DataFrame(
            {
                "date": [t0, t1],
                "close": [100.0, 101.0],
            }
        )
        self.assertEqual(resolve_market_row(df, t1)["close"], 101.0)
        later = datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(resolve_market_row(df, later)["close"], 101.0)
        self.assertIsNone(resolve_market_row(None, t0))


class TestEvaluateCustomExit(unittest.TestCase):
    def test_long_trend_invalidated(self):
        open_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now = open_date + timedelta(hours=4)
        trade = _trade(is_short=False, open_date=open_date)
        row = {
            "close": 90.0,
            "swing_low_12": 95.0,
            "close_1h": 90.0,
            "ema_50_1h": 100.0,
            "macdhist_1h": -0.1,
            "ema_21_1h": 100.0,
            "ema_21": 100.0,
        }
        reason = evaluate_custom_exit(
            trade=trade, current_time=now, current_profit=-0.10, row=row
        )
        self.assertEqual(reason, "exit_long_trend_invalidated")

    def test_short_htf_runner_broken(self):
        open_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now = open_date + timedelta(hours=1)
        trade = _trade(is_short=True, open_date=open_date)
        row = {
            "close": 100.0,
            "swing_high_12": 110.0,
            "close_1h": 105.0,
            "ema_50_1h": 100.0,
            "macdhist_1h": -0.1,
            "ema_21_1h": 100.0,
            "ema_21": 100.0,
        }
        reason = evaluate_custom_exit(
            trade=trade, current_time=now, current_profit=0.40, row=row
        )
        self.assertEqual(reason, "exit_short_htf_runner_broken")

    def test_timeout_24h(self):
        open_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now = open_date + timedelta(hours=25)
        trade = _trade(is_short=False, open_date=open_date)
        row = {
            "close": 99.0,
            "swing_low_12": 90.0,
            "close_1h": 101.0,
            "ema_50_1h": 100.0,
            "macdhist_1h": 0.1,
            "ema_21_1h": 100.0,
            "ema_21": 100.0,
        }
        reason = evaluate_custom_exit(
            trade=trade, current_time=now, current_profit=0.01, row=row
        )
        self.assertEqual(reason, "exit_timeout_24h")

    def test_no_exit(self):
        open_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now = open_date + timedelta(hours=1)
        trade = _trade(is_short=False, open_date=open_date)
        row = {
            "close": 101.0,
            "swing_low_12": 90.0,
            "close_1h": 101.0,
            "ema_50_1h": 100.0,
            "macdhist_1h": 0.1,
            "ema_21_1h": 100.0,
            "ema_21": 100.0,
        }
        self.assertIsNone(
            evaluate_custom_exit(
                trade=trade, current_time=now, current_profit=0.05, row=row
            )
        )


class TestConfirmExitAllowed(unittest.TestCase):
    def test_hard_exits_always_allowed(self):
        trade = _trade(
            is_short=False, open_date=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        for reason in ("stop_loss", "trailing_stop_loss", "emergency_exit", "force_exit"):
            self.assertTrue(
                confirm_exit_allowed(
                    trade=trade, exit_reason=reason, rate=100.0, row=None
                )
            )

    def test_roi_deferred_when_trend_intact(self):
        trade = _trade(
            is_short=False, open_date=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        trade.calc_profit_ratio = lambda rate: 0.55
        row = {"close_1h": 110.0, "ema_21_1h": 100.0, "macdhist_1h": 0.1}
        self.assertFalse(
            confirm_exit_allowed(
                trade=trade, exit_reason="roi", rate=110.0, row=row
            )
        )

    def test_roi_allowed_when_trend_broken(self):
        trade = _trade(
            is_short=False, open_date=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        trade.calc_profit_ratio = lambda rate: 0.55
        row = {"close_1h": 90.0, "ema_21_1h": 100.0, "macdhist_1h": -0.1}
        self.assertTrue(
            confirm_exit_allowed(
                trade=trade, exit_reason="roi", rate=90.0, row=row
            )
        )


if __name__ == "__main__":
    unittest.main()
