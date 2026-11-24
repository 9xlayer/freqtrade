"""
Comprehensive Unit & Integration Test Suite for Jev AI Decision Architecture
=============================================================================
Tests correctness, fail-safe resilience, data contracts, and risk gating across:
1. CandleTrajectoryExtractor
2. MomentumRadar
3. JevHunterEngine & JevHunterCache
4. JevDecisionEngine & JevDecisionCache
5. StrategyMTFSniper Integration & Risk Gating
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


workspace_dir = Path(__file__).resolve().parents[2]
if str(workspace_dir) not in sys.path:
    sys.path.insert(0, str(workspace_dir))

# Archived short-only strategy (not part of live SensorSuite deploy)
_archive_short = workspace_dir / "strategies" / "archive" / "short_sniper"
if str(_archive_short) not in sys.path:
    sys.path.insert(0, str(_archive_short))

from StrategyMTFShortSniper import StrategyMTFShortSniper

from freqtrade.configuration import Configuration
from strategies.mtf_sniper.intelligence.jev_decision import (
    PROMPT_VERSION_EXTERNAL,
    CandleTrajectoryExtractor,
    JevDecisionEngine,
    JevStatus,
    _parse_unit_interval,
    build_decision_cache_key,
    make_verdict,
)
from strategies.mtf_sniper.intelligence.jev_entry_hunter import (
    HUNTER_ENGINE_VERSION,
    HUNTER_MODEL_VERSION_EXTERNAL,
    HUNTER_MODEL_VERSION_NATIVE,
    JevHunterAction,
    JevHunterCache,
    JevHunterEngine,
    JevHunterStatus,
    JevSetupType,
    MomentumRadar,
    build_canonical_payload,
    compute_payload_hash,
    is_mutable_model_id,
    is_setup_valid_for_side,
)
from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper


class TestCandleTrajectoryExtractor(unittest.TestCase):
    """Unit tests for CandleTrajectoryExtractor input validation and payload contract."""

    def setUp(self):
        # Create standard test dataframe with 40 candles
        dates = pd.date_range("2026-01-01", periods=40, freq="15min")
        self.df = pd.DataFrame({
            "date": dates,
            "open": np.linspace(100, 110, 40),
            "high": np.linspace(101, 111, 40),
            "low": np.linspace(99, 109, 40),
            "close": np.linspace(100.5, 110.5, 40),
            "volume": np.full(40, 1000.0),
            "atr": np.full(40, 1.5),
            "rel_vol": np.full(40, 1.2),
            "ema_50_1h": np.full(40, 105.0),
            "rsi": np.full(40, 55.0),
            "trend_4h": ["BULL"] * 40
        })

    def test_empty_dataframe(self):
        empty_df = pd.DataFrame()
        payload = CandleTrajectoryExtractor.build_payload(empty_df, "BTC/USDT:USDT", "long", lookback=32)
        self.assertIsInstance(payload, dict)
        self.assertEqual(len(payload.get("trajectory_sequence", [])), 0)
        self.assertEqual(payload.get("current_price"), 0.0)

    def test_missing_required_columns(self):
        df_incomplete = pd.DataFrame({"close": [100.0, 101.0], "volume": [10, 20]})
        payload = CandleTrajectoryExtractor.build_payload(df_incomplete, "BTC/USDT:USDT", "short", lookback=32)
        self.assertIsInstance(payload, dict)
        self.assertEqual(len(payload.get("trajectory_sequence", [])), 0)

    def test_variable_candle_lengths(self):
        for n in [5, 15, 20, 32]:
            sub_df = self.df.iloc[:n]
            payload = CandleTrajectoryExtractor.build_payload(sub_df, "BTC/USDT:USDT", "long", lookback=32)
            seq = payload.get("trajectory_sequence", [])
            self.assertEqual(len(seq), n)
            # Verify candle indexing
            self.assertEqual(seq[-1]["index"], 0)
            self.assertEqual(seq[0]["index"], -(n - 1))

    def test_zero_volume_and_zero_atr(self):
        df_zeros = self.df.copy()
        df_zeros["volume"] = 0.0
        df_zeros["atr"] = 0.0
        # Must not raise ZeroDivisionError
        payload = CandleTrajectoryExtractor.build_payload(df_zeros, "ETH/USDT:USDT", "short", lookback=32)
        seq = payload.get("trajectory_sequence", [])
        self.assertEqual(len(seq), 32)
        for c in seq:
            self.assertFalse(np.isnan(c["body_ratio"]))
            self.assertFalse(np.isnan(c["volume_ratio"]))

    def test_nan_and_inf_handling(self):
        df_corrupt = self.df.copy()
        df_corrupt.loc[10, "close"] = np.nan
        df_corrupt.loc[11, "volume"] = np.inf
        payload = CandleTrajectoryExtractor.build_payload(df_corrupt, "SOL/USDT:USDT", "long", lookback=32)
        seq = payload.get("trajectory_sequence", [])
        self.assertEqual(len(seq), 32)
        for c in seq:
            self.assertFalse(np.isnan(c["pct_change"]))
            self.assertFalse(np.isnan(c["body_ratio"]))
            self.assertFalse(np.isinf(c["pct_change"]))

    def test_payload_contract_fields(self):
        payload = CandleTrajectoryExtractor.build_payload(self.df, "BTC/USDT:USDT", "long", lookback=32)
        self.assertIn("current_price", payload)
        self.assertIn("timeframe", payload)
        self.assertIn("timestamp", payload)
        self.assertIn("side", payload)
        self.assertIn("pair", payload)
        self.assertIn("macro_context", payload)
        self.assertIn("payload_hash", payload)
        self.assertEqual(payload["timeframe"], "15m")
        self.assertEqual(payload["side"], "LONG")
        # Check unified field mapping for 1h EMA
        self.assertIn("ema_50_1h_dist_pct", payload["macro_context"])

    def test_decision_payload_hash_sensitivity(self):
        """Verify that payload_hash changes whenever candle values or macro context change."""
        payload1 = CandleTrajectoryExtractor.build_payload(self.df, "BTC/USDT:USDT", "short", lookback=32)
        h1 = payload1["payload_hash"]
        self.assertTrue(len(h1) > 0)

        # Mutate intermediate candle in trajectory (not timestamp, not last close)
        df_mutated_candle = self.df.copy()
        df_mutated_candle.loc[25, "close"] = 999.0
        payload2 = CandleTrajectoryExtractor.build_payload(df_mutated_candle, "BTC/USDT:USDT", "short", lookback=32)
        self.assertNotEqual(h1, payload2["payload_hash"], "Hash must change when trajectory candle values change")

        # Mutate macro indicator in last candle
        df_mutated_macro = self.df.copy()
        df_mutated_macro.loc[39, "ema_50_1h"] = 555.0
        payload3 = CandleTrajectoryExtractor.build_payload(df_mutated_macro, "BTC/USDT:USDT", "short", lookback=32)
        self.assertNotEqual(h1, payload3["payload_hash"], "Hash must change when macro context changes")


class TestMomentumRadar(unittest.TestCase):
    """Unit tests for MomentumRadar candidate detection, cooldown isolation, and math guards."""

    def setUp(self):
        n = 120
        self.df = pd.DataFrame({
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 100.0),
            "atr": np.full(n, 1.0)
        })

    def test_insufficient_history(self):
        short_df = self.df.iloc[:50]
        candidates = MomentumRadar.find_candidates(short_df)
        self.assertEqual(candidates, [])

    def test_short_mega_surge(self):
        df = self.df.copy()
        # Bar 105: Bearish surge: close < open, vol 5x SMA (500 vs 100), range 3x ATR (3.0 vs 1.0)
        df.loc[105, "open"] = 100.0
        df.loc[105, "close"] = 97.0
        df.loc[105, "high"] = 100.0
        df.loc[105, "low"] = 97.0
        df.loc[105, "volume"] = 500.0
        candidates = MomentumRadar.find_candidates(df, cooldown_bars=12, directional_cooldown=True)
        self.assertTrue(any(c[0] == 105 and c[1] == "MEGA_SURGE_SHORT" for c in candidates))

    def test_long_mega_surge(self):
        df = self.df.copy()
        # Bar 105: Bullish surge: close > open, vol 5x SMA, range 3x ATR
        df.loc[105, "open"] = 97.0
        df.loc[105, "close"] = 100.0
        df.loc[105, "high"] = 100.0
        df.loc[105, "low"] = 97.0
        df.loc[105, "volume"] = 500.0
        candidates = MomentumRadar.find_candidates(df, cooldown_bars=12, directional_cooldown=True)
        self.assertTrue(any(c[0] == 105 and c[1] == "MEGA_SURGE_LONG" for c in candidates))

    def test_breakout_24h(self):
        df = self.df.copy()
        # Range of prior 96 bars is [99.0, 101.0]
        # Short breakdown below 99.0 with volume 3.0x
        df.loc[105, "close"] = 98.0
        df.loc[105, "volume"] = 300.0
        candidates = MomentumRadar.find_candidates(df, cooldown_bars=12, directional_cooldown=True)
        self.assertTrue(any(c[0] == 105 and c[1] == "BREAKOUT_24H_SHORT" for c in candidates))

    def test_cooldown_directional_isolation(self):
        df = self.df.copy()
        # Long event at 102
        df.loc[102, "open"] = 97.0
        df.loc[102, "close"] = 100.0
        df.loc[102, "high"] = 100.0
        df.loc[102, "low"] = 97.0
        df.loc[102, "volume"] = 500.0

        # Short event at 104 (within 12 bars of Long event!)
        # Set volume high enough (700) so vol_ratio >= 4.0x despite bar 102 spike in SMA
        df.loc[104, "open"] = 100.0
        df.loc[104, "close"] = 97.0
        df.loc[104, "high"] = 100.0
        df.loc[104, "low"] = 97.0
        df.loc[104, "volume"] = 700.0

        candidates = MomentumRadar.find_candidates(df, cooldown_bars=12, directional_cooldown=True)
        event_indices = [c[0] for c in candidates]
        # Both Long at 102 and Short at 104 must be captured because cooldowns are directionally isolated
        self.assertIn(102, event_indices)
        self.assertIn(104, event_indices)

    def test_zero_atr_and_volume(self):
        df = self.df.copy()
        df["volume"] = 0.0
        df["atr"] = 0.0
        # Must not crash
        candidates = MomentumRadar.find_candidates(df)
        self.assertEqual(candidates, [])


class TestJevHunterCacheAndEngine(unittest.TestCase):
    """Unit tests for JevHunterCache and JevHunterEngine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test_hunter.sqlite")
        self.cache = JevHunterCache(self.db_path)
        self.engine = JevHunterEngine(cache_db=self.db_path, mode="native")

        dates = pd.date_range("2026-01-01", periods=32, freq="15min")
        self.df_32 = pd.DataFrame({
            "date": dates,
            "open": np.linspace(100, 95, 32),
            "high": np.linspace(100.5, 95.5, 32),
            "low": np.linspace(99.5, 94.5, 32),
            "close": np.linspace(99.5, 94.8, 32),
            "volume": np.full(32, 1000.0),
            "rel_vol": np.full(32, 3.5),
            "atr": np.full(32, 1.0)
        })

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cache_put_and_get(self):
        pair = "BTC/USDT:USDT"
        ts = "2026-01-01 12:00:00"
        self.cache.put(
            pair=pair,
            timestamp=ts,
            action=JevHunterAction.ENTER_SHORT.value,
            setup_type=JevSetupType.WATERFALL_COLLAPSE.value,
            confidence=0.88,
            status=JevHunterStatus.CONFIRMED.value,
            raw_response={"test": True}
        )
        cached = self.cache.get(pair, ts)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["action"], "enter_short")
        self.assertEqual(cached["setup_type"], "waterfall_collapse")
        self.assertEqual(cached["confidence"], 0.88)
        self.assertEqual(cached["status"], "CONFIRMED")

    def test_cache_does_not_store_transient_failures(self):
        pair = "ETH/USDT:USDT"
        ts = "2026-01-01 12:00:00"
        # Try to put INVALID_RESPONSE
        self.cache.put(
            pair=pair,
            timestamp=ts,
            action=JevHunterAction.NO_TRADE.value,
            setup_type=JevSetupType.UNKNOWN.value,
            confidence=0.0,
            status=JevHunterStatus.INVALID_RESPONSE.value,
            raw_response={"error": "timeout"}
        )
        cached = self.cache.get(pair, ts)
        self.assertIsNone(cached)

    def test_native_waterfall_collapse_evaluation(self):
        # Set last candle as strong bear breakdown
        df = self.df_32.copy()
        df.loc[31, "open"] = 96.0
        df.loc[31, "close"] = 94.0
        df.loc[31, "high"] = 96.1
        df.loc[31, "low"] = 93.9
        df.loc[31, "volume"] = 4000.0
        df.loc[31, "rel_vol"] = 4.0

        macro_info = {"trend_4h": "BEAR", "ema50_1h_dist_pct": -2.5}
        verdict = self.engine.evaluate_candidate(
            df, "BTC/USDT:USDT", "2026-01-01 12:00:00", "MEGA_SURGE_SHORT", macro_info
        )
        self.assertEqual(verdict["action"], "enter_short")
        self.assertEqual(verdict["setup_type"], "waterfall_collapse")
        self.assertEqual(verdict["status"], "CONFIRMED")
        self.assertGreaterEqual(verdict["confidence"], 0.85)

    def test_insufficient_candle_history(self):
        short_df = self.df_32.iloc[:10]
        verdict = self.engine.evaluate_candidate(
            short_df, "BTC/USDT:USDT", "2026-01-01 12:00:00", "MEGA_SURGE_SHORT"
        )
        self.assertEqual(verdict["action"], "no_trade")
        self.assertEqual(verdict["status"], JevHunterStatus.INVALID_RESPONSE.value)
        self.assertEqual(verdict["confidence"], 0.0)

    def test_setup_valid_for_side_helper(self):
        self.assertTrue(is_setup_valid_for_side(JevSetupType.WATERFALL_COLLAPSE, "short"))
        self.assertFalse(is_setup_valid_for_side(JevSetupType.WATERFALL_COLLAPSE, "long"))

        self.assertTrue(is_setup_valid_for_side(JevSetupType.BREAKOUT_EXPANSION, "long"))
        self.assertFalse(is_setup_valid_for_side(JevSetupType.TREND_PULLBACK, "long"))
        self.assertFalse(is_setup_valid_for_side(JevSetupType.ABSORPTION_TRAP, "long"))
        self.assertFalse(is_setup_valid_for_side(JevSetupType.BREAKOUT_EXPANSION, "short"))

        self.assertFalse(is_setup_valid_for_side(JevSetupType.CONSOLIDATION_NOISE, "short"))
        self.assertFalse(is_setup_valid_for_side(JevSetupType.UNKNOWN, "long"))

    def test_hunter_cache_mode_and_hash_isolation(self):
        """Verify Hunter cache isolates native vs external models and rejects stale hash mismatches."""
        pair = "ETH/USDT:USDT"
        ts = "2026-01-01 15:00:00"

        # 1. Put Native Verdict
        self.cache.put(
            pair, ts,
            "enter_short", "waterfall_collapse", 0.88,
            JevHunterStatus.CONFIRMED.value, {"foo": "bar"},
            payload_hash="valid_hash_1",
            model_version=HUNTER_MODEL_VERSION_NATIVE
        )

        # 2. Query with matching model and matching hash -> Hit
        hit = self.cache.get(pair, ts, model_version=HUNTER_MODEL_VERSION_NATIVE, payload_hash="valid_hash_1")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["action"], "enter_short")

        # 3. Query with mismatched payload_hash -> None (prevents stale reused cache)
        mismatch = self.cache.get(pair, ts, model_version=HUNTER_MODEL_VERSION_NATIVE, payload_hash="changed_hash_2")
        self.assertIsNone(mismatch)

        # 4. Query with external model -> None (mode isolation)
        isolated = self.cache.get(pair, ts, model_version=HUNTER_MODEL_VERSION_EXTERNAL, payload_hash="valid_hash_1")
        self.assertIsNone(isolated)

        # 5. Put External Verdict at same timestamp with different action
        self.cache.put(
            pair, ts,
            "enter_long", "absorption_trap", 0.90,
            JevHunterStatus.CONFIRMED.value, {"external": True},
            payload_hash="ext_hash_3",
            model_version=HUNTER_MODEL_VERSION_EXTERNAL
        )

        # 6. Both co-exist independently without clobbering each other
        native_hit = self.cache.get(pair, ts, model_version=HUNTER_MODEL_VERSION_NATIVE, payload_hash="valid_hash_1")
        ext_hit = self.cache.get(pair, ts, model_version=HUNTER_MODEL_VERSION_EXTERNAL, payload_hash="ext_hash_3")
        self.assertIsNotNone(native_hit)
        self.assertEqual(native_hit["action"], "enter_short")
        self.assertIsNotNone(ext_hit)
        self.assertEqual(ext_hit["action"], "enter_long")

    def test_hunter_cache_concurrency_multi_payload_same_timestamp(self):
        """Verify that multiple candidate events at same timestamp with different payload_hash don't overwrite each other."""
        pair = "BTC/USDT:USDT"
        ts = "2026-11-12 13:00:00"
        hash_event_a = "hash_breakdown_123"
        hash_event_b = "hash_surge_456"

        # Event A: waterfall collapse
        self.cache.put(
            pair, ts,
            "enter_short", "waterfall_collapse", 0.88,
            JevHunterStatus.CONFIRMED.value, {"trigger": "BREAKOUT_24H"},
            payload_hash=hash_event_a,
            model_version=HUNTER_MODEL_VERSION_NATIVE
        )

        # Event B: surge pullback at same timestamp
        self.cache.put(
            pair, ts,
            "enter_short", "waterfall_collapse", 0.85,
            JevHunterStatus.CONFIRMED.value, {"trigger": "MEGA_SURGE"},
            payload_hash=hash_event_b,
            model_version=HUNTER_MODEL_VERSION_NATIVE
        )

        # Query A with hash_event_a
        verdict_a = self.cache.get(pair, ts, model_version=HUNTER_MODEL_VERSION_NATIVE, payload_hash=hash_event_a)
        self.assertIsNotNone(verdict_a)
        self.assertEqual(verdict_a["confidence"], 0.88)
        self.assertEqual(verdict_a["raw_response"]["trigger"], "BREAKOUT_24H")

        # Query B with hash_event_b
        verdict_b = self.cache.get(pair, ts, model_version=HUNTER_MODEL_VERSION_NATIVE, payload_hash=hash_event_b)
        self.assertIsNotNone(verdict_b)
        self.assertEqual(verdict_b["confidence"], 0.85)
        self.assertEqual(verdict_b["raw_response"]["trigger"], "MEGA_SURGE")

    def test_hunter_external_strict_schema_validation(self):
        """Verify strict response schema validation in External Hunter engine."""
        engine_ext = JevHunterEngine(cache_db=self.db_path, mode="external")
        engine_ext.api_url = "http://localhost:8000/predict"
        engine_ext.api_key = "test_key"

        class MockResponse:
            def __init__(self, data_dict):
                self.data_dict = data_dict
            def read(self):
                return json.dumps(self.data_dict).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        # 1. Missing suggested_action or setup_type
        bad_resp_1 = {"answers": {"only_something_else": {}}}
        with patch("urllib.request.urlopen", return_value=MockResponse(bad_resp_1)):
            res = engine_ext.evaluate_candidate(self.df_32, "BTC/USDT:USDT", "2026-01-01 12:00:00", "SURGE")
            self.assertEqual(res["status"], JevHunterStatus.INVALID_RESPONSE.value)
            self.assertEqual(res["confidence"], 0.0)

        # 2. Probability missing for chosen action
        bad_resp_2 = {
            "answers": {
                "suggested_action": {"choice": "enter_short", "probabilities": {"enter_long": 0.2}},
                "setup_type": {"choice": "waterfall_collapse"}
            }
        }
        with patch("urllib.request.urlopen", return_value=MockResponse(bad_resp_2)):
            res = engine_ext.evaluate_candidate(self.df_32, "BTC/USDT:USDT", "2026-01-01 12:00:00", "SURGE")
            self.assertEqual(res["status"], JevHunterStatus.INVALID_RESPONSE.value)

        # 3. Probability out of bounds (< 0.0 or > 1.0)
        bad_resp_3 = {
            "answers": {
                "suggested_action": {"choice": "enter_short", "probabilities": {"enter_short": 1.5}},
                "setup_type": {"choice": "waterfall_collapse"}
            }
        }
        with patch("urllib.request.urlopen", return_value=MockResponse(bad_resp_3)):
            res = engine_ext.evaluate_candidate(self.df_32, "BTC/USDT:USDT", "2026-01-01 12:00:00", "SURGE")
            self.assertEqual(res["status"], JevHunterStatus.INVALID_RESPONSE.value)

        # 4. Valid response -> CONFIRMED
        good_resp = {
            "answers": {
                "suggested_action": {"choice": "enter_short", "probabilities": {"enter_short": 0.89}},
                "setup_type": {"choice": "waterfall_collapse"}
            }
        }
        with patch("urllib.request.urlopen", return_value=MockResponse(good_resp)):
            res = engine_ext.evaluate_candidate(self.df_32, "BTC/USDT:USDT", "2026-01-01 12:00:00", "SURGE")
            self.assertEqual(res["status"], JevHunterStatus.CONFIRMED.value)
            self.assertEqual(res["confidence"], 0.89)
            self.assertEqual(res["action"], "enter_short")


