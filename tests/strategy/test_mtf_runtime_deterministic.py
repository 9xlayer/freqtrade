"""Phase 1 — deterministic runtime mode must not execute external AI inference."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from strategies.mtf_sniper.core.runtime_mode import RuntimeMode, build_runtime_profile
from strategies.mtf_sniper.intelligence.decision_adapter import JevDecisionAdapter


class TestRuntimeMode(unittest.TestCase):
    def test_deterministic_forces_hunter_off_and_native_decision(self):
        profile = build_runtime_profile(
            {"mtf_runtime_mode": "deterministic", "enable_jev_hunter": True, "enable_jev_decision": True},
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


class TestDeterministicDecisionNoExternalAI(unittest.TestCase):
    def test_force_native_blocks_external_engine_branch(self):
        prev = os.environ.get("JEV_MODE")
        try:
            os.environ["JEV_MODE"] = "external"
            adapter = JevDecisionAdapter(force_native=True)
            self.assertEqual(adapter.mode, "native")
            with patch.object(
                adapter._engine,
                "_call_external_jev",
                side_effect=AssertionError("external AI must not be called"),
            ) as spy_ext:
                # Real evaluate may cache/veto; only assert no external AI path.
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


if __name__ == "__main__":
    unittest.main()
