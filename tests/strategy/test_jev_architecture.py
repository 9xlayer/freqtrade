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

import os
import sys
import json
import sqlite3
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

# Add strategies/active to path
workspace_dir = Path(__file__).resolve().parents[2]
strat_dir = workspace_dir / "strategies" / "active"
if str(strat_dir) not in sys.path:
    sys.path.insert(0, str(strat_dir))

from jev_decision import (
    CandleTrajectoryExtractor,
    JevDecisionEngine,
    JevDecision,
    JevSide,
    JevStatus,
    JevMarketRegime,
    make_verdict,
    ENGINE_VERSION as DECISION_ENGINE_VERSION
)
from jev_entry_hunter import (
    MomentumRadar,
    JevHunterEngine,
    JevHunterCache,
    JevHunterAction,
    JevSetupType,
    JevHunterStatus,
    is_setup_valid_for_side,
    HUNTER_ENGINE_VERSION,
    HUNTER_MODEL_VERSION_NATIVE,
    HUNTER_MODEL_VERSION_EXTERNAL
)
from StrategyMTFSniper import StrategyMTFSniper
from StrategyMTFShortSniper import StrategyMTFShortSniper
from freqtrade.configuration import Configuration


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
        self.db_path = os.path.join(self.temp_dir, "test_hunter.sqlite")
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

        self.assertTrue(is_setup_valid_for_side(JevSetupType.TREND_PULLBACK, "long"))
        self.assertTrue(is_setup_valid_for_side(JevSetupType.ABSORPTION_TRAP, "long"))
        self.assertFalse(is_setup_valid_for_side(JevSetupType.TREND_PULLBACK, "short"))

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
        self.db_path = os.path.join(self.temp_dir, "test_decision.sqlite")
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

        # LONG only accepts trend_pullback and absorption_trap
        self.assertTrue(is_setup_valid_for_side("trend_pullback", "long"))
        self.assertTrue(is_setup_valid_for_side("absorption_trap", "long"))
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
            with patch("jev_decision.JevDecisionEngine.evaluate") as mock_eval:
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
            def __init__(self, close_profit, close_date):
                self.close_profit = close_profit
                self.close_date = close_date
                self.is_short = False
                self.pair = "BTC/USDT:USDT"
                self.calc_profit_ratio = lambda p: 0.0

        now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)

        # Case 1: 3 severe losses <= -0.10 -> Circuit breaker triggers (False)
        t_severe = [
            MockTrade(-0.15, datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc)),
            MockTrade(-0.12, datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)),
            MockTrade(-0.10, datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else t_severe
            self.assertFalse(strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))

        # Case 2: 3 minor losses > -0.10 (-0.05, -0.02, -0.01) -> Does NOT trigger
        t_minor = [
            MockTrade(-0.05, datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc)),
            MockTrade(-0.02, datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)),
            MockTrade(-0.01, datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else t_minor
            self.assertTrue(strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))

        # Case 3: Streak reset by minor loss: -0.15, -0.12, -0.05, -0.14 -> Streak is 2, Does NOT trigger
        t_reset = [
            MockTrade(-0.15, datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc)),
            MockTrade(-0.12, datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)),
            MockTrade(-0.05, datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)),
            MockTrade(-0.14, datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)),
        ]
        with patch("freqtrade.persistence.Trade.get_trades_proxy") as mock_proxy:
            mock_proxy.side_effect = lambda is_open=False: [] if is_open else t_reset
            self.assertTrue(strat.confirm_trade_entry("BTC/USDT:USDT", "market", 1.0, 100.0, "gtc", now, "entry", "short"))

    def test_short_sniper_circuit_breaker_streak_reset(self):
        """Verify ShortSniper circuit breaker resets streak when a trade profit is between -0.10 and 0.0."""
        cfg = Configuration.from_files(["user_data/config.json"])
        short_strat = StrategyMTFShortSniper(config=cfg)

        class MockTrade:
            def __init__(self, close_profit, close_date):
                self.close_profit = close_profit
                self.close_date = close_date
                self.is_short = True
                self.pair = "BTC/USDT:USDT"
                self.calc_profit_ratio = lambda p: 0.0

        now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
        # Sequence: -15%, -12%, -5% (broke streak), -14% -> Consecutive losses is 2, NOT 3
        trades = [
            MockTrade(-0.15, datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc)),
            MockTrade(-0.12, datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)),
            MockTrade(-0.05, datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)),
            MockTrade(-0.14, datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)),
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
        """Verify StrategyMTFSniper.populate_entry_trend accurately passes BULL, BEAR, and NEUTRAL to Hunter."""
        import sys
        sys.path.insert(0, "strategies/active")
        import jev_entry_hunter

        cfg = Configuration.from_files(["user_data/config.json"])
        strat = StrategyMTFSniper(config=cfg)

        dates = pd.date_range("2026-01-01", periods=35, freq="15min")
        base_df = pd.DataFrame({
            "date": dates,
            "open": [100.0] * 35, "high": [101.0] * 35, "low": [99.0] * 35, "close": [100.0] * 35,
            "volume": [1000.0] * 35, "body": [0.5] * 35, "total_range": [2.0] * 35, "atr": [1.0] * 35,
            "atr_pct": [50.0] * 35, "rsi": [50.0] * 35, "ema_10": [100.0] * 35, "ema_21": [100.0] * 35,
            "ema_200": [100.0] * 35, "swing_low": [98.0] * 35, "swing_high": [102.0] * 35,
            "close_1h": [100.0] * 35, "ema_50_1h": [100.0] * 35, "macdhist_1h": [0.0] * 35,
            "macdhist_prev_1h": [0.0] * 35, "rsi_max_24_1h": [60.0] * 35, "rsi_min_24_1h": [40.0] * 35,
            "macdhist_4h": [0.0] * 35, "ema_50_1d": [100.0] * 35, "macdhist_1d": [0.0] * 35, "pdh_1d": [105.0] * 35,
            "is_bull_wave_4h": [0] * 35, "is_bear_wave_4h": [0] * 35
        })

        mock_hunter = MagicMock()
        mock_hunter.evaluate_candidate.return_value = {"action": "no_trade", "status": "REJECTED"}
        strat._hunter = mock_hunter

        with patch.object(jev_entry_hunter.MomentumRadar, "find_candidates", return_value=[(32, "BREAKOUT_24H_SHORT")]):
            with patch.object(jev_entry_hunter, "JevHunterEngine", return_value=mock_hunter):
                # 1. NEUTRAL: both waves are 0
                strat.populate_entry_trend(base_df.copy(), {"pair": "BTC/USDT:USDT"})
                macro_info = mock_hunter.evaluate_candidate.call_args[0][4]
                self.assertEqual(macro_info["trend_4h"], "NEUTRAL", "Lack of bull and bear wave must be NEUTRAL")

                # 2. BULL: is_bull_wave_4h = 1
                df_bull = base_df.copy()
                df_bull.loc[32, "is_bull_wave_4h"] = 1
                strat.populate_entry_trend(df_bull, {"pair": "BTC/USDT:USDT"})
                macro_info = mock_hunter.evaluate_candidate.call_args[0][4]
                self.assertEqual(macro_info["trend_4h"], "BULL")

                # 3. BEAR: is_bear_wave_4h = 1
                df_bear = base_df.copy()
                df_bear.loc[32, "is_bear_wave_4h"] = 1
                strat.populate_entry_trend(df_bear, {"pair": "BTC/USDT:USDT"})
                macro_info = mock_hunter.evaluate_candidate.call_args[0][4]
                self.assertEqual(macro_info["trend_4h"], "BEAR")

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

        now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)

        # 3 consecutive severe losses belonging exclusively to StrategyMTFShortSniper
        short_sniper_losses = [
            MockTrade(-0.15, datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc), "StrategyMTFShortSniper"),
            MockTrade(-0.12, datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc), "StrategyMTFShortSniper"),
            MockTrade(-0.14, datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc), "StrategyMTFShortSniper"),
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

        now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(short_strat.leverage("BTC/USDT:USDT", now, 100.0, 1.0, 125.0, "entry", "short"), 10.0)
        self.assertEqual(short_strat.leverage("BTC/USDT:USDT", now, 100.0, 1.0, 5.0, "entry", "short"), 5.0)
        self.assertEqual(short_strat.leverage("BTC/USDT:USDT", now, 100.0, 1.0, 10.0, "entry", "short"), 10.0)
        self.assertEqual(short_strat.version(), f"v{short_strat.strategy_version}")


if __name__ == "__main__":
    unittest.main()