class TestJevDecisionEngine(unittest.TestCase):
    """Unit tests for JevDecisionEngine, fail-safe resilience, and contract schemas."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test_decision.sqlite")
        self.engine = JevDecisionEngine(cache_db=self.db_path, mode="native")

        dates = pd.date_range("2026-01-01", periods=32, freq="15min")
        self.df_32 = pd.DataFrame({
            "date": dates,
            "open": np.linspace(100, 105, 32),
            "high": np.linspace(101, 106, 32),
            "low": np.linspace(99.5, 104.5, 32),
            "close": np.linspace(100.8, 105.8, 32),
            "volume": np.full(32, 1000.0),
            "rel_vol": np.full(32, 1.5),
            "atr": np.full(32, 1.0),
            "ema_50_1h": np.full(32, 102.0),
            "rsi": np.full(32, 60.0),
            "trend_4h": ["BULL"] * 32
        })

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_universal_verdict_schema(self):
        payload = CandleTrajectoryExtractor.build_payload(self.df_32, "BTC/USDT:USDT", "long")
        verdict = self.engine.evaluate(payload)

        # Must strictly have all required Universal Decision Contract fields
        expected_keys = [
            "decision", "side", "confidence", "market_regime", "reasons",
            "source", "model_version", "engine_version", "status", "raw_response"
        ]
        for k in expected_keys:
            self.assertIn(k, verdict)

        self.assertIn(verdict["decision"], ["CONFIRM", "VETO"])
        self.assertIn(verdict["status"], ["SUCCESS", "UNAVAILABLE", "INVALID"])
        self.assertIsInstance(verdict["reasons"], list)
        self.assertIsInstance(verdict["confidence"], float)

    def test_fail_safe_under_5_candles(self):
        short_df = self.df_32.iloc[:4]
        payload = CandleTrajectoryExtractor.build_payload(short_df, "BTC/USDT:USDT", "long")
        verdict = self.engine.evaluate(payload)

        # Under 5 candles must return VETO with INSUFFICIENT_DATA and INVALID status
        self.assertEqual(verdict["decision"], "VETO")
        self.assertEqual(verdict["status"], "INVALID")
        self.assertEqual(verdict["market_regime"], "INSUFFICIENT_DATA")
        self.assertEqual(verdict["confidence"], 0.0)

    def test_make_verdict_cannot_confirm_without_success(self):
        # Even if decision="CONFIRM" is passed, if status != SUCCESS it MUST become VETO
        v = make_verdict(decision="CONFIRM", status="INVALID", confidence=0.99)
        self.assertEqual(v["decision"], "VETO")
        self.assertEqual(v["status"], "INVALID")

        v2 = make_verdict(decision="CONFIRM", status="UNAVAILABLE", confidence=0.99)
        self.assertEqual(v2["decision"], "VETO")
        self.assertEqual(v2["status"], "UNAVAILABLE")

    def test_adverse_wick_veto(self):
        df = self.df_32.copy()
        # Last candle for LONG with huge upper wick (adverse upper wick = 0.60)
        df.loc[31, "open"] = 100.0
        df.loc[31, "close"] = 101.0
        df.loc[31, "high"] = 105.0  # Upper wick = 4.0 / 5.0 = 0.80
        df.loc[31, "low"] = 100.0

        payload = CandleTrajectoryExtractor.build_payload(df, "BTC/USDT:USDT", "long")
        verdict = self.engine.evaluate(payload)
        self.assertEqual(verdict["decision"], "VETO")
        self.assertTrue(any("wick" in r.lower() for r in verdict["reasons"]))

    def test_decision_cache_version_isolation(self):
        payload = CandleTrajectoryExtractor.build_payload(self.df_32, "ETH/USDT:USDT", "long")
        # Evaluate to populate cache
        v1 = self.engine.evaluate(payload)
        self.assertEqual(v1["status"], "SUCCESS")

        # Now query with a different engine version
        engine_v9 = JevDecisionEngine(cache_db=self.db_path, mode="native")
        engine_v9.engine_version = "9.9.9"
        # Must not load cache from 2.6.0
        v2 = engine_v9.evaluate(payload)
        self.assertNotIn("Loaded from cache", " ".join(v2.get("reasons", [])))

    def test_external_api_failure_handling(self):
        # Create external engine pointing to non-existent endpoint
        ext_engine = JevDecisionEngine(
            cache_db=self.db_path,
            mode="external",
            api_url="http://127.0.0.1:59999/nonexistent",
            api_key="mock_key"
        )
        payload = CandleTrajectoryExtractor.build_payload(self.df_32, "SOL/USDT:USDT", "long")
        verdict = ext_engine.evaluate(payload)
        # External failure must fail-closed to VETO with UNAVAILABLE status
        self.assertEqual(verdict["decision"], "VETO")
        self.assertEqual(verdict["status"], "UNAVAILABLE")

    def test_external_confirm_threshold_and_choppy_veto(self):
        """External gate: noul must clear threshold; choppy_ranging is hard-vetoed."""
        # Use in-memory DB so successive mocked API answers are not sticky across connections.
        ext_engine = JevDecisionEngine(
            cache_db=":memory:",
            mode="external",
            api_url="http://localhost:8000/predict",
            api_key="mock_key",
        )
        payload = CandleTrajectoryExtractor.build_payload(self.df_32, "BTC/USDT:USDT", "long")

        class MockResponse:
            def __init__(self, data_dict):
                self.data_dict = data_dict

            def read(self):
                return json.dumps(self.data_dict).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        # Below threshold -> VETO
        low_noul = {
            "model": "jev-1.14.0",
            "answers": {
                "confirm_trade": {"type": "noul", "noul": 0.40},
                "market_regime": {"type": "choice", "choice": "trend_expansion"},
            },
        }
        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.60"}):
            with patch("urllib.request.urlopen", return_value=MockResponse(low_noul)):
                v_low = ext_engine.evaluate(payload)
        self.assertEqual(v_low["decision"], "VETO")
        self.assertEqual(v_low["status"], "SUCCESS")

        # Above threshold + trend_expansion -> CONFIRM
        high_noul = {
            "model": "jev-1.14.0",
            "answers": {
                "confirm_trade": {"type": "noul", "noul": 0.72},
                "market_regime": {"type": "choice", "choice": "trend_expansion"},
            },
        }
        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.60"}):
            with patch("urllib.request.urlopen", return_value=MockResponse(high_noul)):
                v_ok = ext_engine.evaluate(payload)
        self.assertEqual(v_ok["decision"], "CONFIRM")

        # Choppy regime hard veto even when noul is high
        choppy = {
            "model": "jev-1.14.0",
            "answers": {
                "confirm_trade": {"type": "noul", "noul": 0.85},
                "market_regime": {"type": "choice", "choice": "choppy_ranging"},
            },
        }
        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.60"}):
            with patch("urllib.request.urlopen", return_value=MockResponse(choppy)):
                v_chop = ext_engine.evaluate(payload)
        self.assertEqual(v_chop["decision"], "VETO")
        self.assertTrue(any("choppy" in r.lower() for r in v_chop["reasons"]))

    def test_decision_cache_identity_isolates_model_prompt_threshold(self):
        """Same payload with different model/prompt/threshold must miss; identical identity must hit."""
        payload = CandleTrajectoryExtractor.build_payload(self.df_32, "BTC/USDT:USDT", "long")
        p_hash = payload["payload_hash"]

        class MockResponse:
            def __init__(self, data_dict):
                self.data_dict = data_dict

            def read(self):
                return json.dumps(self.data_dict).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        api_ok = {
            "model": "jev-1.14.0",
            "answers": {
                "confirm_trade": {"type": "noul", "noul": 0.75},
                "market_regime": {"type": "choice", "choice": "trend_expansion"},
            },
        }

        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.60", "JEV_DECISION_MODEL_VERSION": "jev-1.14.0"}):
            eng = JevDecisionEngine(
                cache_db=self.db_path,
                mode="external",
                api_url="http://localhost/mock",
                api_key="k",
            )
            eng.external_model_version = "jev-1.14.0"
            eng.prompt_version = PROMPT_VERSION_EXTERNAL
            with patch("urllib.request.urlopen", return_value=MockResponse(api_ok)) as mock_api:
                v1 = eng.evaluate(payload)
            self.assertEqual(v1["decision"], "CONFIRM")
            self.assertEqual(v1["status"], "SUCCESS")
            self.assertEqual(mock_api.call_count, 1)

            # Same identity → cache hit (no second API call)
            with patch("urllib.request.urlopen", return_value=MockResponse(api_ok)) as mock_api2:
                v_hit = eng.evaluate(payload)
            self.assertEqual(mock_api2.call_count, 0)
            self.assertTrue(any("cache" in r.lower() for r in v_hit.get("reasons", [])))

        # Model change → miss
        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.60"}):
            eng2 = JevDecisionEngine(cache_db=self.db_path, mode="external", api_url="http://localhost/mock", api_key="k")
            eng2.external_model_version = "jev-9.9.9"
            eng2.prompt_version = PROMPT_VERSION_EXTERNAL
            with patch("urllib.request.urlopen", return_value=MockResponse(api_ok)) as mock_api3:
                v_model = eng2.evaluate(payload)
            self.assertEqual(mock_api3.call_count, 1)
            self.assertNotIn("Loaded from cache", " ".join(v_model.get("reasons", [])))

        # Threshold change → miss
        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.80"}):
            eng3 = JevDecisionEngine(cache_db=self.db_path, mode="external", api_url="http://localhost/mock", api_key="k")
            eng3.external_model_version = "jev-1.14.0"
            eng3.prompt_version = PROMPT_VERSION_EXTERNAL
            with patch("urllib.request.urlopen", return_value=MockResponse(api_ok)) as mock_api4:
                v_thr = eng3.evaluate(payload)
            self.assertEqual(mock_api4.call_count, 1)
            self.assertNotIn("Loaded from cache", " ".join(v_thr.get("reasons", [])))

        # Prompt/policy version change → miss
        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.60"}):
            eng4 = JevDecisionEngine(cache_db=self.db_path, mode="external", api_url="http://localhost/mock", api_key="k")
            eng4.external_model_version = "jev-1.14.0"
            eng4.prompt_version = "decision-external-prompt-altered"
            with patch("urllib.request.urlopen", return_value=MockResponse(api_ok)) as mock_api5:
                v_prompt = eng4.evaluate(payload)
            self.assertEqual(mock_api5.call_count, 1)
            self.assertNotIn("Loaded from cache", " ".join(v_prompt.get("reasons", [])))

        # Identity helper itself differs when any component changes
        k1 = build_decision_cache_key(
            p_hash, model_version="m1", engine_version="e", prompt_version="p",
            confirm_threshold="0.6000", fail_policy="reject", mode="external",
        )
        k2 = build_decision_cache_key(
            p_hash, model_version="m2", engine_version="e", prompt_version="p",
            confirm_threshold="0.6000", fail_policy="reject", mode="external",
        )
        self.assertNotEqual(k1, k2)

    def test_external_invalid_confidence_and_threshold(self):
        """NaN/Inf/OOB confidence → INVALID; bad threshold → INVALID; malformed answers → INVALID."""
        payload = CandleTrajectoryExtractor.build_payload(self.df_32, "BTC/USDT:USDT", "long")

        class MockResponse:
            def __init__(self, data_dict):
                self.data_dict = data_dict

            def read(self):
                return json.dumps(self.data_dict).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        eng = JevDecisionEngine(
            cache_db=self.db_path, mode="external", api_url="http://localhost/mock", api_key="k"
        )
        eng.external_model_version = "jev-1.14.0"

        for bad in (float("nan"), float("inf"), -1.0, 1.5, "not-a-float", None):
            body = {
                "model": "jev-1.14.0",
                "answers": {
                    "confirm_trade": {"type": "noul", "noul": bad},
                    "market_regime": {"type": "choice", "choice": "trend_expansion"},
                },
            }
            with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.60"}):
                with patch("urllib.request.urlopen", return_value=MockResponse(body)):
                    v = eng.evaluate(payload)
            self.assertEqual(v["status"], "INVALID", msg=f"bad conf={bad!r}")
            self.assertEqual(v["decision"], "VETO")
            self.assertNotIn("Loaded from cache", " ".join(v.get("reasons", [])))

        # Invalid threshold env
        ok_body = {
            "model": "jev-1.14.0",
            "answers": {
                "confirm_trade": {"type": "noul", "noul": 0.9},
                "market_regime": {"type": "choice", "choice": "trend_expansion"},
            },
        }
        for bad_thr in ("1.5", "-0.1", "NaN", "inf"):
            with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": bad_thr}):
                with patch("urllib.request.urlopen", return_value=MockResponse(ok_body)):
                    v = eng.evaluate(payload)
            self.assertEqual(v["status"], "INVALID", msg=f"bad thr={bad_thr}")
            self.assertEqual(v["decision"], "VETO")

        # Missing answers / wrong types
        for bad_resp in (
            {"model": "jev-1.14.0"},
            {"model": "jev-1.14.0", "answers": []},
            {"model": "jev-1.14.0", "answers": {"confirm_trade": 0.9}},
            {"model": "jev-1.14.0", "answers": {"confirm_trade": {"noul": 0.9}}},
        ):
            with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.60"}):
                with patch("urllib.request.urlopen", return_value=MockResponse(bad_resp)):
                    v = eng.evaluate(payload)
            self.assertEqual(v["status"], "INVALID")
            self.assertEqual(v["decision"], "VETO")

        self.assertIsNone(_parse_unit_interval(float("nan")))
        self.assertIsNone(_parse_unit_interval(1.5))
        self.assertEqual(_parse_unit_interval(0.6), 0.6)

    def test_hybrid_score_calculation(self):
        """Hybrid score tests: AI noul shifts score as a bounded term, not a binary gatekeeper."""
        payload = CandleTrajectoryExtractor.build_payload(self.df_32, "BTC/USDT:USDT", "long")

        class MockResponse:
            def __init__(self, data_dict):
                self.data_dict = data_dict

            def read(self):
                return json.dumps(self.data_dict).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        ext_engine = JevDecisionEngine(
            cache_db=":memory:",
            mode="external",
            api_url="http://localhost:8000/predict",
            api_key="mock_key",
        )

        # Baseline native score of df_32 is 0.60
        # 1. High AI conviction (noul 0.85 -> delta +0.07 with w_ai=0.10) lifts 0.60 to 0.67
        high_ai = {
            "model": "jev-1.14.0",
            "answers": {
                "confirm_trade": {"type": "noul", "noul": 0.85},
                "market_regime": {"type": "choice", "choice": "trend_expansion"},
            },
        }
        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.65", "JEV_DECISION_WEIGHT_AI": "0.10"}):
            with patch("urllib.request.urlopen", return_value=MockResponse(high_ai)):
                v = ext_engine.evaluate(payload)
        self.assertEqual(v["decision"], "CONFIRM")
        self.assertEqual(v["confidence"], 0.67)
        self.assertTrue(any("Hybrid score: 0.67" in r for r in v["reasons"]))

        # 2. Low AI conviction (noul 0.15 -> delta -0.07 with w_ai=0.10) demotes 0.60 to 0.53
        low_ai = {
            "model": "jev-1.14.0",
            "answers": {
                "confirm_trade": {"type": "noul", "noul": 0.15},
                "market_regime": {"type": "choice", "choice": "trend_expansion"},
            },
        }
        with patch.dict(os.environ, {"JEV_CONFIRM_THRESHOLD": "0.55", "JEV_DECISION_WEIGHT_AI": "0.10"}):
            with patch("urllib.request.urlopen", return_value=MockResponse(low_ai)):
                v = ext_engine.evaluate(payload)
        self.assertEqual(v["decision"], "VETO")
        self.assertEqual(v["confidence"], 0.53)

        # 3. Choppy regime penalty applies
        choppy_ai = {
            "model": "jev-1.14.0",
            "answers": {
                "confirm_trade": {"type": "noul", "noul": 0.85},
                "market_regime": {"type": "choice", "choice": "choppy_ranging"},
            },
        }
        with patch.dict(os.environ, {
            "JEV_CONFIRM_THRESHOLD": "0.60",
            "JEV_DECISION_WEIGHT_AI": "0.10",
            "JEV_DECISION_CHOPPY_PENALTY": "0.10",
        }):
            with patch("urllib.request.urlopen", return_value=MockResponse(choppy_ai)):
                v = ext_engine.evaluate(payload)
        # 0.60 + 0.07 - 0.10 = 0.57 < 0.60 -> VETO
        self.assertEqual(v["decision"], "VETO")
        self.assertEqual(v["confidence"], 0.57)
        self.assertTrue(any("choppy" in r.lower() for r in v["reasons"]))

        # 4. Fail policy fallback_native
        fallback_engine = JevDecisionEngine(
            cache_db=":memory:",
            mode="external",
            api_url="http://127.0.0.1:59999/nonexistent",
            api_key="mock_key",
            fail_policy="fallback_native",
        )
        v_fb = fallback_engine.evaluate(payload)
        self.assertEqual(v_fb["source"], "native_fallback")
        self.assertEqual(v_fb["confidence"], 0.60)

    def test_high_runway_confluence_and_trap_penalty(self):
        """Verify native heuristic correctly rewards high-runway momentum and penalizes counter-trend traps."""
        native_engine = JevDecisionEngine(cache_db=":memory:", mode="native")

        # Create clean bearish dataframe for SHORT candidate
        df_short = self.df_32.copy()
        df_short["open"] = np.linspace(105, 100, 32)
        df_short["close"] = np.linspace(104.8, 99.8, 32)
        df_short["high"] = np.linspace(106, 101, 32)
        df_short["low"] = np.linspace(104.5, 99.5, 32)
        df_short.loc[df_short.index[-1], "open"] = 101.0
        df_short.loc[df_short.index[-1], "high"] = 101.2
        df_short.loc[df_short.index[-1], "low"] = 99.8
        df_short.loc[df_short.index[-1], "close"] = 100.0

        # 1. High-runway SHORT candidate: floor_dist >= 15%, ema50_dist < 0, rsi_1h < 50
        payload_short_runway = CandleTrajectoryExtractor.build_payload(df_short, "ETH/USDT:USDT", "short")
        payload_short_runway["macro_context"] = {
            "floor_20d_dist_pct": 20.0,
            "ema_50_1h_dist_pct": -0.80,
            "rsi_1h": 46.0,
            "trend_4h": "NEUTRAL",
            "short_score": 65.0,
            "atr_percentile": 50.0,
        }
        v_runway = native_engine.evaluate(payload_short_runway)
        self.assertEqual(v_runway["confidence"], 0.70)
        self.assertEqual(v_runway["decision"], "CONFIRM")
        self.assertTrue(any("High-runway short momentum" in r for r in v_runway["reasons"]))

        # 2. Counter-trend trap SHORT candidate: floor_dist < 8% or above 1H EMA50 with bullish RSI
        payload_short_trap = CandleTrajectoryExtractor.build_payload(df_short, "ETH/USDT:USDT", "short")
        payload_short_trap["macro_context"] = {
            "floor_20d_dist_pct": 7.0,
            "ema_50_1h_dist_pct": 0.50,
            "rsi_1h": 54.0,
            "trend_4h": "NEUTRAL",
            "short_score": 65.0,
            "atr_percentile": 50.0,
        }
        v_trap = native_engine.evaluate(payload_short_trap)
        self.assertEqual(v_trap["confidence"], 0.50)
        self.assertEqual(v_trap["decision"], "VETO")
        self.assertTrue(any("Counter-trend trap" in r for r in v_trap["reasons"]))

    def test_waterfall_vacuum_breakdown_decision_gate(self):
        """Verify unified waterfall vacuum breakdown logic in JEV Decision Engine."""
        native_engine = JevDecisionEngine(cache_db=":memory:", mode="native")

        df_short = self.df_32.copy()
        df_short["open"] = np.linspace(105, 100, 32)
        df_short["close"] = np.linspace(104.8, 99.8, 32)
        df_short["high"] = np.linspace(106, 101, 32)
        df_short["low"] = np.linspace(104.5, 99.5, 32)
        df_short.loc[df_short.index[-1], "open"] = 101.0
        df_short.loc[df_short.index[-1], "high"] = 101.2
        df_short.loc[df_short.index[-1], "low"] = 99.8
        df_short.loc[df_short.index[-1], "close"] = 100.0

        # Scenario 1: Clean floor breakdown (floor_dist <= 0.0) with high ATR (95%)
        # Should: waive ATR penalty, award +0.10 floor bonus, avoid trap penalty -> CONFIRM
        payload_clean = CandleTrajectoryExtractor.build_payload(
            df_short, "BTC/USDT:USDT", "short", setup="waterfall_drop"
        )
        payload_clean["macro_context"] = {
            "floor_20d_dist_pct": -0.5,
            "ema_50_1h_dist_pct": -1.0,
            "rsi_1h": 40.0,
            "trend_4h": "BEAR",
            "short_score": 65.0,
            "atr_percentile": 95.0,
            "rel_vol": 1.2,
        }
        v_clean = native_engine.evaluate(payload_clean)
        self.assertEqual(v_clean["decision"], "CONFIRM")
        self.assertTrue(any("Waterfall clean floor breakdown" in r for r in v_clean["reasons"]))
        self.assertTrue(
            any("High ATR volatility aligned with waterfall vacuum" in r for r in v_clean["reasons"])
        )

        # Scenario 2: Near-floor buffer (0.3% > 0) WITHOUT volume surge (rel_vol 1.5 < 3.0)
        # Should: NOT be vacuum -> ATR penalty (-0.20), floor trap penalty (-0.10), NO bonus -> VETO
        payload_no_surge = CandleTrajectoryExtractor.build_payload(
            df_short, "BTC/USDT:USDT", "short", setup="waterfall_drop"
        )
        payload_no_surge["macro_context"] = {
            "floor_20d_dist_pct": 0.3,
            "ema_50_1h_dist_pct": -1.0,
            "rsi_1h": 40.0,
            "trend_4h": "BEAR",
            "short_score": 65.0,
            "atr_percentile": 95.0,
            "rel_vol": 1.5,
        }
        v_no_surge = native_engine.evaluate(payload_no_surge)
        self.assertEqual(v_no_surge["decision"], "VETO")
        self.assertTrue(any("Extreme ATR volatility risk" in r for r in v_no_surge["reasons"]))
        self.assertTrue(
            any("Counter-trend trap or exhausted floor risk" in r for r in v_no_surge["reasons"])
        )

        # Scenario 3: Near-floor buffer (0.3% > 0) WITH strong volume surge (rel_vol 3.5 >= 3.0)
        # Should: IS vacuum -> ATR penalty waived, trap penalty waived, but NO floor bonus (+0.10 only for <= 0.0)
        payload_surge = CandleTrajectoryExtractor.build_payload(
            df_short, "BTC/USDT:USDT", "short", setup="waterfall_drop"
        )
        payload_surge["macro_context"] = {
            "floor_20d_dist_pct": 0.3,
            "ema_50_1h_dist_pct": -1.0,
            "rsi_1h": 40.0,
            "trend_4h": "BEAR",
            "short_score": 65.0,
            "atr_percentile": 95.0,
            "rel_vol": 3.5,
        }
        v_surge = native_engine.evaluate(payload_surge)
        self.assertTrue(
            any("High ATR volatility aligned with waterfall vacuum" in r for r in v_surge["reasons"])
        )
        self.assertFalse(any("Counter-trend trap" in r for r in v_surge["reasons"]))
        self.assertFalse(any("Waterfall clean floor breakdown" in r for r in v_surge["reasons"]))


class TestHunterExternalModelIdentity(unittest.TestCase):
    """Hunter cache must use immutable model identity; mutable alias + no_cache must not persist."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "hunter_identity.sqlite")
        idx = pd.date_range("2026-01-01", periods=32, freq="15min", tz="UTC")
        self.df = pd.DataFrame(
            {
                "open": np.linspace(100, 90, 32),
                "high": np.linspace(101, 91, 32),
                "low": np.linspace(99, 89, 32),
                "close": np.linspace(100, 90, 32),
                "volume": np.full(32, 1000.0),
                "rel_vol": np.full(32, 2.5),
            },
            index=idx,
        )
        self.df.loc[self.df.index[-1], "close"] = 88.0
        self.df.loc[self.df.index[-1], "open"] = 92.0
        self.df.loc[self.df.index[-1], "high"] = 92.5
        self.df.loc[self.df.index[-1], "low"] = 87.5
        self.df.loc[self.df.index[-1], "rel_vol"] = 4.0

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mutable_alias_helpers(self):
        self.assertTrue(is_mutable_model_id("jev-latest"))
        self.assertTrue(is_mutable_model_id("foo-latest"))
        self.assertFalse(is_mutable_model_id("jev-1.14.0"))

    def test_hunter_cache_hit_with_actual_model_no_second_api(self):
        engine = JevHunterEngine(
            cache_db=self.db_path,
            mode="external",
            api_url="http://localhost/mock",
            api_key="k",
            model_version="jev-latest",
            missing_model_policy="no_cache",
        )
        pair, ts = "BTC/USDT:USDT", "2026-01-01T08:00:00+00:00"
        api_body = {
            "model": "jev-1.14.0",
            "answers": {
                "suggested_action": {
                    "type": "choice",
                    "choice": "enter_short",
                    "probabilities": {
                        "enter_short": 0.82,
                        "enter_long": 0.05,
                        "no_trade": 0.13,
                    },
                },
                "setup_type": {"type": "choice", "choice": "waterfall_collapse"},
            },
        }

        class MockResponse:
            def __init__(self, data_dict):
                self.data_dict = data_dict

            def read(self):
                return json.dumps(self.data_dict).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("urllib.request.urlopen", return_value=MockResponse(api_body)) as mock_api:
            v1 = engine.evaluate_candidate(
                self.df, pair, ts, "SHORT_BREAKOUT", {"trend_4h": "BEAR", "ema50_1h_dist_pct": -2.0}
            )
        self.assertEqual(v1["model_version"], "jev-1.14.0")
        self.assertIn(v1["status"], ("CONFIRMED", "REJECTED"))
        self.assertEqual(mock_api.call_count, 1)

        with patch("urllib.request.urlopen", return_value=MockResponse(api_body)) as mock_api2:
            v2 = engine.evaluate_candidate(
                self.df, pair, ts, "SHORT_BREAKOUT", {"trend_4h": "BEAR", "ema50_1h_dist_pct": -2.0}
            )
        self.assertEqual(mock_api2.call_count, 0, "Second call must hit cache under actual model")
        self.assertEqual(v2["model_version"], "jev-1.14.0")

        # Different actual model must not reuse prior verdict
        engine_b = JevHunterEngine(
            cache_db=self.db_path,
            mode="external",
            api_url="http://localhost/mock",
            api_key="k",
            model_version="jev-latest",
            missing_model_policy="no_cache",
        )
        api_body_b = dict(api_body)
        api_body_b["model"] = "jev-2.0.0"
        with patch("urllib.request.urlopen", return_value=MockResponse(api_body_b)) as mock_api3:
            v3 = engine_b.evaluate_candidate(
                self.df, pair, ts, "SHORT_BREAKOUT", {"trend_4h": "BEAR", "ema50_1h_dist_pct": -2.0}
            )
        self.assertEqual(mock_api3.call_count, 1)
        self.assertEqual(v3["model_version"], "jev-2.0.0")

    def test_hunter_no_cache_when_backend_omits_model_and_policy_no_cache(self):
        engine = JevHunterEngine(
            cache_db=self.db_path,
            mode="external",
            api_url="http://localhost/mock",
            api_key="k",
            model_version="jev-latest",
            missing_model_policy="no_cache",
        )
        pair, ts = "ETH/USDT:USDT", "2026-01-01T08:00:00+00:00"
        api_body = {
            "answers": {
                "suggested_action": {
                    "type": "choice",
                    "choice": "no_trade",
                    "probabilities": {
                        "enter_short": 0.1,
                        "enter_long": 0.1,
                        "no_trade": 0.8,
                    },
                },
                "setup_type": {"type": "choice", "choice": "consolidation_noise"},
            },
        }

        class MockResponse:
            def __init__(self, data_dict):
                self.data_dict = data_dict

            def read(self):
                return json.dumps(self.data_dict).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("urllib.request.urlopen", return_value=MockResponse(api_body)):
            v1 = engine.evaluate_candidate(
                self.df, pair, ts, "SHORT_BREAKOUT", {"trend_4h": "BEAR"}
            )
        self.assertEqual(v1["status"], "REJECTED")

        # Nothing stored under alias or empty model
        cached_alias = engine.cache.get(
            pair, ts, engine_version=HUNTER_ENGINE_VERSION, model_version="jev-latest"
        )
        self.assertIsNone(cached_alias)

        with patch("urllib.request.urlopen", return_value=MockResponse(api_body)) as mock_api:
            engine.evaluate_candidate(self.df, pair, ts, "SHORT_BREAKOUT", {"trend_4h": "BEAR"})
        self.assertEqual(mock_api.call_count, 1, "Must re-call API when nothing was cached")


