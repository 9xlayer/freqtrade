"""Unit tests for effective config snapshot, fingerprinting, and override drift warnings."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from strategies.mtf_sniper.core.config_resolve import (
    EffectiveConfig,
    build_effective_config,
    merge_pair_overrides,
    resolve_strategy_switches,
    warn_pair_override_divergences,
)
from strategies.mtf_sniper.core.runtime_mode import RuntimeMode, RuntimeProfile


def _fake_strategy(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "strategy_version": "4.6.0-counter1d",
        "config": {"strategy_params": {"mtf_runtime_mode": "deterministic"}},
        "block_macro_bull_shorts": False,
        "enable_dynamic_sizing": False,
        "jev_fail_policy": "allow",
        "long_loc_tolerance_1h": SimpleNamespace(value=1.02),
        "long_max_1h_rsi_exhaustion": SimpleNamespace(value=72.0),
        "long_lookback_resistance": SimpleNamespace(value=12),
        "long_candle_body_ratio_min": SimpleNamespace(value=0.35),
        "long_max_dist_ema21": SimpleNamespace(value=0.05),
        "long_max_rsi_entry": SimpleNamespace(value=70.0),
        "long_cooldown_candles": SimpleNamespace(value=8),
        "long_overextended_dist_max": SimpleNamespace(value=0.08),
        "long_block_1d_macd": SimpleNamespace(value=True),
        "short_loc_tolerance_1h": SimpleNamespace(value=0.98),
        "short_min_1h_rsi_exhaustion": SimpleNamespace(value=28.0),
        "short_lookback_support": SimpleNamespace(value=12),
        "short_candle_body_ratio_min": SimpleNamespace(value=0.40),
        "short_max_dist_ema21": SimpleNamespace(value=0.055),
        "short_min_rsi_entry": SimpleNamespace(value=30.0),
        "short_cooldown_candles": SimpleNamespace(value=8),
        "custom_pair_params": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestConfigResolve(unittest.TestCase):
    def test_build_effective_config_unwraps_parameters_and_fingerprints(self) -> None:
        strategy = _fake_strategy()
        effective = build_effective_config(strategy)

        self.assertIsInstance(effective, EffectiveConfig)
        self.assertEqual(effective.runtime_mode, RuntimeMode.DETERMINISTIC.value)
        self.assertTrue(effective.enable_jev_decision)
        self.assertFalse(effective.enable_jev_hunter)
        self.assertFalse(effective.allow_external_ai)
        self.assertEqual(effective.decision_jev_mode, "native")
        self.assertEqual(effective.long_params["long_max_dist_ema21"], 0.05)
        self.assertEqual(effective.short_params["short_max_dist_ema21"], 0.055)

        fp1 = effective.fingerprint()
        fp2 = build_effective_config(strategy).fingerprint()
        self.assertEqual(len(fp1), 16)
        self.assertEqual(fp1, fp2)

        log_dict = effective.to_log_dict()
        self.assertEqual(log_dict["fingerprint"], fp1)
        self.assertEqual(log_dict["jev_fail_policy"], "allow")

    def test_fingerprint_changes_when_knob_changes(self) -> None:
        base = build_effective_config(_fake_strategy())
        changed = build_effective_config(_fake_strategy(jev_fail_policy="safe"))
        self.assertNotEqual(base.fingerprint(), changed.fingerprint())

    def test_explicit_profile_overrides_strategy_config(self) -> None:
        profile = RuntimeProfile(
            mode=RuntimeMode.DECISION_EXTERNAL,
            enable_jev_decision=True,
            enable_jev_hunter=False,
            enable_hunter_long=False,
            decision_jev_mode="external",
            allow_external_ai=True,
            source="unit-test",
        )
        effective = build_effective_config(_fake_strategy(), profile=profile)
        self.assertEqual(effective.runtime_mode, "decision_external")
        self.assertTrue(effective.enable_jev_decision)
        self.assertTrue(effective.allow_external_ai)
        self.assertEqual(effective.source, "unit-test")

    def test_warn_pair_override_divergences_detects_long_and_short_masks(self) -> None:
        strategy = _fake_strategy(
            custom_pair_params={
                "BTC/USDT:USDT": {"long_max_dist_ema21": 0.05},
                "ETH/USDT:USDT": {
                    "long_candle_body_ratio_min": 0.50,
                    "short_max_dist_ema21": 0.07,
                },
            }
        )
        effective = build_effective_config(strategy)
        warnings = warn_pair_override_divergences(effective)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("long_candle_body_ratio_min=0.5" in w for w in warnings))
        self.assertTrue(any("short_max_dist_ema21=0.07" in w for w in warnings))

    def test_resolve_strategy_switches_priority_and_bounds(self) -> None:
        switches = resolve_strategy_switches(
            {
                "enable_dynamic_sizing": True,
                "enable_waterfall": False,
                "counter_1d_stake_scale": 0.65,
                "default_leverage": 8,
                "tag_multipliers": {"long_A80_plus": 1.25, "bad": -1},
            },
            environ={
                "ENABLE_DYNAMIC_SIZING": "false",
                "COUNTER_1D_STAKE_SCALE": "0.35",
                "JEV_FAIL_POLICY": "safe",
            },
        )
        self.assertTrue(switches.enable_dynamic_sizing)
        self.assertFalse(switches.enable_waterfall)
        self.assertEqual(switches.counter_1d_stake_scale, 0.65)
        self.assertEqual(switches.default_leverage, 8.0)
        self.assertEqual(switches.jev_fail_policy, "safe")
        self.assertEqual(switches.tag_multipliers, {"long_A80_plus": 1.25})

        env_fallback = resolve_strategy_switches(
            {},
            environ={
                "COUNTER_1D_STAKE_SCALE": "1.5",
                "DEFAULT_LEVERAGE": "200",
                "EXCHANGE_SYNC_INTERVAL_SECS": "20",
            },
        )
        self.assertEqual(env_fallback.counter_1d_stake_scale, 1.0)
        self.assertEqual(env_fallback.default_leverage, 125.0)
        self.assertEqual(env_fallback.exchange_sync_interval_secs, 20.0)

    def test_merge_pair_overrides_does_not_mutate_base(self) -> None:
        base = {"BTC/USDT:USDT": {"stoploss": -0.25}}
        merged = merge_pair_overrides(
            base,
            {
                "pair_overrides": {
                    "BTC/USDT:USDT": {"stoploss": -0.22},
                    "SOL/USDT:USDT": {"stoploss": -0.30},
                }
            },
        )
        self.assertEqual(base["BTC/USDT:USDT"]["stoploss"], -0.25)
        self.assertEqual(merged["BTC/USDT:USDT"]["stoploss"], -0.22)
        self.assertEqual(merged["SOL/USDT:USDT"]["stoploss"], -0.30)

    def test_effective_config_fingerprint_tracks_waterfall_and_sizing(self) -> None:
        base = build_effective_config(
            _fake_strategy(enable_waterfall=True, counter_1d_stake_scale=0.50)
        )
        changed_wf = build_effective_config(
            _fake_strategy(enable_waterfall=False, counter_1d_stake_scale=0.50)
        )
        changed_scale = build_effective_config(
            _fake_strategy(enable_waterfall=True, counter_1d_stake_scale=0.65)
        )
        self.assertNotEqual(base.fingerprint(), changed_wf.fingerprint())
        self.assertNotEqual(base.fingerprint(), changed_scale.fingerprint())


if __name__ == "__main__":
    unittest.main()
