"""Runtime mode matrix + Decision adapter isolation (no global env mutation)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from strategies.mtf_sniper.core.runtime_mode import RuntimeMode, build_runtime_profile
from strategies.mtf_sniper.intelligence.decision_adapter import JevDecisionAdapter


class TestRuntimeMode(unittest.TestCase):
    def test_deterministic_forces_hunter_off_and_native_decision(self):
        profile = build_runtime_profile(
            {
                "mtf_runtime_mode": "deterministic",
                "enable_jev_hunter": True,
                "enable_jev_decision": True,
            },
            enable_jev_hunter_cfg=True,
            enable_hunter_long_cfg=True,
            enable_jev_decision_cfg=True,
        )
        self.assertEqual(profile.mode, RuntimeMode.DETERMINISTIC)
        self.assertFalse(profile.enable_jev_hunter)
        self.assertFalse(profile.enable_hunter_long)
        self.assertTrue(profile.enable_jev_decision)
        self.assertFalse(profile.allow_external_ai)
        self.assertEqual(profile.decision_jev_mode, "native")

    def test_pure_deterministic_forces_decision_off(self):
        profile = build_runtime_profile(
            {
                "mtf_runtime_mode": "pure_deterministic",
                "enable_jev_decision": True,
                "enable_jev_hunter": True,
            },
            enable_jev_decision_cfg=True,
            enable_jev_hunter_cfg=True,
            enable_hunter_long_cfg=True,
        )
        self.assertEqual(profile.mode, RuntimeMode.PURE_DETERMINISTIC)
        self.assertFalse(profile.enable_jev_decision)
        self.assertFalse(profile.enable_jev_hunter)
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
        self.assertFalse(profile.enable_jev_hunter)

    def test_decision_external_allows_ai_keeps_hunter_off_by_default(self):
        profile = build_runtime_profile(
            {
                "mtf_runtime_mode": "decision_external",
                "enable_jev_decision": True,
                "enable_jev_hunter": False,
            },
            enable_jev_decision_cfg=True,
            enable_jev_hunter_cfg=False,
        )
        self.assertEqual(profile.mode, RuntimeMode.DECISION_EXTERNAL)
        self.assertTrue(profile.allow_external_ai)
        self.assertEqual(profile.decision_jev_mode, "external")
        self.assertFalse(profile.enable_jev_hunter)

    def test_deterministic_native_alias(self):
        profile = build_runtime_profile({"mtf_runtime_mode": "deterministic_native"})
        self.assertEqual(profile.mode, RuntimeMode.DETERMINISTIC)


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


if __name__ == "__main__":
    unittest.main()