class TestStrategyIntegrationAndRiskGating(unittest.TestCase):
    """Integration tests verifying Risk Gate precedence, conflict resolution, and custom stoploss math."""

    def test_simultaneous_long_short_conflict_resolution(self):
        """Simulate conflict where both final_short_mask and final_long_mask are True on the same bar."""
        final_short_mask = pd.Series([False, True, False, True])
        final_long_mask = pd.Series([False, True, False, False])

        both_mask = final_short_mask & final_long_mask
        self.assertTrue(both_mask.iloc[1])

        # Conflict resolution logic: cancel both on bar 1
        resolved_short = final_short_mask & (~both_mask)
        resolved_long = final_long_mask & (~both_mask)

        self.assertFalse(resolved_short.iloc[1])
        self.assertFalse(resolved_long.iloc[1])
        # Bar 3 short remains untouched
        self.assertTrue(resolved_short.iloc[3])

    def test_custom_stoploss_math_semantics(self):
        """
        Verify Freqtrade stoploss_from_open semantics at 10x leverage:
        Targeting -5% ROE (-0.05) at 10x leverage.
        stoploss_from_open returns relative stop distance ~0.04004 (4% ROE distance = 0.4% price move).
        """
        from freqtrade.strategy import stoploss_from_open

        # Case 1: Long position at 10x leverage, current_profit = -0.01 (-1% ROE)
        sl_rel = stoploss_from_open(-0.05, -0.01, is_short=False, leverage=10.0)
        self.assertGreater(sl_rel, 0.0)
        self.assertAlmostEqual(sl_rel, 0.04004, places=4)

        # Case 2: Short position at 10x leverage, current_profit = -0.01
        sl_rel_short = stoploss_from_open(-0.05, -0.01, is_short=True, leverage=10.0)
        self.assertGreater(sl_rel_short, 0.0)
        self.assertAlmostEqual(sl_rel_short, 0.03996, places=4)

    def test_hunter_setup_side_enforcement(self):
        """Verify that Strategy strictly matches Hunter setup with side."""
        # SHORT only accepts waterfall_collapse
        self.assertTrue(is_setup_valid_for_side("waterfall_collapse", "short"))
        self.assertFalse(is_setup_valid_for_side("trend_pullback", "short"))
        self.assertFalse(is_setup_valid_for_side("absorption_trap", "short"))
        self.assertFalse(is_setup_valid_for_side("breakout_expansion", "short"))

        # LONG only accepts breakout_expansion (trend-follow); mean-reversion labels rejected
        self.assertTrue(is_setup_valid_for_side("breakout_expansion", "long"))
        self.assertFalse(is_setup_valid_for_side("trend_pullback", "long"))
        self.assertFalse(is_setup_valid_for_side("absorption_trap", "long"))
        self.assertFalse(is_setup_valid_for_side("waterfall_collapse", "long"))
        self.assertFalse(is_setup_valid_for_side("consolidation_noise", "long"))

    def test_jev_fail_policy_allow_and_reject(self):
        """Verify JEV fail policy: 'allow' bypasses AI gate on UNAVAILABLE, 'reject' blocks."""
        cfg = Configuration.from_files(["user_data/config.json"])
        strat = StrategyMTFSniper(config=cfg)
        strat.enable_jev_decision = True
        strat.dp = MagicMock()

        dates = pd.date_range("2026-01-01", periods=32, freq="15min")
        df = pd.DataFrame({
            "date": dates,
            "open": [100.0] * 32, "high": [102.0] * 32, "low": [99.0] * 32,
            "close": [101.0] * 32, "volume": [1000.0] * 32
        })
        strat.dp.get_analyzed_dataframe.return_value = (df, "15m")
        now = dates[-1]

        with patch("freqtrade.persistence.Trade.get_trades_proxy", return_value=[]):
            with patch(
                "strategies.mtf_sniper.intelligence.jev_decision.JevDecisionEngine.evaluate"
            ) as mock_eval:
                mock_eval.return_value = {
                    "decision": "VETO",
                    "status": JevStatus.UNAVAILABLE.value,
                    "confidence": 0.0,
                    "market_regime": "UNKNOWN",
                    "reasons": ["API server down"]
                }
                # Policy 'allow' MUST return True
                strat.jev_fail_policy = "allow"
                self.assertTrue(strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "long"))

                # Policy 'reject' MUST return False
                strat.jev_fail_policy = "reject"
                self.assertFalse(strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "long"))

    def test_circuit_breaker_severe_loss_threshold_and_reset(self):
        """Verify Circuit Breaker requires losses <= -10% ROE and resets immediately on > -10%."""
        cfg = Configuration.from_files(["user_data/config.json"])
        strat = StrategyMTFSniper(config=cfg)

        class MockTrade:
            def __init__(self, close_profit, close_date, strategy="StrategyMTFSniper"):
                self.close_profit = close_profit
                self.close_date = close_date
                self.strategy = strategy
                self.is_short = False
                self.pair = "BTC/USDT:USDT"
                self.calc_profit_ratio = lambda p: 0.0

        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

        # Case 1: 3 severe losses <= -0.10 -> Circuit breaker triggers (False)
        t_severe = [
            MockTrade(-0.15, datetime(2026, 11, 15, 11, 0, tzinfo=UTC)),
            MockTrade(-0.12, datetime(2026, 11, 15, 10, 0, tzinfo=UTC)),
            MockTrade(-0.10, datetime(2026, 11, 15, 9, 0, tzinfo=UTC)),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else t_severe
            self.assertFalse(strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))

        # Case 2: 3 minor losses > -0.10 (-0.05, -0.02, -0.01) -> Does NOT trigger
        t_minor = [
            MockTrade(-0.05, datetime(2026, 11, 15, 11, 0, tzinfo=UTC)),
            MockTrade(-0.02, datetime(2026, 11, 15, 10, 0, tzinfo=UTC)),
            MockTrade(-0.01, datetime(2026, 11, 15, 9, 0, tzinfo=UTC)),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else t_minor
            self.assertTrue(strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))

        # Case 3: Streak reset by minor loss: -0.15, -0.12, -0.05, -0.14 -> Streak is 2, Does NOT trigger
        t_reset = [
            MockTrade(-0.15, datetime(2026, 11, 15, 11, 0, tzinfo=UTC)),
            MockTrade(-0.12, datetime(2026, 11, 15, 10, 0, tzinfo=UTC)),
            MockTrade(-0.05, datetime(2026, 11, 15, 9, 0, tzinfo=UTC)),
            MockTrade(-0.14, datetime(2026, 11, 15, 8, 0, tzinfo=UTC)),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else t_reset
            self.assertTrue(strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))

    def test_short_sniper_circuit_breaker_streak_reset(self):
        """Verify ShortSniper circuit breaker resets streak when a trade profit is between -0.10 and 0.0."""
        cfg = Configuration.from_files(["user_data/config.json"])
        short_strat = StrategyMTFShortSniper(config=cfg)

        class MockTrade:
            def __init__(self, close_profit, close_date, strategy="StrategyMTFShortSniper"):
                self.close_profit = close_profit
                self.close_date = close_date
                self.strategy = strategy
                self.is_short = True
                self.pair = "BTC/USDT:USDT"
                self.calc_profit_ratio = lambda p: 0.0

        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        # Sequence: -15%, -12%, -5% (broke streak), -14% -> Consecutive losses is 2, NOT 3
        trades = [
            MockTrade(-0.15, datetime(2026, 11, 15, 11, 0, tzinfo=UTC)),
            MockTrade(-0.12, datetime(2026, 11, 15, 10, 0, tzinfo=UTC)),
            MockTrade(-0.05, datetime(2026, 11, 15, 9, 0, tzinfo=UTC)),
            MockTrade(-0.14, datetime(2026, 11, 15, 8, 0, tzinfo=UTC)),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else trades
            self.assertTrue(short_strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))

    def test_short_sniper_informative_fail_closed(self):
        """Verify StrategyMTFShortSniper fails closed when informative columns are missing."""
        cfg = Configuration.from_files(["user_data/config.json"])
        short_strat = StrategyMTFShortSniper(config=cfg)

        dates = pd.date_range("2026-01-01", periods=50, freq="15min")
        # Construct dataframe with perfect sniper entry setup but omit required 1d columns
        df = pd.DataFrame({
            "date": dates,
            "open": [100.0] * 50, "high": [101.0] * 50, "low": [98.0] * 50, "close": [98.5] * 50,
            "volume": [1000.0] * 50, "body": [1.5] * 50, "total_range": [3.0] * 50, "atr": [1.0] * 50,
            "swing_low": [99.0] * 50, "ema_10": [102.0] * 50, "ema_21": [103.0] * 50, "rsi": [45.0] * 50,
            "close_1h": [98.5] * 50, "ema_50_1h": [98.0] * 50,
            "macdhist_1h": [-0.5] * 50, "macdhist_prev_1h": [0.0] * 50,
            "rsi_max_24_1h": [65.0] * 50, "is_bull_wave_4h": [0] * 50,
        })
        # Missing 'is_macro_bull_run_1d', 'swing_low_20d_1d', 'is_deep_capitulation_1d'
        out = short_strat.populate_entry_trend(df, {"pair": "BTC/USDT:USDT"})
        self.assertEqual(out["enter_short"].sum(), 0.0, "Must be fail-closed when macro informative columns are missing")

    def test_hunter_4h_macro_three_state_classification(self):
        """Legacy hunter_merge populate_entry path removed — SensorSuite live does not call JevHunter."""
        self.skipTest("Retired with strategies/active hunter_merge; entry is SensorSuite now.")

    def test_circuit_breaker_strategy_isolation(self):
        """Verify that Circuit Breaker filters trades by strategy name, preventing cross-strategy contamination."""
        cfg = Configuration.from_files(["user_data/config.json"])
        mtf_strat = StrategyMTFSniper(config=cfg)
        short_strat = StrategyMTFShortSniper(config=cfg)

        class MockTrade:
            def __init__(self, close_profit, close_date, strategy_name):
                self.close_profit = close_profit
                self.close_date = close_date
                self.strategy = strategy_name
                self.calc_profit_ratio = lambda p: 0.0

        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

        # 3 consecutive severe losses belonging exclusively to StrategyMTFShortSniper
        short_sniper_losses = [
            MockTrade(-0.15, datetime(2026, 11, 15, 11, 0, tzinfo=UTC), "StrategyMTFShortSniper"),
            MockTrade(-0.12, datetime(2026, 11, 15, 10, 0, tzinfo=UTC), "StrategyMTFShortSniper"),
            MockTrade(-0.14, datetime(2026, 11, 15, 9, 0, tzinfo=UTC), "StrategyMTFShortSniper"),
        ]

        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else short_sniper_losses

            # StrategyMTFSniper MUST NOT be blocked by StrategyMTFShortSniper's losses
            self.assertTrue(
                mtf_strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "long"),
                "StrategyMTFSniper must ignore losses from other strategies"
            )

            # StrategyMTFShortSniper MUST be blocked by its own 3 consecutive severe losses
            self.assertFalse(
                short_strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"),
                "StrategyMTFShortSniper must trigger its own circuit breaker"
            )

    def test_short_sniper_leverage_bound(self):
        """Verify StrategyMTFShortSniper harmonizes leverage bound with min(10.0, float(max_leverage))."""
        cfg = Configuration.from_files(["user_data/config.json"])
        short_strat = StrategyMTFShortSniper(config=cfg)

        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        self.assertEqual(short_strat.leverage("BTC/USDT:USDT", now, 100.0, 1.0, 125.0, "entry", "short"), 10.0)
        self.assertEqual(short_strat.leverage("BTC/USDT:USDT", now, 100.0, 1.0, 5.0, "entry", "short"), 5.0)
        self.assertEqual(short_strat.leverage("BTC/USDT:USDT", now, 100.0, 1.0, 10.0, "entry", "short"), 10.0)
        self.assertEqual(short_strat.version(), f"v{short_strat.strategy_version}")


