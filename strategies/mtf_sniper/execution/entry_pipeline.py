"""
LIVE entry generation for MTF Sniper — Single-Truth unified flow.

  SensorSuite -> SetupComposer.compose_suite (SUBSYSTEM_REGISTRY)
    -> DirectionalTradingEngine (subsystem_policies) -> Policy -> entry masks
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame

from strategies.mtf_sniper.core.contracts import (
    PolicyEvaluationResult,
    TradeCandidate,
    TriggerEvent,
)
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.common_gates import missing_informative_columns
from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine
from strategies.mtf_sniper.directional.long.policy import LongPolicyConfig
from strategies.mtf_sniper.directional.policy_escalation import is_soft_policy_reject
from strategies.mtf_sniper.directional.setup_composer import SetupComposer
from strategies.mtf_sniper.directional.setup_recipes import all_live_recipes
from strategies.mtf_sniper.directional.short.policy import (
    DEFAULT_ETH_WATERFALL_FILTER,
    DEFAULT_WATERFALL_FILTER,
    ShortPolicyConfig,
    build_waterfall_config_from_params,
)
from strategies.mtf_sniper.intelligence.hunter_escalation import HunterEscalationGateway
from strategies.mtf_sniper.signals.candidate_builder import CandidateBuilder
from strategies.mtf_sniper.signals.sensor_suite import SensorSuite


logger = logging.getLogger(__name__)


@dataclass
class EntryPipelineResult:
    enter_long: pd.Series
    enter_short: pd.Series
    enter_tag: pd.Series
    candidates: list[TradeCandidate] = field(default_factory=list)
    accepted: list[tuple[TradeCandidate, PolicyEvaluationResult]] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    hunter_promoted: list[tuple[TradeCandidate, PolicyEvaluationResult]] = field(
        default_factory=list
    )
    trigger_events: list[TriggerEvent] = field(default_factory=list)


class EntryPipeline:
    """SensorSuite → Builder → SetupComposer → Policy → entry masks."""

    def __init__(
        self,
        *,
        sensor_suite: SensorSuite | None = None,
        candidate_builder: CandidateBuilder | None = None,
        engine: DirectionalTradingEngine | None = None,
        setup_composer: SetupComposer | None = None,
        hunter_escalation: HunterEscalationGateway | None = None,
        builder_cooldown_bars: int = 0,
        long_config: LongPolicyConfig | None = None,
        short_config: ShortPolicyConfig | None = None,
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
            self.setup_composer = SetupComposer(drop_anomalies=(hunter_escalation is None))
        self.engine = engine or DirectionalTradingEngine(
            long_config=long_config,
            short_config=short_config,
            setup_composer=self.setup_composer,
        )
        self.hunter_escalation = hunter_escalation

    @classmethod
    def live(
        cls, strategy: Any, hunter_escalation: HunterEscalationGateway | None = None
    ) -> EntryPipeline:
        _ = hunter_escalation

        def _pval(name: str, default: Any) -> Any:
            raw = getattr(strategy, name, None)
            if raw is None:
                return default
            return getattr(raw, "value", raw)

        enable_waterfall = bool(getattr(strategy, "enable_waterfall", False))
        pair_overrides = dict(getattr(strategy, "custom_pair_params", {}) or {})
        wf_pair_configs = {
            "ETH/USDT:USDT": DEFAULT_ETH_WATERFALL_FILTER,
            "ETH/USDT": DEFAULT_ETH_WATERFALL_FILTER,
        }
        for pair_name, params in pair_overrides.items():
            base_cfg = wf_pair_configs.get(pair_name, DEFAULT_WATERFALL_FILTER)
            wf_pair_configs[pair_name] = build_waterfall_config_from_params(base_cfg, params)

        filter_wick = bool(_pval("filter_wick_rejection", True))
        max_wick = float(_pval("max_rejection_wick_ratio", 0.25))

        return cls(
            sensor_suite=SensorSuite.live(strategy, include_waterfall=enable_waterfall),
            setup_composer=SetupComposer(
                recipes=all_live_recipes(include_waterfall=enable_waterfall),
                drop_anomalies=not enable_waterfall,
            ),
            hunter_escalation=None,
            long_config=LongPolicyConfig(
                enable_hunter_long=False,
                allow_anomaly_entries=False,
                filter_wick_rejection=filter_wick,
                max_rejection_wick_ratio=max_wick,
                min_close_pos_ratio=float(_pval("min_close_pos_ratio_long", 0.70)),
                max_rsi_entry=float(_pval("long_max_rsi_entry", 68.1)),
                max_dist_ema21_pct=float(_pval("long_max_dist_ema21", 3.3)),
                overextended_dist_max=float(_pval("long_overextended_dist_max", 0.53)),
                filter_pdh_ceiling=bool(_pval("filter_pdh_ceiling", True)),
            ),
            short_config=ShortPolicyConfig(
                block_macro_bull_shorts=bool(getattr(strategy, "block_macro_bull_shorts", False)),
                allow_anomaly_entries=enable_waterfall,
                waterfall_pair_configs=wf_pair_configs,
                filter_wick_rejection=filter_wick,
                max_rejection_wick_ratio=max_wick,
                max_close_pos_ratio=float(_pval("max_close_pos_ratio_short", 0.30)),
                min_rsi_entry=float(_pval("short_min_rsi_entry", 27.8)),
                max_dist_ema21_pct=float(_pval("short_max_dist_ema21", 1.0)),
            ),
        )

    def _resolve_candidate(
        self,
        candidate: TradeCandidate,
        result: PolicyEvaluationResult,
        rows_by_index: dict[int, dict[str, Any]],
        dataframe: pd.DataFrame,
        rejected: list[dict],
        hunter_promoted: list[tuple[TradeCandidate, PolicyEvaluationResult]],
    ) -> PolicyEvaluationResult | None:
        if not result.is_valid and self.hunter_escalation is not None:
            row = rows_by_index.get(candidate.candle_index) or {}
            attempt = self.hunter_escalation.evaluate(candidate, result, dataframe, row)
            if attempt.promoted is not None:
                hunter_promoted.append((candidate, attempt.promoted))
                return attempt.promoted
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
            return None
        if not result.is_valid:
            rejected.append(
                {
                    "pair": candidate.pair,
                    "candle_index": candidate.candle_index,
                    "side": candidate.side.value,
                    "reasons": list(result.reasons),
                    "setup": result.metadata.get("setup"),
                    "escalation": (
                        "soft_not_promoted" if is_soft_policy_reject(result) else "hard"
                    ),
                }
            )
            return None
        return result

    def run(self, dataframe: pd.DataFrame, pair: str) -> EntryPipelineResult:
        """Scan sensors, compose setups, evaluate policies."""
        self.candidate_builder.reset()
        rejected: list[dict] = []

        events: list[TriggerEvent] = []
        candidates = self.setup_composer.compose_suite(
            self.sensor_suite,
            dataframe,
            pair,
            candidate_builder=self.candidate_builder,
            dropped=rejected,
            trigger_events_out=events,
        )

        rows_by_index: dict[int, dict[str, Any]] = {}
        for c in candidates:
            if c.candle_index not in rows_by_index:
                rows_by_index[c.candle_index] = dataframe.iloc[c.candle_index].to_dict()

        evaluated = self.engine.evaluate_many(candidates, rows_by_index)

        enter_long = pd.Series(False, index=dataframe.index)
        enter_short = pd.Series(False, index=dataframe.index)
        enter_tag = pd.Series("", index=dataframe.index)
        accepted: list[tuple[TradeCandidate, PolicyEvaluationResult]] = []
        hunter_promoted: list[tuple[TradeCandidate, PolicyEvaluationResult]] = []

        for candidate, result in evaluated:
            final = self._resolve_candidate(
                candidate, result, rows_by_index, dataframe, rejected, hunter_promoted
            )
            if final is None or not final.is_valid:
                continue

            pos = candidate.candle_index
            tag = str(final.metadata.get("entry_tag") or final.metadata.get("setup") or "")
            if candidate.side == TradeSide.LONG:
                if not enter_long.iloc[pos]:
                    enter_long.iloc[pos] = True
                    enter_tag.iloc[pos] = tag
                    accepted.append((candidate, final))
            else:
                if not enter_short.iloc[pos]:
                    enter_short.iloc[pos] = True
                    enter_tag.iloc[pos] = tag
                    accepted.append((candidate, final))

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
    short_cd = int(
        getattr(strategy.short_cooldown_candles, "value", strategy.short_cooldown_candles)
    )
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

    # Correlate only surviving post-cooldown / post-conflict candidates so suppressed
    # bars never overwrite active signal IDs in CandidateRegistry.
    surviving_accepted = [
        (c, pol)
        for c, pol in result.accepted
        if 0 <= c.candle_index < n
        and (
            (c.side == TradeSide.LONG and bool(long_arr[c.candle_index]))
            or (c.side == TradeSide.SHORT and bool(short_arr[c.candle_index]))
        )
    ]
    _remember_accepted_candidates(strategy, surviving_accepted)

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
            "policy_score": float(pol.score),
            "quality_grade": pol.quality_grade,
            "signal_source": str((c.raw_features or {}).get("signal_source") or ""),
            "regime_1w": str((c.raw_features or {}).get("regime_1w") or ""),
            "is_bear_wave_1w": bool((c.raw_features or {}).get("is_bear_wave_1w", False)),
            "context_sensors": list((c.raw_features or {}).get("context_sensors") or []),
        }
        for c, pol in surviving_accepted
    ]
    return dataframe


def _remember_accepted_candidates(
    strategy: Any,
    accepted: Sequence[tuple[TradeCandidate, PolicyEvaluationResult]] | EntryPipelineResult,
) -> None:
    from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry

    reg = getattr(strategy, "_candidate_registry", None)
    if reg is None:
        reg = CandidateRegistry()
        try:
            strategy._candidate_registry = reg
        except Exception:
            return
    pairs = accepted.accepted if isinstance(accepted, EntryPipelineResult) else accepted
    for candidate, policy in pairs:
        tag = str(policy.metadata.get("entry_tag") or policy.metadata.get("setup") or "")
        reg.remember(candidate, tag)


def _summarize_live_entries(dataframe: DataFrame) -> list[dict]:
    """One summary row per positional enter_* signal (safe under duplicate index)."""
    rows: list[dict] = []
    long_pos = np.flatnonzero(dataframe["enter_long"].fillna(0).to_numpy(dtype=bool))
    short_pos = np.flatnonzero(dataframe["enter_short"].fillna(0).to_numpy(dtype=bool))
    has_date = "date" in dataframe.columns
    for pos in long_pos:
        p = int(pos)
        row = dataframe.iloc[p]
        rows.append(
            {
                "side": "long",
                "tag": str(row.get("enter_tag") or ""),
                "date": str(row["date"]) if has_date else str(dataframe.index[p]),
            }
        )
    for pos in short_pos:
        p = int(pos)
        row = dataframe.iloc[p]
        rows.append(
            {
                "side": "short",
                "tag": str(row.get("enter_tag") or ""),
                "date": str(row["date"]) if has_date else str(dataframe.index[p]),
            }
        )
    return rows
