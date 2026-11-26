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
    composed = pipe.setup_composer.compose_suite(pipe.sensor_suite, df, pair)
    comp_hits = [c for c in composed if c.candle_index == idx and c.side == side]
    self.assertEqual(len(comp_hits), 1)

    events = pipe.sensor_suite.scan(df, pair)
    pipe.candidate_builder.reset()
    raw = pipe.candidate_builder.process(events)
    event = pipe.setup_composer.compose(raw)
    event_hits = [c for c in event if c.candle_index == idx and c.side == side]
    self.assertEqual(len(event_hits), 1)

    f, e = comp_hits[0], event_hits[0]
    self.assertEqual(_semantic_view(f), _semantic_view(e))
    self.assertEqual((f.raw_features or {}).get("signal_source"), "vectorized_compose")
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

    def test_apply_entry_cooldown_does_not_overwrite_registry(self):
        strat = _make_strategy(short_cooldown_candles=_param(10))
        df = _full_df()
        _force_short_sniper_bar(df, 120)
        _force_short_sniper_bar(df, 125)
        pair = "BTC/USDT:USDT"
        out = apply_entry_pipeline(strat, df, {"pair": pair})
        self.assertEqual(int(out.loc[120, "enter_short"]), 1)
        self.assertEqual(int(out.loc[125, "enter_short"]), 0)
        ts_120 = normalize_utc(df.loc[120, "date"])
        ts_125 = normalize_utc(df.loc[125, "date"])
        expected_120 = make_candidate_id(pair, TradeSide.SHORT, ts_120, 120)
        reg = strat._candidate_registry
        self.assertEqual(
            reg.lookup(pair=pair, side="short", entry_tag="short_A80_plus", current_time=ts_125),
            expected_120,
        )


class TestSetupComposerComposeSuiteParity(unittest.TestCase):
    def test_vectorized_compose_suite_matches_event_sniper_and_waterfall(self):
        from strategies.mtf_sniper.directional.setup_composer import SetupComposer
        from strategies.mtf_sniper.directional.setup_recipes import (
            SETUP_SNIPER_LONG,
            SETUP_SNIPER_SHORT,
        )
        from strategies.mtf_sniper.signals.sensors.anomaly_waterfall import WaterfallMomentumSensor

        strat = _make_strategy(enable_waterfall=True)
        df = _full_df(200)
        _force_short_sniper_bar(df, 110)
        _force_long_sniper_bar(df, 150)
        df.loc[180, ["open", "high", "low", "close", "volume"]] = [
            100.0, 100.0, 95.0, 95.2, 600.0,
        ]

        pipe = EntryPipeline.live(strat)
        composed = pipe.setup_composer.compose_suite(pipe.sensor_suite, df, "BTC/USDT:USDT")
        self.assertEqual(
            [(c.candle_index, c.side, c.primary_sensor) for c in composed],
            [
                (110, TradeSide.SHORT, SETUP_SNIPER_SHORT),
                (150, TradeSide.LONG, SETUP_SNIPER_LONG),
                (180, TradeSide.SHORT, "waterfall_drop"),
            ],
        )

        events = pipe.sensor_suite.scan(df, "BTC/USDT:USDT")
        pipe.candidate_builder.reset()
        raw = pipe.candidate_builder.process(events)
        event_composed = SetupComposer(
            recipes=pipe.setup_composer._recipes, drop_anomalies=False
        ).compose(raw)
        wf_bars = [
            int(i)
            for i in np.flatnonzero(WaterfallMomentumSensor().scan_mask(df, "BTC/USDT:USDT"))
        ]
        self.assertEqual(wf_bars, [180])
        self.assertEqual(len(composed), len(event_composed))
        for comp, ev in zip(composed, event_composed, strict=True):
            if comp.primary_sensor != "waterfall_drop":
                self.assertEqual(_semantic_view(comp), _semantic_view(ev))
            row = df.iloc[comp.candle_index].to_dict()
            self.assertEqual(
                _policy_view(pipe.engine.evaluate(comp, row)),
                _policy_view(pipe.engine.evaluate(ev, row)),
            )

    def test_vectorized_compose_suite_cancels_simultaneous_opposite_sniper(self):
        df = _full_df(140)
        df.loc[120, ["short_score", "long_score"]] = [85.0, 85.0]
        pipe = EntryPipeline.live(_make_strategy(enable_waterfall=False))
        # Simulate both sniper recipes active at bar 120 via direct _compose_from_masks.
        n = len(df)
        both_true = np.zeros(n, dtype=bool)
        both_true[120] = True
        masks = {s.name: both_true.copy() for s in pipe.sensor_suite.sensors}
        out = pipe.setup_composer._compose_from_masks(
            pipe.sensor_suite.sensors, masks, df, "BTC/USDT:USDT"
        )
        self.assertEqual(out, [])

    def test_compose_suite_falls_back_to_events_when_sensor_has_no_mask(self):
        from strategies.mtf_sniper.directional.setup_composer import SetupComposer
        from strategies.mtf_sniper.signals.sensor_suite import SensorSuite
        from strategies.mtf_sniper.signals.sensors import WaterfallDropSensor

        df = _full_df(140)
        df.loc[120, ["open", "high", "low", "close", "volume"]] = [
            100.0, 100.0, 95.0, 95.5, 600.0,
        ]
        suite = SensorSuite.from_sensors(WaterfallDropSensor())
        composer = SetupComposer(drop_anomalies=False)
        events_out: list = []
        cands = composer.compose_suite(
            suite, df, "BTC/USDT:USDT", trigger_events_out=events_out
        )
        self.assertEqual([e.candle_index for e in events_out], [120])
        self.assertEqual([c.candle_index for c in cands], [120])

    def test_context_sensor_parity_between_vectorized_and_event_paths(self):
        from strategies.mtf_sniper.directional.setup_composer import SetupComposer

        strat = _make_strategy(enable_waterfall=False)
        df = _full_df(160)
        df["market_regime_1w"] = "cycle_top"
        df["macdhist_1w"] = -310.0
        df["adx_1w"] = 48.0
        df["rsi_1w"] = 56.0
        df["is_bear_wave_1w"] = True
        df["is_bull_wave_1w"] = False
        _force_long_sniper_bar(df, 120)

        pipe = EntryPipeline.live(strat)
        comp = [
            c
            for c in pipe.setup_composer.compose_suite(pipe.sensor_suite, df, "BTC/USDT:USDT")
            if c.candle_index == 120
        ]
        events = pipe.sensor_suite.scan(df, "BTC/USDT:USDT")
        pipe.candidate_builder.reset()
        ev = [
            c
            for c in SetupComposer(recipes=pipe.setup_composer._recipes).compose(
                pipe.candidate_builder.process(events)
            )
            if c.candle_index == 120
        ]
        self.assertEqual(len(comp), 1)
        self.assertEqual(len(ev), 1)
        self.assertEqual(_semantic_view(comp[0]), _semantic_view(ev[0]))
        for key in ("regime_1w", "is_bear_wave_1w", "macdhist_1w", "context_sensors"):
            self.assertEqual(comp[0].raw_features.get(key), ev[0].raw_features.get(key))
        self.assertEqual(comp[0].raw_features.get("regime_1w"), "cycle_top")
        self.assertEqual(comp[0].confluence_count, 4)


if __name__ == "__main__":
    unittest.main()
