"""Runtime mode matrix + Decision adapter isolation (no global env mutation)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from strategies.mtf_sniper.core.runtime_mode import (
    InvalidRuntimeModeError,
    RuntimeMode,
    build_runtime_profile,
    resolve_runtime_mode,
)
from strategies.mtf_sniper.intelligence.decision_adapter import JevDecisionAdapter
from strategies.mtf_sniper.intelligence.jev_decision import JevDecisionEngine


class TestRuntimeMode(unittest.TestCase):
    def test_deterministic_forces_native_decision_and_ignores_stale_keys(self):
        profile = build_runtime_profile(
            {
                "mtf_runtime_mode": "deterministic",
                "enable_jev_hunter": True,
                "enable_jev_decision": True,
            },
            enable_jev_decision_cfg=True,
        )
        self.assertEqual(profile.mode, RuntimeMode.DETERMINISTIC)
        self.assertTrue(profile.enable_jev_decision)
        self.assertFalse(profile.allow_external_ai)
        self.assertEqual(profile.decision_jev_mode, "native")

    def test_pure_deterministic_forces_decision_off(self):
        profile = build_runtime_profile(
            {
                "mtf_runtime_mode": "pure_deterministic",
                "enable_jev_decision": True,
            },
            enable_jev_decision_cfg=True,
        )
        self.assertEqual(profile.mode, RuntimeMode.PURE_DETERMINISTIC)
        self.assertFalse(profile.enable_jev_decision)
        self.assertFalse(profile.allow_external_ai)

    def test_pure_alias(self):
        profile = build_runtime_profile({"mtf_runtime_mode": "pure"})
        self.assertEqual(profile.mode, RuntimeMode.PURE_DETERMINISTIC)
        self.assertFalse(profile.enable_jev_decision)

    def test_decision_native_blocks_external(self):
        profile = build_runtime_profile(
            {"mtf_runtime_mode": "decision_native", "enable_jev_decision": True},
            enable_jev_decision_cfg=True,
        )
        self.assertEqual(profile.mode, RuntimeMode.DECISION_NATIVE)
        self.assertTrue(profile.enable_jev_decision)
        self.assertEqual(profile.decision_jev_mode, "native")
        self.assertFalse(profile.allow_external_ai)

    def test_decision_external_allows_ai(self):
        profile = build_runtime_profile(
            {
                "mtf_runtime_mode": "decision_external",
                "enable_jev_decision": True,
            },
            enable_jev_decision_cfg=True,
        )
        self.assertEqual(profile.mode, RuntimeMode.DECISION_EXTERNAL)
        self.assertTrue(profile.allow_external_ai)
        self.assertEqual(profile.decision_jev_mode, "external")

    def test_deterministic_native_alias(self):
        profile = build_runtime_profile({"mtf_runtime_mode": "deterministic_native"})
        self.assertEqual(profile.mode, RuntimeMode.DETERMINISTIC)

    def test_invalid_mode_fails_fast(self):
        with self.assertRaises(InvalidRuntimeModeError):
            resolve_runtime_mode({"mtf_runtime_mode": "pure_determinstic"})
        with self.assertRaises(InvalidRuntimeModeError):
            build_runtime_profile(
                {"mtf_runtime_mode": "pure_determinstic", "enable_jev_decision": True},
                enable_jev_decision_cfg=True,
            )


class TestDeterministicDecisionNoExternalAI(unittest.TestCase):
    def test_force_native_blocks_external_engine_branch(self):
        prev = os.environ.get("JEV_MODE")
        try:
            os.environ["JEV_MODE"] = "external"
            adapter = JevDecisionAdapter(force_native=True)
            self.assertEqual(adapter.mode, "native")
            # Adapter must not clobber process env — mode is constructor-only.
            self.assertEqual(os.environ.get("JEV_MODE"), "external")
            with patch.object(
                adapter._engine,
                "_call_external_jev",
                side_effect=AssertionError("external AI must not be called"),
            ) as spy_ext:
                adapter.evaluate(
                    {
                        "pair": "BTC/USDT:USDT",
                        "side": "long",
                        "timestamp": "2024-01-01T00:00:00+00:00",
                        "sequence": [{"close": 1.0, "open": 1.0, "high": 1.0, "low": 1.0}],
                    }
                )
                spy_ext.assert_not_called()
            self.assertEqual(adapter.external_calls, 0)
        finally:
            if prev is None:
                os.environ.pop("JEV_MODE", None)
            else:
                os.environ["JEV_MODE"] = prev

    def test_two_adapters_do_not_cross_contaminate_modes(self):
        prev = os.environ.get("JEV_MODE")
        try:
            os.environ["JEV_MODE"] = "external"
            native = JevDecisionAdapter(force_native=True, mode="native")
            external = JevDecisionAdapter(force_native=False, mode="external")
            self.assertEqual(native.mode, "native")
            self.assertEqual(external.mode, "external")
            self.assertEqual(os.environ.get("JEV_MODE"), "external")
        finally:
            if prev is None:
                os.environ.pop("JEV_MODE", None)
            else:
                os.environ["JEV_MODE"] = prev

    def test_decision_engine_dotenv_does_not_mutate_environ(self):
        prev = dict(os.environ)
        try:
            # Ensure a key we can detect is absent before init
            os.environ.pop("JEV_DECISION_MODEL_VERSION", None)
            before = dict(os.environ)
            JevDecisionEngine(mode="native")
            self.assertEqual(os.environ, before)
        finally:
            os.environ.clear()
            os.environ.update(prev)

    def test_adapter_propagates_fail_policy_and_eth_base_pair_match(self):
        adapter = JevDecisionAdapter(force_native=True, mode="native", fail_policy="allow")
        self.assertEqual(adapter.fail_policy, "allow")
        adapter.fail_policy = "reject"
        self.assertEqual(adapter._engine.fail_policy, "reject")

        # ETHFI must not trigger ETH-only require_below_ema50_4h checks.
        engine = JevDecisionEngine(mode="native")
        payload = {
            "pair": "ETHFI/USDT:USDT",
            "side": "SHORT",
            "setup": "waterfall_collapse",
            "trajectory_sequence": [
                {
                    "body_ratio": 0.7,
                    "vol_ratio": 1.0,
                    "lower_wick": 0.1,
                    "range_pct": 0.5,
                    "close_pos": 0.2,
                }
                for _ in range(6)
            ],
            "macro_context": {
                "trend_4h": "BEAR",
                "atr_percentile": 95.0,
                "short_score": 85.0,
                "floor_20d_dist_pct": -1.0,
                "ema_50_1h_dist_pct": -1.0,
                "rsi_1h": 40.0,
                "rel_vol": 3.5,
            },
        }
        verdict = engine._evaluate_native_sequence(payload)
        self.assertEqual(verdict["decision"], "CONFIRM")


if __name__ == "__main__":
    unittest.main()
