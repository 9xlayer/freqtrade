"""Telemetry ledgers must pickle for freqtrade hyperopt parallel workers."""
from __future__ import annotations

import pickle
import unittest

from strategies.mtf_sniper.core.contracts import TelemetryDecisionRecord, TelemetryTradeRecord
from strategies.mtf_sniper.telemetry import DecisionLedger, TradeLedger


def _decision_row() -> TelemetryDecisionRecord:
    return TelemetryDecisionRecord(
        candidate_id="c1",
        timestamp="2026-01-01T00:00:00+00:00",
        pair="BTC/USDT:USDT",
        side="long",
        sensors_triggered=["s1"],
        confluence_count=1,
        market_regime="bull",
        policy_score=0.8,
        quality_grade="A",
        policy_verdict="ACCEPT",
        ai_verdict="CONFIRM",
        ai_confidence=0.7,
        ai_model_version=None,
        risk_approved=True,
        final_action="ENTER",
        final_reasons=[],
    )


def _trade_row() -> TelemetryTradeRecord:
    return TelemetryTradeRecord(
        trade_id=1,
        candidate_id="c1",
        pair="BTC/USDT:USDT",
        side="short",
        open_date="2026-01-01T00:00:00+00:00",
        close_date="2026-01-01T01:00:00+00:00",
        open_rate=100.0,
        close_rate=99.0,
        stake_amount=100.0,
        leverage=10.0,
        pnl_usdt=1.0,
        pnl_pct=0.01,
        max_favorable_excursion=0.02,
        max_adverse_excursion=-0.01,
        exit_reason="roi",
    )


class TestTelemetryPickle(unittest.TestCase):
    def test_decision_ledger_roundtrip(self) -> None:
        ledger = DecisionLedger(enabled=True)
        ledger.record(_decision_row())
        clone = pickle.loads(pickle.dumps(ledger))
        self.assertTrue(hasattr(clone, "_lock"))
        self.assertEqual(len(clone.all()), 1)
        clone.record(_decision_row())
        self.assertEqual(len(clone.all()), 2)

    def test_trade_ledger_roundtrip(self) -> None:
        ledger = TradeLedger(enabled=True)
        ledger.record(_trade_row())
        clone = pickle.loads(pickle.dumps(ledger))
        self.assertTrue(hasattr(clone, "_lock"))
        self.assertEqual(len(clone.all()), 1)
        clone.record(_trade_row())
        self.assertEqual(len(clone.all()), 2)


if __name__ == "__main__":
    unittest.main()
