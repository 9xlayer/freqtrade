"""Regression: EntryPipeline must write/read by absolute position under duplicate index."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from strategies.mtf_sniper.core.candidate_id import make_candidate_id
from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.execution.entry_pipeline import (
    EntryPipeline,
    EntryPipelineResult,
    _summarize_live_entries,
    apply_entry_pipeline,
)
from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper


def _dup_df() -> pd.DataFrame:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    dates = [base + timedelta(minutes=15 * i) for i in range(4)]
    return pd.DataFrame(
        {
            "date": dates,
            "close": [100.0, 101.0, 102.0, 103.0],
            # Satisfy missing_informative_columns check with a dummy required col set
            # is not needed when we mock pipeline.run; for apply_entry we mock live().
        },
        index=[5, 7, 7, 9],
    )


def _candidate(side: TradeSide, pos: int, df: pd.DataFrame, tag: str) -> TradeCandidate:
    pair = "BTC/USDT:USDT"
    ts = df.iloc[pos]["date"]
    return TradeCandidate(
        candidate_id=make_candidate_id(pair, side, ts, pos),
        pair=pair,
        side=side,
        primary_sensor="setup:sniper_short" if side == TradeSide.SHORT else "setup:sniper_long",
        secondary_sensors=[],
        confluence_count=4,
        timestamp=ts,
        candle_index=pos,
        raw_features={"signal_source": "test"},
    )


def _accept(tag: str) -> PolicyEvaluationResult:
    return PolicyEvaluationResult(
        is_valid=True,
        score=80.0,
        quality_grade="A",
        reasons=[],
        suggested_stop_distance_pct=0.02,
        target_rr=2.0,
        metadata={"entry_tag": tag, "setup": tag},
    )


class TestEntryPipelineRunDuplicateIndex(unittest.TestCase):
    def _run_with_candidate(self, side: TradeSide, tag: str) -> tuple[pd.DataFrame, EntryPipelineResult]:
        df = _dup_df()
        cand = _candidate(side, 2, df, tag)
        pol = _accept(tag)
        pipe = EntryPipeline(
            sensor_suite=MagicMock(),
            candidate_builder=MagicMock(),
            engine=MagicMock(),
            setup_composer=MagicMock(),
            hunter_escalation=None,
        )
        pipe.setup_composer.compose_suite.return_value = [cand]
        pipe.engine.evaluate_many.return_value = [(cand, pol)]
        result = pipe.run(df, "BTC/USDT:USDT")
        return df, result

    def test_short_signal_only_at_iloc_2(self):
        _, result = self._run_with_candidate(TradeSide.SHORT, "short_A80_plus")
        self.assertEqual(result.enter_short.to_numpy().tolist(), [False, False, True, False])
        self.assertEqual(result.enter_long.to_numpy().tolist(), [False, False, False, False])
        self.assertEqual(result.enter_tag.iloc[2], "short_A80_plus")
        self.assertEqual(result.enter_tag.iloc[1], "")
        self.assertEqual(int(result.enter_short.sum()), 1)

    def test_long_signal_only_at_iloc_2(self):
        _, result = self._run_with_candidate(TradeSide.LONG, "long_A80_plus")
        self.assertEqual(result.enter_long.to_numpy().tolist(), [False, False, True, False])
        self.assertEqual(result.enter_short.to_numpy().tolist(), [False, False, False, False])
        self.assertEqual(result.enter_tag.iloc[2], "long_A80_plus")
        self.assertEqual(result.enter_tag.iloc[1], "")
        self.assertEqual(int(result.enter_long.sum()), 1)

    def test_candidate_maps_one_to_one_with_signal(self):
        df, result = self._run_with_candidate(TradeSide.SHORT, "short_A80_plus")
        self.assertEqual(len(result.accepted), 1)
        cand, pol = result.accepted[0]
        self.assertEqual(cand.candle_index, 2)
        self.assertTrue(cand.candidate_id.endswith("_2"))
        self.assertTrue(bool(result.enter_short.iloc[2]))
        # No extra signals
        self.assertEqual(int(result.enter_short.sum()), 1)
        self.assertEqual(int(result.enter_long.sum()), 0)
        self.assertEqual(pol.metadata.get("entry_tag"), "short_A80_plus")
        # Sibling duplicate label row stays clean
        self.assertFalse(bool(result.enter_short.iloc[1]))
        self.assertEqual(result.enter_tag.iloc[1], "")
        _ = df


class TestApplyEntryPipelineDuplicateIndex(unittest.TestCase):
    def test_apply_writes_positional_short_and_tag(self):
        df = _dup_df()
        # Informative cols check — inject required stubs or mock missing check via run mock
        enter_short = pd.Series([False, False, True, False], index=df.index)
        enter_long = pd.Series([False, False, False, False], index=df.index)
        enter_tag = pd.Series(["", "", "short_A80_plus", ""], index=df.index)
        cand = _candidate(TradeSide.SHORT, 2, df, "short_A80_plus")
        pol = _accept("short_A80_plus")
        pipe_result = EntryPipelineResult(
            enter_long=enter_long,
            enter_short=enter_short,
            enter_tag=enter_tag,
            accepted=[(cand, pol)],
        )
        strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
        strat.long_cooldown_candles = SimpleNamespace(value=0)
        strat.short_cooldown_candles = SimpleNamespace(value=0)
        strat._candidate_registry = None

        mock_pipe = MagicMock()
        mock_pipe.run.return_value = pipe_result
        with patch(
            "strategies.mtf_sniper.execution.entry_pipeline.missing_informative_columns",
            return_value=[],
        ), patch(
            "strategies.mtf_sniper.execution.entry_pipeline.EntryPipeline.live",
            return_value=mock_pipe,
        ):
            out = apply_entry_pipeline(strat, df, {"pair": "BTC/USDT:USDT"})

        self.assertEqual(out["enter_short"].to_numpy().tolist(), [0, 0, 1, 0])
        self.assertEqual(out["enter_long"].to_numpy().tolist(), [0, 0, 0, 0])
        self.assertEqual(out["enter_tag"].iloc[2], "short_A80_plus")
        self.assertEqual(out["enter_tag"].iloc[1], "")
        summary = out.attrs["mtf_sniper_live_entries"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["side"], "short")
        self.assertEqual(summary[0]["tag"], "short_A80_plus")
        self.assertEqual(summary[0]["date"], str(df.iloc[2]["date"]))


class TestCooldownDuplicateIndex(unittest.TestCase):
    def test_cooldown_uses_positions_not_labels(self):
        """Two hits on distinct positions sharing a label must not cross-cancel wrongly."""
        idx = [5, 7, 7, 9]
        # Signals at iloc 1 and iloc 2 (both label 7) — cooldown=1 keeps first only
        # if positional: pos1 keep, pos2 rejected (2-1 < 1 is false... wait >= 1 means
        # adjacent positions both kept when cooldown=1? Condition: pos - last >= cooldown
        # cooldown=1: pos1 keep last=1; pos2: 2-1=1 >= 1 keep. Both kept.
        # cooldown=2: pos1 keep; pos2: 2-1=1 < 2 reject. Only iloc1.
        sig = pd.Series([False, True, True, False], index=idx)
        out = StrategyMTFSniper.apply_cooldown(sig, cooldown=2)
        self.assertEqual(out.to_numpy().tolist(), [False, True, False, False])

        # Hits at iloc 0 and iloc 2 — label 5 and 7; cooldown=2 keeps both (2-0>=2)
        sig2 = pd.Series([True, False, True, False], index=idx)
        out2 = StrategyMTFSniper.apply_cooldown(sig2, cooldown=2)
        self.assertEqual(out2.to_numpy().tolist(), [True, False, True, False])

        # Duplicate label must not make iloc 3 inherit a ghost signal
        sig3 = pd.Series([False, False, True, False], index=idx)
        out3 = StrategyMTFSniper.apply_cooldown(sig3, cooldown=3)
        self.assertEqual(out3.to_numpy().tolist(), [False, False, True, False])
        self.assertFalse(bool(out3.iloc[1]))
        self.assertFalse(bool(out3.iloc[3]))


class TestSummarizeLiveEntriesDuplicateIndex(unittest.TestCase):
    def test_one_signal_one_summary_from_iloc_2(self):
        df = _dup_df()
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["enter_tag"] = ""
        df.iloc[2, df.columns.get_loc("enter_short")] = 1
        df.iloc[2, df.columns.get_loc("enter_tag")] = "short_A80_plus"
        rows = _summarize_live_entries(df)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], "short")
        self.assertEqual(rows[0]["tag"], "short_A80_plus")
        self.assertEqual(rows[0]["date"], str(df.iloc[2]["date"]))
        # Must not stringify both duplicate-label rows
        self.assertNotIn("\n", rows[0]["tag"])
        self.assertNotIn("\n", rows[0]["date"])


if __name__ == "__main__":
    unittest.main()
