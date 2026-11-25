"""Unit tests for MTF Sniper P3 telemetry ledgers."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from freqtrade.persistence import Order, Trade
from strategies.mtf_sniper.core.contracts import (
    TelemetryDecisionRecord,
    TelemetryTradeRecord,
)
from strategies.mtf_sniper.core.enums import RejectionReasonCode
from strategies.mtf_sniper.risk.risk_engine import RiskEngine
from strategies.mtf_sniper.telemetry import DecisionLedger, TradeLedger
from tests.strategy.test_mtf_sniper_risk import _trade


class TestDecisionLedger(unittest.TestCase):
    def test_record_and_filter(self):
        ledger = DecisionLedger(enabled=True)
        now = datetime(2024, 1, 10, tzinfo=UTC).isoformat()
        ledger.record(
            TelemetryDecisionRecord(
                candidate_id="c1",
                timestamp=now,
                pair="BTC/USDT:USDT",
                side="long",
                sensors_triggered=["long_A80_plus"],
                confluence_count=1,
                market_regime="",
                policy_score=0.0,
                quality_grade="ACCEPT",
                policy_verdict="ACCEPT",
                ai_verdict="CONFIRM",
                ai_confidence=0.8,
                ai_model_version=None,
                risk_approved=True,
                final_action="ENTER",
                final_reasons=[],
            )
        )
        ledger.record(
            TelemetryDecisionRecord(
                candidate_id="c2",
                timestamp=now,
                pair="ETH/USDT:USDT",
                side="short",
                sensors_triggered=[],
                confluence_count=0,
                market_regime="",
                policy_score=0.0,
                quality_grade="REJECT",
                policy_verdict="REJECT",
                ai_verdict="",
                ai_confidence=0.0,
                ai_model_version=None,
                risk_approved=False,
                final_action="REJECT",
                final_reasons=[RejectionReasonCode.RISK_DIRECTIONAL_LOCK.value],
            )
        )
        self.assertEqual(len(ledger.all()), 2)
        self.assertEqual(len(ledger.entered()), 1)
        self.assertEqual(len(ledger.rejected()), 1)
        self.assertEqual(
            ledger.rejected()[0].final_reasons[0],
            RejectionReasonCode.RISK_DIRECTIONAL_LOCK.value,
        )

    def test_jsonl_persist_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decision_ledger.jsonl"
            ledger = DecisionLedger(path, enabled=True)
            ledger.record_dict(
                candidate_id="c1",
                timestamp="2024-01-10T00:00:00+00:00",
                pair="BTC/USDT:USDT",
                side="long",
                sensors_triggered=["long_A80_plus"],
                confluence_count=1,
                market_regime="",
                policy_score=0.0,
                quality_grade="REJECT",
                policy_verdict="REJECT",
                ai_verdict="",
                ai_confidence=0.0,
                ai_model_version=None,
                risk_approved=False,
                final_action="REJECT",
                final_reasons=["RISK_CIRCUIT_BREAKER_ACTIVE"],
            )
            disk_rows = list(ledger.load_from_disk())
            self.assertEqual(len(disk_rows), 1)
            self.assertEqual(disk_rows[0].candidate_id, "c1")
            raw = path.read_text(encoding="utf-8").strip()
            payload = json.loads(raw)
            self.assertEqual(payload["final_action"], "REJECT")


class TestTradeLedger(unittest.TestCase):
    def test_record_mfe_mae(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trade_ledger.jsonl"
            ledger = TradeLedger(path, enabled=True)
            ledger.record(
                TelemetryTradeRecord(
                    trade_id=1,
                    candidate_id="c1",
                    pair="BTC/USDT:USDT",
                    side="long",
                    open_date="2024-01-01T00:00:00+00:00",
                    close_date="2024-01-02T00:00:00+00:00",
                    open_rate=100.0,
                    close_rate=105.0,
                    stake_amount=100.0,
                    leverage=10.0,
                    pnl_usdt=50.0,
                    pnl_pct=0.05,
                    max_favorable_excursion=0.08,
                    max_adverse_excursion=-0.02,
                    exit_reason="roi",
                )
            )
            rows = list(ledger.load_from_disk())
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0].max_favorable_excursion, 0.08)
            self.assertEqual(rows[0].exit_reason, "roi")

    def test_disabled_skips_write(self):
        ledger = TradeLedger(enabled=False)
        ledger.record_dict(
            trade_id=1,
            candidate_id="c1",
            pair="BTC/USDT:USDT",
            side="long",
            open_date="",
            close_date="",
            open_rate=1.0,
            close_rate=1.0,
            stake_amount=1.0,
            leverage=1.0,
            pnl_usdt=0.0,
            pnl_pct=0.0,
            max_favorable_excursion=0.0,
            max_adverse_excursion=0.0,
            exit_reason="",
        )
        self.assertEqual(ledger.all(), [])


class TestStrategyOrderFilledTradeLedger(unittest.TestCase):
    def test_exit_fill_records_trade(self):
        from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper
        from strategies.mtf_sniper.telemetry import TradeLedger

        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat.enable_telemetry = True
        strat._trade_ledger = TradeLedger(enabled=True)
        strat._trade_ledger_recorded_ids = set()
        strat._decision_ledger = None

        now = datetime(2024, 1, 2, tzinfo=UTC)
        trade = _trade(
            id=42,
            is_short=False,
            open_rate=100.0,
            close_rate=None,
            max_rate=108.0,
            min_rate=97.0,
            stake_amount=100.0,
            leverage=10.0,
            close_profit=None,
            close_profit_abs=None,
            exit_reason="roi",
            enter_tag="long_A80_plus",
            amount=1.0,
            exit_side="sell",
            open_date_utc=datetime(2024, 1, 1, tzinfo=UTC),
            close_date_utc=None,
            is_open=True,
        )
        trade.calc_profit_ratio = lambda rate: (rate - 100.0) / 100.0
        trade.calc_profit = lambda rate: rate - 100.0

        order = cast(
            Order,
            SimpleNamespace(
                ft_order_side="sell",
                safe_amount=1.0,
                filled=1.0,
                average=105.0,
                price=105.0,
                ft_price=105.0,
            ),
        )
        strat.order_filled("BTC/USDT:USDT", cast(Trade, trade), order, now)
        self.assertEqual(len(strat._trade_ledger.all()), 1)
        row = strat._trade_ledger.all()[0]
        self.assertEqual(row.trade_id, 42)
        self.assertAlmostEqual(row.close_rate, 105.0)
        self.assertAlmostEqual(row.max_favorable_excursion, 0.08)
        self.assertAlmostEqual(row.max_adverse_excursion, -0.03)
        # Dedup
        strat.order_filled("BTC/USDT:USDT", cast(Trade, trade), order, now)
        self.assertEqual(len(strat._trade_ledger.all()), 1)

    def test_entry_fill_ignored(self):
        from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper
        from strategies.mtf_sniper.telemetry import TradeLedger

        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat.enable_telemetry = True
        strat._trade_ledger = TradeLedger(enabled=True)
        strat._trade_ledger_recorded_ids = set()
        trade = _trade(is_open=True, amount=1.0, exit_side="sell")
        order = cast(
            Order,
            SimpleNamespace(ft_order_side="buy", safe_amount=1.0, filled=1.0),
        )
        strat.order_filled(
            "BTC/USDT:USDT", cast(Trade, trade), order, datetime(2024, 1, 1, tzinfo=UTC)
        )
        self.assertEqual(strat._trade_ledger.all(), [])


class TestRiskEngineDecisionLedger(unittest.TestCase):

    def test_authorize_logs_reject(self):
        ledger = DecisionLedger(enabled=True)
        engine = RiskEngine(decision_ledger=ledger)
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
            entry_tag="long_A80_plus",
        )
        self.assertFalse(auth.is_approved)
        self.assertEqual(len(ledger.rejected()), 1)
        row = ledger.rejected()[0]
        self.assertIn(RejectionReasonCode.RISK_DIRECTIONAL_LOCK.value, row.final_reasons)
        self.assertEqual(row.sensors_triggered, ["long_A80_plus"])
        self.assertEqual(row.final_action, "REJECT")

    def test_authorize_logs_enter(self):
        ledger = DecisionLedger(enabled=True)
        engine = RiskEngine(decision_ledger=ledger)
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
            entry_tag="long_A80_plus",
        )
        self.assertTrue(auth.is_approved)
        self.assertEqual(len(ledger.entered()), 1)
        self.assertEqual(ledger.entered()[0].final_action, "ENTER")

    def test_authorize_records_jev_detail_and_dedups_reject_burst(self):
        ledger = DecisionLedger(enabled=True)
        engine = RiskEngine(decision_ledger=ledger)
        t0 = datetime(2024, 1, 10, 0, 1, tzinfo=UTC)
        t1 = datetime(2024, 1, 10, 0, 2, tzinfo=UTC)
        t_next_bar = datetime(2024, 1, 10, 0, 16, tzinfo=UTC)

        # Repeated identical reject in same 15m candle should log once.
        for ts in (t0, t1):
            engine.authorize_entry(
                strategy_name="StrategyMTFSniper",
                pair="BTC/USDT:USDT",
                side="long",
                current_time=ts,
                closed_trades=[],
                open_trades=[_trade(is_short=True)],
                get_price=lambda p, t: 100.0,
                candidate_id="cid_1",
                entry_tag="long_A80_plus",
            )
        self.assertEqual(len(ledger.rejected()), 1)

        # Next 15m candle logs again.
        engine.authorize_entry(
            strategy_name="StrategyMTFSniper",
            pair="BTC/USDT:USDT",
            side="long",
            current_time=t_next_bar,
            closed_trades=[],
            open_trades=[_trade(is_short=True)],
            get_price=lambda p, t: 100.0,
            candidate_id="cid_1",
            entry_tag="long_A80_plus",
        )
        self.assertEqual(len(ledger.rejected()), 2)

        # Dict verdict populates AI telemetry fields accurately.
        auth = engine.authorize_entry(
            strategy_name="StrategyMTFSniper",
            pair="BTC/USDT:USDT",
            side="short",
            current_time=t_next_bar,
            closed_trades=[],
            open_trades=[],
            get_price=lambda p, t: 100.0,
            jev_confirm=lambda: {
                "approved": True,
                "ai_verdict": "CONFIRM",
                "ai_confidence": 0.88,
                "ai_model_version": "native-system1-v2.7.2",
                "market_regime": "TREND_EXPANSION",
            },
            policy_score=0.9,
            entry_tag="short_A80_plus",
            candidate_id="cid_2",
        )
        self.assertTrue(auth.is_approved)
        row = ledger.entered()[0]
        self.assertEqual(row.ai_verdict, "CONFIRM")
        self.assertAlmostEqual(row.ai_confidence, 0.88)
        self.assertEqual(row.ai_model_version, "native-system1-v2.7.2")
        self.assertEqual(row.market_regime, "TREND_EXPANSION")
        self.assertAlmostEqual(row.policy_score, 0.9)

    def test_bounded_buffer_and_size_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decision_ledger.jsonl"
            ledger = DecisionLedger(path, enabled=True, max_buffer=2, max_file_bytes=200)
            for idx in range(3):
                ledger.record_dict(
                    candidate_id=f"c{idx}",
                    timestamp="2024-01-10T00:00:00+00:00",
                    pair="BTC/USDT:USDT",
                    side="long",
                    sensors_triggered=["long_A80_plus"],
                    confluence_count=1,
                    market_regime="",
                    policy_score=0.0,
                    quality_grade="ACCEPT",
                    policy_verdict="ACCEPT",
                    ai_verdict="CONFIRM",
                    ai_confidence=0.8,
                    ai_model_version=None,
                    risk_approved=True,
                    final_action="ENTER",
                    final_reasons=[],
                )
            self.assertEqual([r.candidate_id for r in ledger.all()], ["c1", "c2"])
            self.assertTrue(path.with_name("decision_ledger.jsonl.1").exists())

    def test_confirm_trade_entry_passes_stake_and_equity(self):
        from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper

        strat = StrategyMTFSniper(config={"stake_currency": "USDT", "strategy_params": {}})
        strat.enable_jev_decision = False
        strat.enable_telemetry = False
        strat.dp = SimpleNamespace(get_analyzed_dataframe=lambda pair, tf: (None, None))
        strat.wallets = SimpleNamespace(get_total=lambda ccy: 2000.0 if ccy == "USDT" else 0.0)
        captured: dict = {}
        orig_auth = strat._get_risk_engine().authorize_entry

        def _spy(**kwargs):
            captured.update(kwargs)
            return orig_auth(**kwargs)

        with (
            patch.object(strat._get_risk_engine(), "authorize_entry", side_effect=_spy),
            patch("freqtrade.persistence.Trade.get_trades_proxy", return_value=[]),
        ):
            ok = strat.confirm_trade_entry(
                "BTC/USDT:USDT",
                "limit",
                amount=0.5,
                rate=1000.0,
                time_in_force="GTC",
                current_time=datetime(2024, 1, 10, tzinfo=UTC),
                entry_tag="long_A80_plus",
                side="long",
            )
        self.assertTrue(ok)
        # 0.5 * 1000 / 10x leverage = 50.0 USDT stake; wallet_equity = 2000.0
        self.assertAlmostEqual(captured["proposed_stake"], 50.0)
        self.assertAlmostEqual(captured["wallet_equity"], 2000.0)
        self.assertFalse(captured["enable_dynamic_sizing"])


if __name__ == "__main__":
    unittest.main()
