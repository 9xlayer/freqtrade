"""Verify DATA_CONTRACTS.md stays aligned with core dataclasses / candidate IDs."""
from __future__ import annotations

import unittest
from dataclasses import fields, is_dataclass
from datetime import datetime, UTC

from strategies.mtf_sniper.core.candidate_id import (
    CANDIDATE_ID_CUSTOM_DATA_KEY,
    CANDIDATE_ID_MISSING,
    is_canonical_candidate_id,
    make_candidate_id,
    parse_candidate_id,
)
from strategies.mtf_sniper.core.contracts import (
    DecisionVerdict,
    PolicyEvaluationResult,
    RiskAuthorization,
    TelemetryDecisionRecord,
    TelemetryTradeRecord,
    TradeCandidate,
    TriggerEvent,
)
from strategies.mtf_sniper.core.enums import TradeSide


def _field_names(cls) -> set[str]:
    assert is_dataclass(cls)
    return {f.name for f in fields(cls)}


class TestDataContractShapes(unittest.TestCase):
    def test_all_core_contracts_are_frozen(self):
        for cls in (
            TriggerEvent,
            TradeCandidate,
            PolicyEvaluationResult,
            DecisionVerdict,
            RiskAuthorization,
            TelemetryDecisionRecord,
            TelemetryTradeRecord,
        ):
            self.assertTrue(cls.__dataclass_params__.frozen, cls.__name__)

    def test_trade_candidate_required_fields(self):
        expected = {
            "candidate_id",
            "pair",
            "side",
            "primary_sensor",
            "secondary_sensors",
            "confluence_count",
            "timestamp",
            "candle_index",
            "raw_features",
            "cooldown_bars",
        }
        self.assertEqual(_field_names(TradeCandidate), expected)

    def test_policy_evaluation_has_eligibility_and_grade(self):
        names = _field_names(PolicyEvaluationResult)
        self.assertIn("is_valid", names)
        self.assertIn("quality_grade", names)
        self.assertIn("reasons", names)

    def test_risk_authorization_honest_optional_budget_fields(self):
        names = _field_names(RiskAuthorization)
        for required in (
            "is_approved",
            "stake_amount",
            "leverage",
            "stoploss_rate",
            "rejection_reasons",
            "total_exposure_notional",
            "sizing_method",
            "config_fingerprint",
            "stage_rejected",
            "risk_budget_used_pct",
            "risk_per_trade_pct",
            "total_exposure_pct",
            "wallet_equity",
        ):
            self.assertIn(required, names)
        # Defaults: unavailable metrics are None; sizing is FT-proposed.
        auth = RiskAuthorization(
            is_approved=True,
            stake_amount=100.0,
            leverage=10.0,
            stoploss_rate=-0.25,
            rejection_reasons=[],
        )
        self.assertIsNone(auth.risk_budget_used_pct)
        self.assertIsNone(auth.risk_per_trade_pct)
        self.assertEqual(auth.sizing_method, "freqtrade_proposed")

    def test_telemetry_decision_extended_fields(self):
        names = _field_names(TelemetryDecisionRecord)
        for extra in (
            "setup_type",
            "signal_source",
            "policy_version",
            "config_fingerprint",
            "feature_snapshot",
        ):
            self.assertIn(extra, names)


class TestCandidateIdContract(unittest.TestCase):
    def test_canonical_includes_candle_index(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        parsed = parse_candidate_id(cid)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.candle_index, 42)
        self.assertTrue(cid.endswith("_42"))
        # Legacy shape without candle index must fail
        legacy = f"BTC/USDT:USDT_short_{int(ts.timestamp())}"
        self.assertFalse(is_canonical_candidate_id(legacy))

    def test_make_parse_make_round_trip(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        for side in (TradeSide.LONG, TradeSide.SHORT, "long", "short"):
            original = make_candidate_id("ETH/USDT:USDT", side, ts, 7)
            parsed = parse_candidate_id(original)
            self.assertIsNotNone(parsed)
            remade = make_candidate_id(
                parsed.pair, parsed.side, datetime.fromtimestamp(parsed.epoch, tz=UTC), parsed.candle_index
            )
            self.assertEqual(original, remade)
            self.assertTrue(is_canonical_candidate_id(original))

    def test_rejects_uppercase_side_and_leading_zeros(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        epoch = int(ts.timestamp())
        self.assertIsNone(parse_candidate_id(f"BTC/USDT:USDT_LONG_{epoch}_1"))
        self.assertIsNone(parse_candidate_id(f"BTC/USDT:USDT_Short_{epoch}_1"))
        self.assertIsNone(parse_candidate_id(f"BTC/USDT:USDT_short_0{epoch}_1"))
        self.assertIsNone(parse_candidate_id(f"BTC/USDT:USDT_short_{epoch}_01"))
        with self.assertRaises(ValueError):
            make_candidate_id("BTC/USDT:USDT", "LONG", ts, 1)
        with self.assertRaises(ValueError):
            make_candidate_id("BTCUSDT", TradeSide.LONG, ts, 1)

    def test_rejects_negative_candle_index(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        bad = f"BTC/USDT:USDT_long_{int(ts.timestamp())}_-1"
        self.assertFalse(is_canonical_candidate_id(bad))
        self.assertIsNone(parse_candidate_id(bad))
        with self.assertRaises(ValueError):
            make_candidate_id("BTC/USDT:USDT", TradeSide.LONG, ts, -1)

    def test_rejects_non_integral_candle_index_and_bad_timestamp(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        # No silent float truncate
        for bad_idx in (12.0, 12.9, True, False, "12"):
            with self.assertRaises(ValueError):
                make_candidate_id("BTC/USDT:USDT", TradeSide.LONG, ts, bad_idx)
        # Valid exact int
        cid = make_candidate_id("BTC/USDT:USDT", TradeSide.LONG, ts, 12)
        self.assertTrue(cid.endswith("_12"))
        # Bad timestamps
        for bad_ts in (1717243200, 1717243200.5, True, "2024-06-01"):
            with self.assertRaises(ValueError):
                make_candidate_id("BTC/USDT:USDT", TradeSide.LONG, bad_ts, 1)

    def test_missing_sentinel_and_custom_data_key(self):
        self.assertEqual(CANDIDATE_ID_CUSTOM_DATA_KEY, "mtf_candidate_id")
        self.assertFalse(is_canonical_candidate_id(CANDIDATE_ID_MISSING))


if __name__ == "__main__":
    unittest.main()