class TestHunterPayloadHashCompleteness(unittest.TestCase):
    """
    P1 Requirement 2: Verify Hunter payload hash completeness and determinism.
    - OHLCV same but rel_vol changed -> hash must change.
    - volume_ratio_20 changed -> hash must change.
    - macro context changed -> hash must change.
    - event trigger changed -> hash must change.
    - same payload -> hash must be identical.
    - NaN, Inf, None handled consistently.
    """

    def setUp(self):
        dates = pd.date_range("2026-01-01", periods=16, freq="15min")
        self.df = pd.DataFrame({
            "date": dates,
            "open": [100.0] * 16,
            "high": [105.0] * 16,
            "low": [99.0] * 16,
            "close": [102.0] * 16,
            "volume": [1000.0] * 16,
            "rel_vol": [1.5] * 16,
            "volume_ratio_20": [1.2] * 16
        })
        self.macro = {"trend_4h": "BULL", "ema50_1h_dist_pct": 0.5, "atr_percentile": 60.0}

    def test_hash_determinism_identical_payload(self):
        h1 = compute_payload_hash(build_canonical_payload(self.df, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro))
        h2 = compute_payload_hash(build_canonical_payload(self.df.copy(), "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro.copy()))
        self.assertEqual(h1, h2)

    def test_hash_sensitivity_rel_vol(self):
        h_base = compute_payload_hash(build_canonical_payload(self.df, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro))
        df_mod = self.df.copy()
        df_mod.loc[15, "rel_vol"] = 3.5
        h_mod = compute_payload_hash(build_canonical_payload(df_mod, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro))
        self.assertNotEqual(h_base, h_mod)

    def test_hash_sensitivity_volume_ratio_20(self):
        h_base = compute_payload_hash(build_canonical_payload(self.df, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro))
        df_mod = self.df.copy()
        df_mod.loc[15, "volume_ratio_20"] = 4.2
        h_mod = compute_payload_hash(build_canonical_payload(df_mod, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro))
        self.assertNotEqual(h_base, h_mod)

    def test_hash_sensitivity_macro_context(self):
        h_base = compute_payload_hash(build_canonical_payload(self.df, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro))
        macro_mod = self.macro.copy()
        macro_mod["trend_4h"] = "BEAR"
        h_mod = compute_payload_hash(build_canonical_payload(self.df, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", macro_mod))
        self.assertNotEqual(h_base, h_mod)

    def test_hash_sensitivity_event_trigger(self):
        h_base = compute_payload_hash(build_canonical_payload(self.df, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro))
        h_mod = compute_payload_hash(build_canonical_payload(self.df, "BTC/USDT", "2026-01-01 12:00:00", "BREAKDOWN_24H", self.macro))
        self.assertNotEqual(h_base, h_mod)

    def test_hash_handles_nan_and_inf_cleanly(self):
        df_nan = self.df.copy()
        df_nan.loc[0, "rel_vol"] = float("nan")
        df_nan.loc[1, "volume_ratio_20"] = float("inf")
        payload = build_canonical_payload(df_nan, "BTC/USDT", "2026-01-01 12:00:00", "SURGE", self.macro)
        h = compute_payload_hash(payload)
        self.assertTrue(isinstance(h, str) and len(h) > 0)


class TestExternalModelVersionImmutability(unittest.TestCase):
    """
    P1 Requirement 3: External Model Version Immutability & Policy.
    - Two different model versions cannot share cache.
    - Backend returning new model version creates new cache identity.
    - Missing model version handled according to configured policy.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test_ext_cache.sqlite")
        self.cache = JevHunterCache(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_different_model_versions_do_not_share_cache(self):
        pair = "BTC/USDT:USDT"
        ts = "2026-01-01 10:00:00"
        h = "hash_model_iso_1"

        # Save verdict under model-v1.0
        self.cache.put(
            pair, ts, "enter_short", "waterfall_collapse", 0.90,
            JevHunterStatus.CONFIRMED.value, {}, payload_hash=h,
            model_version="jev-hunter-v1.0"
        )

        # Lookup with model-v1.0 -> hit
        self.assertIsNotNone(self.cache.get(pair, ts, model_version="jev-hunter-v1.0", payload_hash=h))
        # Lookup with model-v2.0 -> miss (isolation enforced)
        self.assertIsNone(self.cache.get(pair, ts, model_version="jev-hunter-v2.0", payload_hash=h))

    def test_backend_actual_model_version_saved_to_cache(self):
        """When backend returns model_version in response, that exact version is recorded in verdict and cache."""
        engine = JevHunterEngine(
            api_url="http://fake.api", api_key="secret",
            cache_db=self.db_path, mode="external",
            model_version="configured-alias-v1"
        )
        dates = pd.date_range("2026-01-01", periods=16, freq="15min")
        df_16 = pd.DataFrame({
            "date": dates, "open": [100.0]*16, "high": [101.0]*16, "low": [98.0]*16, "close": [98.5]*16,
            "volume": [1000.0]*16, "rel_vol": [1.0]*16, "volume_ratio_20": [1.0]*16
        })

        mock_api_response = {
            "model_version": "concrete-immutable-20261117",
            "answers": {
                "suggested_action": {"choice": "enter_short", "probabilities": {"enter_short": 0.89}},
                "setup_type": {"choice": "waterfall_collapse"}
            }
        }

        class MockResponse:
            def read(self):
                return json.dumps(mock_api_response).encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *args): pass

        with patch("urllib.request.urlopen", return_value=MockResponse()):
            verdict = engine.evaluate_candidate(df_16, "BTC/USDT:USDT", "2026-01-01 10:00:00", "SURGE")
            self.assertEqual(verdict["model_version"], "concrete-immutable-20261117")

            # Check cache: must be stored under concrete-immutable-20261117
            cached = self.cache.get("BTC/USDT:USDT", "2026-01-01 10:00:00", model_version="concrete-immutable-20261117")
            self.assertIsNotNone(cached)
            self.assertEqual(cached["model_version"], "concrete-immutable-20261117")

    def test_missing_model_policy_no_cache(self):
        """When backend does not return model_version and policy='no_cache', result is NOT cached."""
        engine = JevHunterEngine(
            api_url="http://fake.api", api_key="secret",
            cache_db=self.db_path, mode="external",
            model_version="alias-v1", missing_model_policy="no_cache"
        )
        dates = pd.date_range("2026-01-01", periods=16, freq="15min")
        df_16 = pd.DataFrame({
            "date": dates, "open": [100.0]*16, "high": [101.0]*16, "low": [98.0]*16, "close": [98.5]*16,
            "volume": [1000.0]*16
        })

        mock_api_response = {
            # No model_version in response
            "answers": {
                "suggested_action": {"choice": "enter_short", "probabilities": {"enter_short": 0.89}},
                "setup_type": {"choice": "waterfall_collapse"}
            }
        }

        class MockResponse:
            def read(self): return json.dumps(mock_api_response).encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *args): pass

        with patch("urllib.request.urlopen", return_value=MockResponse()):
            verdict = engine.evaluate_candidate(df_16, "BTC/USDT:USDT", "2026-01-01 11:00:00", "SURGE")
            self.assertEqual(verdict["status"], "CONFIRMED")
            # Verify NOT cached
            cached = self.cache.get("BTC/USDT:USDT", "2026-01-01 11:00:00", model_version="alias-v1")
            self.assertIsNone(cached)


class TestCircuitBreakerIsolationScenarios(unittest.TestCase):
    """
    P1 Requirement 4: Circuit Breaker Strategy Isolation.
    - LongSniper only affected by LongSniper trades.
    - ShortSniper only affected by ShortSniper trades.
    - Trade with strategy None does not affect either.
    - Trade with another strategy does not affect either.
    """

    def setUp(self):
        cfg = Configuration.from_files(["user_data/config.json"])
        self.mtf_strat = StrategyMTFSniper(config=cfg)
        self.short_strat = StrategyMTFShortSniper(config=cfg)

    def test_untracked_strategy_none_does_not_affect_circuit_breaker(self):
        """Trades with strategy=None must NOT trigger circuit breaker on either strategy."""
        class MockTrade:
            def __init__(self, close_profit, close_date, strategy=None):
                self.close_profit = close_profit
                self.close_date = close_date
                self.strategy = strategy
                self.pair = "BTC/USDT:USDT"
                self.is_short = False
                self.calc_profit_ratio = lambda p: 0.0

        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        none_trades = [
            MockTrade(-0.25, datetime(2026, 11, 15, 11, 0, tzinfo=UTC), strategy=None),
            MockTrade(-0.20, datetime(2026, 11, 15, 10, 0, tzinfo=UTC), strategy=None),
            MockTrade(-0.15, datetime(2026, 11, 15, 9, 0, tzinfo=UTC), strategy=None),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else none_trades
            self.assertTrue(self.mtf_strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "long"))
            self.assertTrue(self.short_strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))

    def test_other_strategy_does_not_affect_circuit_breaker(self):
        """Trades from 'OtherStrategy' must NOT trigger circuit breaker on StrategyMTFSniper or StrategyMTFShortSniper."""
        class MockTrade:
            def __init__(self, close_profit, close_date, strategy="OtherStrategy"):
                self.close_profit = close_profit
                self.close_date = close_date
                self.strategy = strategy
                self.pair = "BTC/USDT:USDT"
                self.is_short = False
                self.calc_profit_ratio = lambda p: 0.0

        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        other_trades = [
            MockTrade(-0.25, datetime(2026, 11, 15, 11, 0, tzinfo=UTC), strategy="OtherStrategy"),
            MockTrade(-0.20, datetime(2026, 11, 15, 10, 0, tzinfo=UTC), strategy="OtherStrategy"),
            MockTrade(-0.15, datetime(2026, 11, 15, 9, 0, tzinfo=UTC), strategy="OtherStrategy"),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else other_trades
            self.assertTrue(self.mtf_strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "long"))
            self.assertTrue(self.short_strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))


class TestRealSqliteConcurrency(unittest.TestCase):
    """
    P1 Requirement 5: Real SQLite Concurrency Tests.
    - Multiple threads writing different payloads at same pair/timestamp concurrently.
    - Multiple processes opening the same SQLite database.
    - Concurrent reads and writes.
    - Database reopened and verified.
    - No corruption, no lost writes, no cross-payload overwrite.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test_concurrency.sqlite")
        self.cache = JevHunterCache(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_multithreaded_concurrent_writes_same_timestamp(self):
        """10 threads concurrently write different payloads to same (pair, timestamp). All 10 must persist without loss."""
        import threading
        pair = "BTC/USDT:USDT"
        ts = "2026-11-12 13:00:00"
        num_threads = 10
        errors = []

        def worker(thread_id):
            try:
                c = JevHunterCache(self.db_path)
                h = f"thread_payload_hash_{thread_id}"
                c.put(
                    pair, ts, "enter_short", "waterfall_collapse", 0.85 + (thread_id * 0.01),
                    JevHunterStatus.CONFIRMED.value, {"tid": thread_id},
                    payload_hash=h, model_version=HUNTER_MODEL_VERSION_NATIVE
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread write errors: {errors}")

        for i in range(num_threads):
            h = f"thread_payload_hash_{i}"
            res = self.cache.get(pair, ts, model_version=HUNTER_MODEL_VERSION_NATIVE, payload_hash=h)
            self.assertIsNotNone(res, f"Lost write for payload hash {h}")
            self.assertEqual(res["raw_response"]["tid"], i)

    def test_multiprocess_concurrent_reads_writes(self):
        """Multiple OS processes concurrently open the same SQLite cache and perform reads & writes."""
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        pair = "ETH/USDT:USDT"
        num_procs = 4

        def proc_worker(proc_id, db_path):
            c = JevHunterCache(db_path)
            for j in range(5):
                ts = f"2026-01-01 {proc_id:02d}:{j:02d}:00"
                h = f"proc_{proc_id}_hash_{j}"
                c.put(
                    pair, ts, "enter_long", "absorption_trap", 0.88,
                    JevHunterStatus.CONFIRMED.value, {"p": proc_id, "j": j},
                    payload_hash=h, model_version=HUNTER_MODEL_VERSION_NATIVE
                )
                hit = c.get(pair, ts, model_version=HUNTER_MODEL_VERSION_NATIVE, payload_hash=h)
                assert hit is not None and hit["raw_response"]["p"] == proc_id

        procs = [ctx.Process(target=proc_worker, args=(i, self.db_path)) for i in range(num_procs)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()

        for p in procs:
            self.assertEqual(p.exitcode, 0, "Child process failed with error")

        with sqlite3.connect(self.db_path) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
            count = conn.execute("SELECT count(*) FROM jev_hunter_verdicts WHERE pair=?", (pair,)).fetchone()[0]
            self.assertEqual(count, num_procs * 5)

    def test_concurrent_initialization_migration(self):
        """Simultaneous instantiation of JevHunterCache from multiple threads on same DB must succeed without lock collision."""
        import threading
        new_db = str(Path(self.temp_dir) / "fresh_concurrent.sqlite")
        errors = []

        def init_worker():
            try:
                c = JevHunterCache(new_db)
                c.put("BTC/USDT", "2026-01-01", "enter_short", "waterfall_collapse", 0.9,
                      JevHunterStatus.CONFIRMED.value, {}, payload_hash="init_h", model_version=HUNTER_MODEL_VERSION_NATIVE)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=init_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent init errors: {errors}")
        with sqlite3.connect(new_db) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")


class TestShortSniperCooldownConsistency(unittest.TestCase):
    """
    P2 Requirement 6: ShortSniper Cooldown Consistency.
    - RangeIndex.
    - DatetimeIndex.
    - Non-contiguous index.
    - Multiple raw signals in same cooldown window.
    - Boundary signal testing.
    """

    def test_cooldown_range_index(self):
        sig = pd.Series([False]*10)
        sig.iloc[1] = True
        sig.iloc[2] = True
        sig.iloc[4] = True
        res = StrategyMTFShortSniper.apply_cooldown(sig, cooldown=3)
        self.assertEqual(res.to_list(), [False, True, False, False, True, False, False, False, False, False])

    def test_cooldown_datetime_index(self):
        dates = pd.date_range("2026-01-01", periods=10, freq="15min")
        sig = pd.Series([False]*10, index=dates)
        sig.iloc[0] = True
        sig.iloc[1] = True
        sig.iloc[5] = True
        res = StrategyMTFShortSniper.apply_cooldown(sig, cooldown=4)
        self.assertTrue(res.iloc[0])
        self.assertFalse(res.iloc[1])
        self.assertTrue(res.iloc[5])

    def test_cooldown_non_contiguous_index(self):
        indices = [100, 250, 300, 310, 450, 500]
        sig = pd.Series([False]*6, index=indices)
        sig.iloc[0] = True
        sig.iloc[1] = True
        sig.iloc[2] = True
        res = StrategyMTFShortSniper.apply_cooldown(sig, cooldown=2)
        self.assertTrue(res.iloc[0])
        self.assertFalse(res.iloc[1])
        self.assertTrue(res.iloc[2])

    def test_cooldown_exact_boundary(self):
        sig = pd.Series([False]*10)
        sig.iloc[2] = True
        sig.iloc[5] = True
        sig.iloc[6] = True
        res = StrategyMTFShortSniper.apply_cooldown(sig, cooldown=4)
        self.assertTrue(res.iloc[2])
        self.assertFalse(res.iloc[5])
        self.assertTrue(res.iloc[6])


if __name__ == "__main__":
    unittest.main()
