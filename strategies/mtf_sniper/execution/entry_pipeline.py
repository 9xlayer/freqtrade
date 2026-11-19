"""
LIVE entry generation for MTF Sniper — deterministic flow (AI/Hunter off).

  Vectorized part AND (fast path) or SensorSuite parts → Builder → SetupComposer
    → Policy 4b → strategy cooldown → enter_*

Hunter escalation disabled until deterministic sniper is re-validated.
JEV Decision Gate remains on confirm_trade_entry (separate from Hunter).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from functools import reduce
from operator import and_
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from pandas import DataFrame

from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate, TriggerEvent
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.common_gates import missing_informative_columns
from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine
from strategies.mtf_sniper.directional.long.policy import LongPolicyConfig
from strategies.mtf_sniper.directional.long.setups import LONG_SNIPER_PART_NAMES
from strategies.mtf_sniper.directional.policy_escalation import is_soft_policy_reject
from strategies.mtf_sniper.directional.setup_composer import SetupComposer
from strategies.mtf_sniper.directional.setup_recipes import SETUP_SNIPER_LONG, SETUP_SNIPER_SHORT
from strategies.mtf_sniper.directional.short.policy import ShortPolicyConfig
from strategies.mtf_sniper.directional.short.setups import SHORT_SNIPER_PART_NAMES
from strategies.mtf_sniper.intelligence.hunter_escalation import HunterEscalationGateway
from strategies.mtf_sniper.signals.candidate_builder import CandidateBuilder
from strategies.mtf_sniper.signals.sensor_suite import SensorSuite
from strategies.mtf_sniper.signals.sensors.pattern_sniper_parts import (
    LegacySniperPartSensor,
    cached_sniper_parts,
)

logger = logging.getLogger(__name__)

@dataclass
class EntryPipelineResult:
    enter_long: pd.Series
    enter_short: pd.Series
    enter_tag: pd.Series
    candidates: List[TradeCandidate] = field(default_factory=list)
    accepted: List[Tuple[TradeCandidate, PolicyEvaluationResult]] = field(default_factory=list)
    rejected: List[dict] = field(default_factory=list)
    hunter_promoted: List[Tuple[TradeCandidate, PolicyEvaluationResult]] = field(
        default_factory=list
    )
    trigger_events: List[TriggerEvent] = field(default_factory=list)


class EntryPipeline:
    """SensorSuite → Builder → SetupComposer → Policy → entry masks."""

    def __init__(
        self,
        *,
        sensor_suite: Optional[SensorSuite] = None,
        candidate_builder: Optional[CandidateBuilder] = None,
        engine: Optional[DirectionalTradingEngine] = None,
        setup_composer: Optional[SetupComposer] = None,
        hunter_escalation: Optional[HunterEscalationGateway] = None,
        builder_cooldown_bars: int = 0,
        long_config: Optional[LongPolicyConfig] = None,
        short_config: Optional[ShortPolicyConfig] = None,
    ) -> None:
        # Builder cooldown off — strategy apply_cooldown owns spacing; avoids
        # incomplete part bars burning cooldown before a full setup arrives.
        self.sensor_suite = sensor_suite or SensorSuite.hunter()
        self.candidate_builder = candidate_builder or CandidateBuilder(
            cooldown_bars=builder_cooldown_bars
        )
        if setup_composer is not None:
            self.setup_composer = setup_composer
        else:
            # Keep anomalies only when Hunter gateway is injected (escalation path).
            self.setup_composer = SetupComposer(
                drop_anomalies=(hunter_escalation is None)
            )
        self.engine = engine or DirectionalTradingEngine(
            long_config=long_config,
            short_config=short_config,
            setup_composer=self.setup_composer,
        )
        self.hunter_escalation = hunter_escalation

    @classmethod
    def live(cls, strategy: Any, hunter_escalation: Optional[HunterEscalationGateway] = None) -> "EntryPipeline":
        # Production path: Hunter off until deterministic sniper is re-validated.
        _ = hunter_escalation  # reserved for future re-enable
        return cls(
            sensor_suite=SensorSuite.live(strategy),
            hunter_escalation=None,
            long_config=LongPolicyConfig(
                enable_hunter_long=False,
                allow_anomaly_entries=False,
            ),
            short_config=ShortPolicyConfig(
                block_macro_bull_shorts=bool(
                    getattr(strategy, "block_macro_bull_shorts", False)
                ),
                allow_anomaly_entries=False,
            ),
        )

    def run(self, dataframe: pd.DataFrame, pair: str) -> EntryPipelineResult:
        """Scan sensors, compose setups, evaluate policies."""
        self.candidate_builder.reset()
        rejected: List[dict] = []

        # Fast path: AND part masks vectorized (legacy parity) — avoids O(hits)
        # TriggerEvent explosion when each part fires independently on BT history.
        events: List[TriggerEvent] = []
        candidates = self._candidates_from_sniper_masks(dataframe, pair)
        if candidates is None:
            events = self.sensor_suite.scan(dataframe, pair)
            raw_candidates = self.candidate_builder.process(events, rejected=rejected)
            candidates = self.setup_composer.compose(raw_candidates, dropped=rejected)

        rows_by_index: Dict[int, Dict[str, Any]] = {}
        for c in candidates:
            if c.candle_index not in rows_by_index:
                rows_by_index[c.candle_index] = dataframe.iloc[c.candle_index].to_dict()

        evaluated = self.engine.evaluate_many(candidates, rows_by_index)

        enter_long = pd.Series(False, index=dataframe.index)
        enter_short = pd.Series(False, index=dataframe.index)
        enter_tag = pd.Series("", index=dataframe.index)
        accepted: List[Tuple[TradeCandidate, PolicyEvaluationResult]] = []
        hunter_promoted: List[Tuple[TradeCandidate, PolicyEvaluationResult]] = []

        for candidate, result in evaluated:
            final = result
            if not result.is_valid and self.hunter_escalation is not None:
                row = rows_by_index.get(candidate.candle_index) or {}
                attempt = self.hunter_escalation.evaluate(
                    candidate, result, dataframe, row
                )
                if attempt.promoted is not None:
                    hunter_promoted.append((candidate, attempt.promoted))
                    final = attempt.promoted
                else:
                    verdict = attempt.score_verdict
                    rejected.append(
                        {
                            "pair": candidate.pair,
                            "candle_index": candidate.candle_index,
                            "side": candidate.side.value,
                            "reasons": list(result.reasons),
                            "setup": result.metadata.get("setup"),
                            "escalation": attempt.outcome,
                            "score": None if verdict is None else verdict.score,
                            "score_passed": None if verdict is None else verdict.passed,
                            "score_reason": None if verdict is None else verdict.reason,
                            "gate_reasons": attempt.gate_reasons,
                        }
                    )
                    continue
            elif not result.is_valid:
                rejected.append(
                    {
                        "pair": candidate.pair,
                        "candle_index": candidate.candle_index,
                        "side": candidate.side.value,
                        "reasons": list(result.reasons),
                        "setup": result.metadata.get("setup"),
                        "escalation": (
                            "soft_not_promoted"
                            if is_soft_policy_reject(result)
                            else "hard"
                        ),
                    }
                )
                continue

            if not final.is_valid:
                rejected.append(
                    {
                        "pair": candidate.pair,
                        "candle_index": candidate.candle_index,
                        "side": candidate.side.value,
                        "reasons": list(final.reasons),
                        "setup": final.metadata.get("setup"),
                        "escalation": "none",
                    }
                )
                continue

            accepted.append((candidate, final))
            # candle_index is an absolute iloc position — never write via index label
            # (duplicate labels would mark multiple rows).
            pos = int(candidate.candle_index)
            tag = str(final.metadata.get("entry_tag") or final.metadata.get("setup") or "")
            if candidate.side == TradeSide.LONG:
                enter_long.iloc[pos] = True
                enter_tag.iloc[pos] = tag
            else:
                enter_short.iloc[pos] = True
                enter_tag.iloc[pos] = tag

        # Element-wise via numpy so duplicate index labels cannot realign/explode.
        long_arr = enter_long.to_numpy(dtype=bool, copy=True)
        short_arr = enter_short.to_numpy(dtype=bool, copy=True)
        both_arr = long_arr & short_arr
        if both_arr.any():
            long_arr &= ~both_arr
            short_arr &= ~both_arr
            tag_arr = enter_tag.to_numpy(copy=True)
            tag_arr[both_arr] = ""
            enter_long = pd.Series(long_arr, index=dataframe.index)
            enter_short = pd.Series(short_arr, index=dataframe.index)
            enter_tag = pd.Series(tag_arr, index=dataframe.index)
            rejected.append(
                {
                    "pair": pair,
                    "reason": "CONTRADICTORY_SIGNALS",
                    "bars": int(both_arr.sum()),
                }
            )

        return EntryPipelineResult(
            enter_long=enter_long,
            enter_short=enter_short,
            enter_tag=enter_tag,
            candidates=list(candidates),
            accepted=accepted,
            rejected=rejected,
            hunter_promoted=hunter_promoted,
            trigger_events=list(events),
        )

    def _candidates_from_sniper_masks(
        self,
        dataframe: pd.DataFrame,
        pair: str,
    ) -> Optional[List[TradeCandidate]]:
        """
        Build setup-complete candidates from vectorized AND of part masks.

        Returns None when the suite is not a pure live part-sensor catalog
        (fallback to event scan).
        """
        sensors = self.sensor_suite.sensors
        if not sensors or not all(isinstance(s, LegacySniperPartSensor) for s in sensors):
            return None

        strategy = getattr(sensors[0], "_strategy", None)
        if strategy is None:
            return None

        min_history = max(int(getattr(s, "min_history", 100)) for s in sensors)
        short_parts = cached_sniper_parts(strategy, dataframe, pair, TradeSide.SHORT)
        long_parts = cached_sniper_parts(strategy, dataframe, pair, TradeSide.LONG)

        short_and = reduce(
            and_,
            (short_parts[name].fillna(False).astype(bool) for name in SHORT_SNIPER_PART_NAMES),
        )
        long_and = reduce(
            and_,
            (long_parts[name].fillna(False).astype(bool) for name in LONG_SNIPER_PART_NAMES),
        )
        if min_history > 0:
            short_and.iloc[:min_history] = False
            long_and.iloc[:min_history] = False

        # Positional cancel — Series & under duplicate labels can mis-align.
        short_arr = short_and.to_numpy(dtype=bool, copy=True)
        long_arr = long_and.to_numpy(dtype=bool, copy=True)
        both_arr = short_arr & long_arr
        if both_arr.any():
            short_arr &= ~both_arr
            long_arr &= ~both_arr
            short_and = pd.Series(short_arr, index=dataframe.index)
            long_and = pd.Series(long_arr, index=dataframe.index)

        candidates: List[TradeCandidate] = []
        candidates.extend(
            self._candidates_for_mask(
                dataframe,
                pair,
                short_and,
                side=TradeSide.SHORT,
                setup_name=SETUP_SNIPER_SHORT,
                part_names=SHORT_SNIPER_PART_NAMES,
                score_key="short_score",
            )
        )
        candidates.extend(
            self._candidates_for_mask(
                dataframe,
                pair,
                long_and,
                side=TradeSide.LONG,
                setup_name=SETUP_SNIPER_LONG,
                part_names=LONG_SNIPER_PART_NAMES,
                score_key="long_score",
            )
        )
        candidates.sort(key=lambda c: (c.candle_index, c.side.value))
        return candidates

    def _candidates_for_mask(
        self,
        dataframe: pd.DataFrame,
        pair: str,
        mask: pd.Series,
        *,
        side: TradeSide,
        setup_name: str,
        part_names: Sequence[str],
        score_key: str,
    ) -> List[TradeCandidate]:
        hits = np.flatnonzero(mask.to_numpy(dtype=bool, na_value=False))
        if len(hits) == 0:
            return []

        has_date = "date" in dataframe.columns
        out: List[TradeCandidate] = []
        for idx in hits:
            i = int(idx)
            row = dataframe.iloc[i]
            if has_date:
                ts = normalize_utc(row["date"])
            else:
                ts = normalize_utc(datetime(1970, 1, 1))
            feats: Dict[str, Any] = {
                "setup_complete": True,
                "setup_name": setup_name,
                "legacy_sniper": side.value,
                "mask": f"legacy_and_{side.value}",
                "signal_source": "vectorized_and",
                "recipe_sensors": list(part_names),
                "sensors": list(part_names),
                "part": "structure",
                score_key: float(row.get(score_key, 0.0) or 0.0),
            }
            out.append(
                TradeCandidate(
                    candidate_id=make_candidate_id(pair, side, ts, i),
                    pair=pair,
                    side=side,
                    primary_sensor=setup_name,
                    secondary_sensors=list(part_names),
                    confluence_count=len(part_names),
                    timestamp=ts,
                    candle_index=i,
                    raw_features=feats,
                    cooldown_bars=0,
                )
            )
        return out


def apply_entry_pipeline(
    strategy: Any,
    dataframe: DataFrame,
    metadata: dict,
) -> DataFrame:
    """
    LIVE strategy-facing entrypoint — thin adapter over EntryPipeline.

    Does not short-circuit Sensor / Builder / Composer / Policy layers.
    Hunter escalation forced off in deterministic phase.
    """
    pair = metadata.get("pair", "")
    dataframe["enter_long"] = 0
    dataframe["enter_short"] = 0
    dataframe["enter_tag"] = ""

    missing_cols = missing_informative_columns(dataframe)
    if missing_cols:
        logger.warning(
            f"StrategyMTFSniper: Missing required informative columns: {missing_cols}. "
            f"Failing closed (0 entry signals)."
        )
        return dataframe

    pipeline = EntryPipeline.live(strategy, hunter_escalation=None)
    result = pipeline.run(dataframe, pair)

    cand_long = result.enter_long.fillna(False).astype(bool)
    cand_short = result.enter_short.fillna(False).astype(bool)
    tags = result.enter_tag.fillna("").astype(str)

    long_cd = int(getattr(strategy.long_cooldown_candles, "value", strategy.long_cooldown_candles))
    short_cd = int(getattr(strategy.short_cooldown_candles, "value", strategy.short_cooldown_candles))
    final_long = strategy.apply_cooldown(cand_long, long_cd)
    final_short = strategy.apply_cooldown(cand_short, short_cd)

    # Positional masks — avoid Series & / .loc[label] under duplicate indexes.
    long_arr = final_long.to_numpy(dtype=bool, copy=True)
    short_arr = final_short.to_numpy(dtype=bool, copy=True)
    both_arr = long_arr & short_arr
    if both_arr.any():
        logger.warning(
            f"[RiskGate] Simultaneous Long & Short on {int(both_arr.sum())} bars — cancel both."
        )
        long_arr &= ~both_arr
        short_arr &= ~both_arr

    n = len(dataframe)
    enter_short_out = np.zeros(n, dtype=np.int64)
    enter_long_out = np.zeros(n, dtype=np.int64)
    enter_short_out[short_arr] = 1
    enter_long_out[long_arr] = 1
    tag_src = tags.to_numpy()
    tag_out = np.where(long_arr | short_arr, tag_src, "")
    dataframe["enter_short"] = enter_short_out
    dataframe["enter_long"] = enter_long_out
    dataframe["enter_tag"] = tag_out

    # Correlate Candidate → confirm_trade_entry / Risk telemetry
    _remember_accepted_candidates(strategy, result)

    dataframe.attrs["mtf_sniper_live_entries"] = _summarize_live_entries(dataframe)
    dataframe.attrs["mtf_sniper_trigger_events"] = result.trigger_events
    dataframe.attrs["mtf_sniper_entry_rejected"] = result.rejected
    dataframe.attrs["mtf_sniper_hunter_promoted"] = []
    dataframe.attrs["mtf_sniper_accepted_candidates"] = [
        {
            "candidate_id": c.candidate_id,
            "candle_index": c.candle_index,
            "side": c.side.value,
            "entry_tag": str(pol.metadata.get("entry_tag") or ""),
            "quality_grade": pol.quality_grade,
            "signal_source": str((c.raw_features or {}).get("signal_source") or ""),
        }
        for c, pol in result.accepted
    ]
    return dataframe


def _remember_accepted_candidates(strategy: Any, result: EntryPipelineResult) -> None:
    from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry

    reg = getattr(strategy, "_candidate_registry", None)
    if reg is None:
        reg = CandidateRegistry()
        try:
            strategy._candidate_registry = reg
        except Exception:
            return
    for candidate, policy in result.accepted:
        tag = str(policy.metadata.get("entry_tag") or policy.metadata.get("setup") or "")
        reg.remember(candidate, tag)


def _summarize_live_entries(dataframe: DataFrame) -> List[dict]:
    """One summary row per positional enter_* signal (safe under duplicate index)."""
    rows: List[dict] = []
    long_pos = np.flatnonzero(
        dataframe["enter_long"].fillna(0).to_numpy(dtype=bool)
    )
    short_pos = np.flatnonzero(
        dataframe["enter_short"].fillna(0).to_numpy(dtype=bool)
    )
    has_date = "date" in dataframe.columns
    for pos in long_pos:
        row = dataframe.iloc[int(pos)]
        rows.append(
            {
                "side": "long",
                "tag": str(row.get("enter_tag") or ""),
                "date": str(row["date"]) if has_date else str(dataframe.index[int(pos)]),
            }
        )
    for pos in short_pos:
        row = dataframe.iloc[int(pos)]
        rows.append(
            {
                "side": "short",
                "tag": str(row.get("enter_tag") or ""),
                "date": str(row["date"]) if has_date else str(dataframe.index[int(pos)]),
            }
        )
    return rows
