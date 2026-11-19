"""Phase 2 — candidate ID contract + fast-path vs event-path semantic parity."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd

from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
from strategies.mtf_sniper.core.contracts import TriggerEvent
from strategies.mtf_sniper.core.enums import SensorType, TradeSide
from strategies.mtf_sniper.directional.long.setups import LONG_SNIPER_PART_NAMES
from strategies.mtf_sniper.directional.short.setups import SHORT_SNIPER_PART_NAMES
from strategies.mtf_sniper.execution.entry_pipeline import EntryPipeline, apply_entry_pipeline
from strategies.mtf_sniper.signals.candidate_builder import CandidateBuilder


def _param(value):
    return SimpleNamespace(value=value)


def _make_strategy(**overrides):
    base = {
        "high_vol_pct": _param(90.0),
        "block_high_vol": _param(True),
        "min_adx_4h": _param(18.0),
        "short_max_dist_ema21": _param(1.0),
        "short_candle_body_ratio_min": _param(0.62),
        "short_loc_tolerance_1h": _param(0.991),
        "short_min_1h_rsi_exhaustion": _param(55.1),
        "short_min_rsi_entry": _param(27.8),
        "short_cooldown_candles": _param(1),
        "long_max_dist_ema21": _param(3.3),
        "long_candle_body_ratio_min": _param(0.63),
        "long_loc_tolerance_1h": _param(1.008),
        "long_max_1h_rsi_exhaustion": _param(62.4),
        "long_lookback_resistance": _param(22),
        "long_max_rsi_entry": _param(67.6),
        "long_cooldown_candles": _param(1),
        "long_overextended_dist_max": _param(0.59),
        "long_block_1d_macd": _param(True),
        "block_4h_counter_wave": _param(True),
        "block_1d_counter_wave": _param(True),
        "filter_wick_rejection": _param(True),
        "max_rejection_wick_ratio": _param(0.25),
        "min_close_pos_ratio_long": _param(0.65),
        "max_close_pos_ratio_short": _param(0.35),
        "filter_pdh_ceiling": _param(True),
        "enable_hunter_long": False,
        "enable_jev_hunter": False,
        "block_macro_bull_shorts": False,
        "_get_pair_params": lambda pair: {},
    }
    base.update(overrides)
    strat = SimpleNamespace(**base)
    from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper

    strat.apply_cooldown = StrategyMTFSniper.apply_cooldown
    return strat


def _full_df(n: int = 150) -> pd.DataFrame:
    idx = pd.RangeIndex(n)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 100.0),
            "body": np.full(n, 1.0),
            "total_range": np.full(n, 2.0),
            "atr": np.full(n, 1.0),
            "atr_pct": np.full(n, 50.0),
            "rsi": np.full(n, 50.0),
            "ema_10": np.full(n, 100.0),
            "ema_21": np.full(n, 100.0),
            "ema_200": np.full(n, 95.0),
            "swing_low": np.full(n, 98.0),
            "swing_high": np.full(n, 102.0),
            "upper_wick_ratio": np.full(n, 0.1),
            "lower_wick_ratio": np.full(n, 0.1),
            "close_pos_ratio": np.full(n, 0.5),
            "long_score": np.full(n, 50.0),
            "short_score": np.full(n, 50.0),
            "close_1h": np.full(n, 100.0),
            "ema_50_1h": np.full(n, 100.0),
            "macdhist_1h": np.full(n, 0.0),
            "macdhist_prev_1h": np.full(n, 0.0),
            "rsi_max_24_1h": np.full(n, 60.0),
            "rsi_min_24_1h": np.full(n, 40.0),
            "adx_4h": np.full(n, 25.0),
            "macdhist_4h": np.full(n, 0.0),
            "is_bull_wave_4h": np.full(n, 0),
            "is_bear_wave_4h": np.full(n, 0),
            "ema_50_1d": np.full(n, 90.0),
            "ema_200_1d": np.full(n, 80.0),
            "macdhist_1d": np.full(n, 0.1),
            "pdh_1d": np.full(n, 110.0),
            "dist_from_ema_200_1d": np.full(n, 0.2),
            "is_macro_bull_run_1d": np.full(n, 0),
            "is_macro_bear_market_1d": np.full(n, 0),
            "swing_low_20d_1d": np.full(n, 70.0),
            "is_deep_capitulation_1d": np.full(n, 0),
            "is_bull_wave_1d": np.full(n, 0),
            "bb_width_4h": np.full(n, 0.05),
            "bb_width_1d": np.full(n, 0.15),
            "range_24h": np.full(n, 0.03),
            "rel_vol": np.full(n, 1.0),
            "volume_mean_20": np.full(n, 100.0),
        },
        index=idx,
    )


def _force_short_sniper_bar(df: pd.DataFrame, i: int = 120) -> None:
    df.loc[i, ["open", "close", "high", "low"]] = [100.0, 97.0, 100.0, 96.5]
    df.loc[i, "body"] = 3.0
    df.loc[i, "total_range"] = 3.5
    df.loc[i, "atr"] = 1.0
    df.loc[i, "swing_low"] = 98.0
    df.loc[i - 1, "close"] = 99.0
    df.loc[i, ["ema_10", "ema_21"]] = [97.4, 97.6]
    df.loc[i, "rsi"] = 35.0
    df.loc[i, "lower_wick_ratio"] = 0.10
    df.loc[i, "close_pos_ratio"] = 0.20
    df.loc[i, "short_score"] = 85.0
    df.loc[i, "close_1h"] = 100.0
    df.loc[i, "ema_50_1h"] = 100.5
    df.loc[i, "macdhist_1h"] = -0.1
    df.loc[i, "macdhist_prev_1h"] = 0.0
    df.loc[i, "rsi_max_24_1h"] = 60.0
    df.loc[i, "is_bull_wave_4h"] = 0
    df.loc[i, "is_macro_bull_run_1d"] = 0
    df.loc[i, "is_bull_wave_1d"] = 0
    df.loc[i, "swing_low_20d_1d"] = 70.0
    df.loc[i, "is_deep_capitulation_1d"] = 0
    df.loc[i, "atr_pct"] = 50.0
    df.loc[i, "adx_4h"] = 25.0
    df.loc[i, "volume"] = 100.0


def _force_long_sniper_bar(df: pd.DataFrame, i: int = 120) -> None:
    """Satisfy legacy long AND-mask at index i (BTC)."""
    lookback = 22
    for j in range(max(0, i - lookback), i):
        df.loc[j, "high"] = 100.0
        df.loc[j, "close"] = 99.5
    df.loc[i, ["open", "close", "high", "low"]] = [100.0, 103.0, 103.5, 99.8]
    df.loc[i, "body"] = 3.0
    df.loc[i, "total_range"] = 3.7
    df.loc[i, "atr"] = 1.0
    df.loc[i - 1, "close"] = 100.0
    df.loc[i, ["ema_10", "ema_21"]] = [101.0, 100.5]
    df.loc[i, "rsi"] = 55.0
    df.loc[i, "upper_wick_ratio"] = 0.10
    df.loc[i, "close_pos_ratio"] = 0.80
    df.loc[i, "long_score"] = 85.0
    df.loc[i, "close_1h"] = 102.0
    df.loc[i, "ema_50_1h"] = 100.0
    df.loc[i, "rsi_min_24_1h"] = 40.0
    df.loc[i, "macdhist_4h"] = 0.1
    df.loc[i, "ema_50_1d"] = 90.0
    df.loc[i, "ema_200_1d"] = 80.0
    df.loc[i, "macdhist_1d"] = 0.1
    df.loc[i, "is_macro_bear_market_1d"] = 0
    df.loc[i, "dist_from_ema_200_1d"] = 0.2
    df.loc[i, "pdh_1d"] = 110.0
    df.loc[i, "atr_pct"] = 50.0
    df.loc[i, "adx_4h"] = 25.0
    df.loc[i, "volume"] = 100.0


def _semantic_view(cand) -> dict:
    """Fields that must match across fast/event (excludes provenance signal_source)."""
    feats = cand.raw_features or {}
    score_keys = sorted(
        k for k in feats if k.endswith("_score") or k in ("short_score", "long_score")
    )
    return {
        "candidate_id": cand.candidate_id,
        "pair": cand.pair,
        "side": cand.side.value,
        "candle_index": cand.candle_index,
        "primary_sensor": cand.primary_sensor,
        "secondary_sensors": list(cand.secondary_sensors),
        "confluence_count": cand.confluence_count,
        "setup_name": feats.get("setup_name"),
        "setup_complete": bool(feats.get("setup_complete")),
        "legacy_sniper": feats.get("legacy_sniper"),
        "mask": feats.get("mask"),
        "sensors": list(feats.get("sensors") or []),
        "recipe_sensors": list(feats.get("recipe_sensors") or []),
        "scores": {k: float(feats.get(k) or 0.0) for k in score_keys},
    }


def _policy_view(pol) -> dict:
    return {
        "is_valid": pol.is_valid,
        "score": float(pol.score),
        "quality_grade": pol.quality_grade,
        "reasons": list(pol.reasons),
        "entry_tag": pol.metadata.get("entry_tag"),
        "setup": pol.metadata.get("setup"),
        "setup_eligible": pol.metadata.get("setup_eligible"),
    }


def _assert_path_parity(self, pipe, df, pair, idx, side, part_names, expected_tag):
    fast = pipe._candidates_from_sniper_masks(df, pair)
    self.assertIsNotNone(fast)
    fast_hits = [c for c in fast if c.candle_index == idx and c.side == side]
    self.assertEqual(len(fast_hits), 1)

    events = pipe.sensor_suite.scan(df, pair)
    pipe.candidate_builder.reset()
    raw = pipe.candidate_builder.process(events)
    event = pipe.setup_composer.compose(raw)
    event_hits = [c for c in event if c.candle_index == idx and c.side == side]
    self.assertEqual(len(event_hits), 1)

    f, e = fast_hits[0], event_hits[0]
    self.assertEqual(_semantic_view(f), _semantic_view(e))
    self.assertEqual((f.raw_features or {}).get("signal_source"), "vectorized_and")
    self.assertEqual((e.raw_features or {}).get("signal_source"), "event_scan")
    self.assertEqual(f.secondary_sensors, list(part_names))

    row = df.iloc[idx].to_dict()
    pol_f = pipe.engine.evaluate(f, row)
    pol_e = pipe.engine.evaluate(e, row)
    self.assertEqual(_policy_view(pol_f), _policy_view(pol_e))
    self.assertTrue(pol_f.is_valid)
    self.assertEqual(pol_f.metadata.get("entry_tag"), expected_tag)


class TestCandidateIdContract(unittest.TestCase):
    def test_normalize_naive_to_utc(self):
        ts = normalize_utc(datetime(2024, 1, 1, 12, 0, 0))
        self.assertEqual(ts.tzinfo, UTC)

    def test_make_candidate_id_stable(self):
        ts = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        a = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)
        b = make_candidate_id("BTC/USDT:USDT", "short", ts, 42)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("BTC/USDT:USDT_short_"))

    def test_builder_uses_canonical_id(self):
        ts = datetime(2024, 6, 1, 15, 0, tzinfo=UTC)
        events = [
            TriggerEvent(
                sensor_name="short_structure",
                sensor_type=SensorType.PATTERN,
                side=TradeSide.SHORT,
                pair="BTC/USDT:USDT",
                timestamp=ts,
                candle_index=10,
                raw_metrics={"part": "structure"},
            ),
            TriggerEvent(
                sensor_name="short_mtf",
                sensor_type=SensorType.PATTERN,
                side=TradeSide.SHORT,
                pair="BTC/USDT:USDT",
                timestamp=ts,
                candle_index=10,
                raw_metrics={"part": "mtf"},
            ),
        ]
        builder = CandidateBuilder(cooldown_bars=0)
        cands = builder.process(events)
        self.assertEqual(len(cands), 1)
        expected = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 10)
        self.assertEqual(cands[0].candidate_id, expected)
        self.assertEqual(cands[0].timestamp.tzinfo, UTC)


class TestFastVsEventCandidateParity(unittest.TestCase):
    """Both paths must agree on setup semantics + Policy outcome."""

    def test_short_setup_contract_and_policy_parity(self):
        strat = _make_strategy()
        df = _full_df()
        _force_short_sniper_bar(df, 120)
        pipe = EntryPipeline.live(strat)
        _assert_path_parity(
            self,
            pipe,
            df,
            "BTC/USDT:USDT",
            120,
            TradeSide.SHORT,
            SHORT_SNIPER_PART_NAMES,
            "short_A80_plus",
        )

    def test_long_setup_contract_and_policy_parity(self):
        strat = _make_strategy()
        df = _full_df()
        _force_long_sniper_bar(df, 120)
        pipe = EntryPipeline.live(strat)
        _assert_path_parity(
            self,
            pipe,
            df,
            "BTC/USDT:USDT",
            120,
            TradeSide.LONG,
            LONG_SNIPER_PART_NAMES,
            "long_A80_plus",
        )

    def test_apply_entry_registers_canonical_id(self):
        strat = _make_strategy()
        df = _full_df()
        _force_short_sniper_bar(df, 120)
        pair = "BTC/USDT:USDT"
        out = apply_entry_pipeline(strat, df, {"pair": pair})
        self.assertEqual(int(out.loc[120, "enter_short"]), 1)
        accepted = out.attrs.get("mtf_sniper_accepted_candidates") or []
        self.assertTrue(accepted)
        hit = next(a for a in accepted if a["candle_index"] == 120)
        ts = normalize_utc(df.loc[120, "date"])
        expected = make_candidate_id(pair, TradeSide.SHORT, ts, 120)
        self.assertEqual(hit["candidate_id"], expected)
        reg = getattr(strat, "_candidate_registry", None)
        self.assertIsNotNone(reg)
        looked = reg.lookup(
            pair=pair,
            side="short",
            entry_tag=hit["entry_tag"],
            current_time=ts,
        )
        self.assertEqual(looked, expected)


if __name__ == "__main__":
    unittest.main()
