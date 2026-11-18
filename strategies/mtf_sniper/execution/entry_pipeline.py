"""
LIVE entry generation for MTF Sniper (SensorSuite architecture).

Flow:
  SensorSuite → CandidateBuilder → DirectionalTradingEngine
  → [soft-reject] HunterEscalationGateway (optional JEV Hunter promote)
  → strategy cooldown → conflict resolve → enter_*

JEV Decision Gate remains on confirm_trade_entry (not here).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pandas import DataFrame

from strategies.mtf_sniper.core.contracts import PolicyEvaluationResult, TradeCandidate, TriggerEvent
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.common_gates import missing_informative_columns
from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine
from strategies.mtf_sniper.directional.long.policy import LongPolicyConfig
from strategies.mtf_sniper.directional.policy_escalation import is_soft_policy_reject
from strategies.mtf_sniper.directional.short.policy import ShortPolicyConfig
from strategies.mtf_sniper.intelligence.hunter_escalation import (
    HunterEscalationGateway,
    default_hunter_gateway,
)
from strategies.mtf_sniper.signals.candidate_builder import CandidateBuilder
from strategies.mtf_sniper.signals.sensor_suite import SensorSuite

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
    """SensorSuite → policy → optional Hunter escalation → entry masks."""

    def __init__(
        self,
        *,
        sensor_suite: Optional[SensorSuite] = None,
        candidate_builder: Optional[CandidateBuilder] = None,
        engine: Optional[DirectionalTradingEngine] = None,
        hunter_escalation: Optional[HunterEscalationGateway] = None,
        builder_cooldown_bars: int = 12,
        long_config: Optional[LongPolicyConfig] = None,
        short_config: Optional[ShortPolicyConfig] = None,
    ) -> None:
        self.sensor_suite = sensor_suite or SensorSuite.live()
        self.candidate_builder = candidate_builder or CandidateBuilder(
            cooldown_bars=builder_cooldown_bars
        )
        self.engine = engine or DirectionalTradingEngine(
            long_config=long_config,
            short_config=short_config,
        )
        self.hunter_escalation = hunter_escalation

    @classmethod
    def live(cls) -> "EntryPipeline":
        """Production SensorSuite catalog (Hunter attached by apply_entry_pipeline)."""
        return cls(sensor_suite=SensorSuite.live())

    def run(self, dataframe: pd.DataFrame, pair: str) -> EntryPipelineResult:
        """Scan sensors, build candidates, evaluate policies, maybe escalate."""
        self.candidate_builder.reset()
        rejected: List[dict] = []

        events = self.sensor_suite.scan(dataframe, pair)
        candidates = self.candidate_builder.process(events, rejected=rejected)

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
            idx = dataframe.index[candidate.candle_index]
            tag = str(final.metadata.get("entry_tag") or final.metadata.get("setup") or "")
            if candidate.side == TradeSide.LONG:
                enter_long.loc[idx] = True
                enter_tag.loc[idx] = tag
            else:
                enter_short.loc[idx] = True
                enter_tag.loc[idx] = tag

        both = enter_long & enter_short
        if both.any():
            enter_long = enter_long & ~both
            enter_short = enter_short & ~both
            enter_tag.loc[both] = ""
            rejected.append(
                {
                    "pair": pair,
                    "reason": "CONTRADICTORY_SIGNALS",
                    "bars": int(both.sum()),
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


def apply_entry_pipeline(
    strategy: Any,
    dataframe: DataFrame,
    metadata: dict,
) -> DataFrame:
    """
    LIVE strategy-facing entrypoint.

    SensorSuite → policies → optional JEV Hunter soft-escalate → cooldown → enter_*.
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

    long_cfg = LongPolicyConfig(
        enable_hunter_long=bool(getattr(strategy, "enable_hunter_long", False)),
    )
    short_cfg = ShortPolicyConfig(
        block_macro_bull_shorts=bool(getattr(strategy, "block_macro_bull_shorts", False)),
    )

    hunter_enabled = bool(getattr(strategy, "enable_jev_hunter", True))
    gateway = default_hunter_gateway(
        enabled=hunter_enabled,
        allow_long_promotion=bool(getattr(strategy, "enable_hunter_long", False)),
    )

    pipeline = EntryPipeline(
        sensor_suite=SensorSuite.live(),
        long_config=long_cfg,
        short_config=short_cfg,
        hunter_escalation=gateway,
    )
    result = pipeline.run(dataframe, pair)

    cand_long = result.enter_long.fillna(False).astype(bool)
    cand_short = result.enter_short.fillna(False).astype(bool)
    tags = result.enter_tag.fillna("").astype(str)

    long_cd = int(getattr(strategy.long_cooldown_candles, "value", strategy.long_cooldown_candles))
    short_cd = int(getattr(strategy.short_cooldown_candles, "value", strategy.short_cooldown_candles))
    final_long = strategy.apply_cooldown(cand_long, long_cd)
    final_short = strategy.apply_cooldown(cand_short, short_cd)

    both_mask = final_long & final_short
    if both_mask.any():
        logger.warning(
            f"[RiskGate] Simultaneous Long & Short on {both_mask.sum()} bars — cancel both."
        )
        final_long = final_long & (~both_mask)
        final_short = final_short & (~both_mask)

    dataframe.loc[final_short, "enter_short"] = 1
    dataframe.loc[final_long, "enter_long"] = 1
    for idx in dataframe.index[final_long | final_short]:
        dataframe.loc[idx, "enter_tag"] = tags.loc[idx]

    dataframe.attrs["mtf_sniper_live_entries"] = _summarize_live_entries(dataframe)
    dataframe.attrs["mtf_sniper_trigger_events"] = result.trigger_events
    dataframe.attrs["mtf_sniper_entry_rejected"] = result.rejected
    dataframe.attrs["mtf_sniper_hunter_promoted"] = [
        {
            "pair": c.pair,
            "side": c.side.value,
            "candle_index": c.candle_index,
            "tag": r.metadata.get("entry_tag"),
            "confidence": r.metadata.get("hunter_confidence"),
            "soft_reasons": r.metadata.get("policy_soft_reasons"),
        }
        for c, r in result.hunter_promoted
    ]
    return dataframe


def _summarize_live_entries(dataframe: DataFrame) -> List[dict]:
    rows: List[dict] = []
    long_mask = dataframe["enter_long"].fillna(0).astype(bool)
    short_mask = dataframe["enter_short"].fillna(0).astype(bool)
    for idx in dataframe.index[long_mask]:
        rows.append(
            {
                "side": "long",
                "tag": str(dataframe.loc[idx, "enter_tag"] or ""),
                "date": str(dataframe.loc[idx, "date"]) if "date" in dataframe.columns else str(idx),
            }
        )
    for idx in dataframe.index[short_mask]:
        rows.append(
            {
                "side": "short",
                "tag": str(dataframe.loc[idx, "enter_tag"] or ""),
                "date": str(dataframe.loc[idx, "date"]) if "date" in dataframe.columns else str(idx),
            }
        )
    return rows
