"""Unit tests for soft-reject → JEV Hunter escalation + post-AI gates."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import RejectionReasonCode, TradeSide
from strategies.mtf_sniper.directional.policy_escalation import is_soft_policy_reject
from strategies.mtf_sniper.execution.entry_pipeline import EntryPipeline
from strategies.mtf_sniper.intelligence.hunter_escalation import (
    HunterEscalationConfig,
    HunterEscalationGateway,
    sensor_to_event_trigger,
)
from strategies.mtf_sniper.scoring import ScoreConfig


def _candidate(
    side: TradeSide = TradeSide.SHORT,
    primary: str = "volume_surge",
    idx: int = 40,
) -> TradeCandidate:
    return TradeCandidate(
        candidate_id=f"c_{side.value}_{idx}",
        pair="BTC/USDT:USDT",
        side=side,
        primary_sensor=primary,
        secondary_sensors=[],
        confluence_count=1,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        candle_index=idx,
        raw_features={},
    )


def _reject(*codes: str, score: float = 0.7) -> PolicyEvaluationResult:
    return PolicyEvaluationResult(
        is_valid=False,
        score=score,
        quality_grade="REJECT",
        reasons=list(codes),
        suggested_stop_distance_pct=0.018,
        target_rr=2.0,
        metadata={"setup": "test"},
    )


def _gw_config(**kwargs: Any) -> HunterEscalationConfig:
    """Default test config: scorer always passes so Hunter path can be asserted."""
    base: dict[str, Any] = {
        "enabled": True,
        "allow_long_promotion": False,
        "score": ScoreConfig(pass_threshold=0.0),
    }
    base.update(kwargs)
    return HunterEscalationConfig(**base)


def _df(n: int = 50) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 100.0),
            "is_bull_wave_4h": np.zeros(n),
            "is_bear_wave_4h": np.ones(n),
            "ema_50_1h": np.full(n, 102.0),
            "atr_pct": np.full(n, 55.0),
        }
    )


def _promote_row(**overrides: Any) -> dict[str, Any]:
    """Row that clears legacy short waterfall post-AI gates."""
    row: dict[str, Any] = {
        "close": 100.0,
        "open": 101.0,
        "high": 101.2,
        "low": 99.5,
        "lower_wick_ratio": 0.10,
        "close_pos_ratio": 0.20,
        "ema_21": 100.5,
        "rsi": 40.0,
        "swing_low_20d_1d": 90.0,
        "atr_pct": 55.0,
        "adx_4h": 25.0,
        "bb_width_4h": 0.05,
        "is_bear_wave_4h": 1,
        "is_bull_wave_4h": 0,
        "is_macro_bear_market_1d": 1,
        "is_macro_bull_run_1d": 0,
        "macdhist_4h": -0.5,
        "ema_50_1d": 110.0,
    }
    row.update(overrides)
    return row


class _FakeHunter:
    def __init__(self, verdict: dict[str, Any]) -> None:
        self.verdict = verdict
        self.calls = 0

    def evaluate_candidate(self, df_32, pair, ts_str, event_type, macro_info):
        self.calls += 1
        return dict(self.verdict)


class TestSoftRejectClassification(unittest.TestCase):
    def test_soft_wick_only(self):
        r = _reject(RejectionReasonCode.REJECT_UPPER_WICK_REJECTION.value)
        self.assertTrue(is_soft_policy_reject(r))

    def test_hard_regime(self):
        r = _reject(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)
        self.assertFalse(is_soft_policy_reject(r))

    def test_mixed_hard_wins(self):
        r = _reject(
            RejectionReasonCode.REJECT_RSI_OVEREXTENDED.value,
            RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value,
        )
        self.assertFalse(is_soft_policy_reject(r))

    def test_valid_not_soft(self):
        r = PolicyEvaluationResult(
            is_valid=True,
            score=0.9,
            quality_grade="A",
            reasons=["ACCEPT"],
            suggested_stop_distance_pct=0.018,
            target_rr=2.0,
        )
        self.assertFalse(is_soft_policy_reject(r))


class TestHunterEscalationGateway(unittest.TestCase):
    def test_promotes_soft_short(self):
        fake = _FakeHunter(
            {
                "action": "enter_short",
                "setup_type": "waterfall_collapse",
                "confidence": 0.85,
                "status": "CONFIRMED",
            }
        )
        gw = HunterEscalationGateway(fake, _gw_config(allow_long_promotion=False))
        cand = _candidate(TradeSide.SHORT, idx=40)
        soft = _reject(RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value)
        out = gw.evaluate(cand, soft, _df(), _promote_row())
        self.assertEqual(out.outcome, "promoted")
        self.assertIsNotNone(out.promoted)
        assert out.promoted is not None
        self.assertTrue(out.promoted.is_valid)
        self.assertEqual(out.promoted.metadata["entry_tag"], "jev_hunter_waterfall_collapse")
        self.assertEqual(fake.calls, 1)

    def test_post_gate_rejects_low_confidence(self):
        fake = _FakeHunter(
            {
                "action": "enter_short",
                "setup_type": "waterfall_collapse",
                "confidence": 0.70,
                "status": "CONFIRMED",
            }
        )
        gw = HunterEscalationGateway(fake, _gw_config())
        cand = _candidate(TradeSide.SHORT)
        soft = _reject(RejectionReasonCode.REJECT_EMA_DISTANCE_TOO_FAR.value)
        out = gw.evaluate(cand, soft, _df(), _promote_row())
        self.assertEqual(out.outcome, "post_gate_rejected")
        self.assertIn("CONF_BELOW_THRESHOLD", out.gate_reasons or [])
        self.assertEqual(fake.calls, 1)

    def test_post_gate_rejects_sideway(self):
        fake = _FakeHunter(
            {
                "action": "enter_short",
                "setup_type": "waterfall_collapse",
                "confidence": 0.90,
                "status": "CONFIRMED",
            }
        )
        gw = HunterEscalationGateway(fake, _gw_config())
        cand = _candidate(TradeSide.SHORT)
        soft = _reject(RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value)
        out = gw.evaluate(
            cand, soft, _df(), _promote_row(adx_4h=10.0, bb_width_4h=0.01)
        )
        self.assertEqual(out.outcome, "post_gate_rejected")
        self.assertIn("SIDEWAY_UNSAFE", out.gate_reasons or [])

    def test_rejects_non_waterfall_setup(self):
        fake = _FakeHunter(
            {
                "action": "enter_short",
                "setup_type": "other_setup",
                "confidence": 0.95,
                "status": "CONFIRMED",
            }
        )
        gw = HunterEscalationGateway(fake, _gw_config())
        out = gw.evaluate(
            _candidate(TradeSide.SHORT),
            _reject(RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value),
            _df(),
            _promote_row(),
        )
        self.assertEqual(out.outcome, "post_gate_rejected")
        self.assertIn("SETUP_NOT_WATERFALL", out.gate_reasons or [])

    def test_hard_reject_skips_hunter(self):
        fake = _FakeHunter({"action": "enter_short", "status": "CONFIRMED", "confidence": 0.9})
        gw = HunterEscalationGateway(fake, _gw_config())
        cand = _candidate(TradeSide.SHORT)
        hard = _reject(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)
        out = gw.evaluate(cand, hard, _df(), _promote_row())
        self.assertEqual(out.outcome, "hard_or_ineligible")
        self.assertEqual(fake.calls, 0)

    def test_long_blocked_without_flag(self):
        fake = _FakeHunter(
            {
                "action": "enter_long",
                "setup_type": "breakout_expansion",
                "confidence": 0.9,
                "status": "CONFIRMED",
            }
        )
        gw = HunterEscalationGateway(fake, _gw_config(allow_long_promotion=False))
        cand = _candidate(TradeSide.LONG, primary="swing_breakout")
        soft = _reject(RejectionReasonCode.REJECT_RSI_OVEREXTENDED.value)
        out = gw.evaluate(cand, soft, _df(), _promote_row())
        self.assertEqual(out.outcome, "hard_or_ineligible")
        self.assertEqual(fake.calls, 0)

    def test_event_trigger_mapping(self):
        self.assertEqual(
            sensor_to_event_trigger(_candidate(TradeSide.SHORT, "waterfall_drop")),
            "MEGA_SURGE_SHORT",
        )
        self.assertEqual(
            sensor_to_event_trigger(_candidate(TradeSide.LONG, "swing_breakout")),
            "BREAKOUT_24H_LONG",
        )


class TestEntryPipelineHunterWire(unittest.TestCase):
    def test_pipeline_promotes_via_gateway(self):
        from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine
        from strategies.mtf_sniper.directional.long.policy import LongPolicy
        from strategies.mtf_sniper.directional.short.policy import ShortPolicy
        from strategies.mtf_sniper.signals.sensor_suite import SensorSuite

        class AlwaysSoftShortPolicy(ShortPolicy):
            def evaluate(self, candidate, row):
                return _reject(RejectionReasonCode.REJECT_LOWER_WICK_REJECTION.value)

        class QuietLongPolicy(LongPolicy):
            def evaluate(self, candidate, row):
                return _reject(RejectionReasonCode.REJECT_REGIME_INCOMPATIBLE.value)

        fake = _FakeHunter(
            {
                "action": "enter_short",
                "setup_type": "waterfall_collapse",
                "confidence": 0.9,
                "status": "CONFIRMED",
            }
        )
        gw = HunterEscalationGateway(fake, _gw_config())

        from strategies.mtf_sniper.core.contracts import TriggerEvent
        from strategies.mtf_sniper.core.enums import SensorType
        from strategies.mtf_sniper.signals.base_sensor import BaseSensor

        class OneShotShortSensor(BaseSensor):
            name = "volume_surge"
            sensor_type = SensorType.ANOMALY

            def scan(self, dataframe, pair, candle_index=None):
                i = 40
                return [
                    TriggerEvent(
                        sensor_name=self.name,
                        sensor_type=self.sensor_type,
                        side=TradeSide.SHORT,
                        pair=pair,
                        timestamp=dataframe.iloc[i]["date"].to_pydatetime(),
                        candle_index=i,
                        raw_metrics={},
                    )
                ]

        n = 50
        df = _df(n)
        # Attach promote-safe columns so pipeline row lookup clears post gates.
        for k, v in _promote_row().items():
            if k not in df.columns:
                df[k] = v

        engine = DirectionalTradingEngine(
            long_policy=QuietLongPolicy(),
            short_policy=AlwaysSoftShortPolicy(),
        )
        pipe = EntryPipeline(
            sensor_suite=SensorSuite([OneShotShortSensor()]),
            engine=engine,
            hunter_escalation=gw,
            builder_cooldown_bars=1,
        )
        result = pipe.run(df, "BTC/USDT:USDT")
        self.assertTrue(bool(result.enter_short.iloc[40]))
        self.assertIn("jev_hunter_", str(result.enter_tag.iloc[40]))
        self.assertEqual(len(result.hunter_promoted), 1)


if __name__ == "__main__":
    unittest.main()
