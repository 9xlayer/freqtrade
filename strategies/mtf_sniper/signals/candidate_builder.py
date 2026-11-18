"""Sensor fusion: confluence merge, opposite-side neutralize, directional cooldown."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
from strategies.mtf_sniper.core.contracts import TradeCandidate, TriggerEvent
from strategies.mtf_sniper.core.enums import RejectionReasonCode, SensorType, TradeSide


# Pattern sensors outrank anomaly sensors when choosing primary_sensor.
_SENSOR_PRIORITY = {
    SensorType.PATTERN: 0,
    SensorType.ANOMALY: 1,
}


class CandidateBuilder:
    """
    Smart aggregator for raw TriggerEvents.

    Rules:
    1. Same-side triggers on the same (pair, candle_index) -> MERGE into one TradeCandidate
       with confluence_count and Pattern-preferred primary_sensor.
    2. Opposite-side on same bar:
       - Both sides complete sniper recipes -> NEUTRALIZE both.
       - Exactly one side complete -> keep that side (ignore opposite ambient parts).
       - Neither complete -> emit both sides separately (SetupComposer drops incomplete parts).
    3. Directional cooldown: minimum separation of `cooldown_bars` between consecutive
       candidates on the same (pair, side).
    """

    def __init__(
        self,
        cooldown_bars: int = 12,
        *,
        side_cooldowns: Optional[Dict[TradeSide, int]] = None,
    ) -> None:
        self.cooldown_bars = cooldown_bars
        # Optional per-side overrides (parity: long=18, short=96)
        self.side_cooldowns: Dict[TradeSide, int] = dict(side_cooldowns or {})
        # (pair, side) -> last accepted candle_index
        self._last_accepted: Dict[Tuple[str, TradeSide], int] = {}

    def cooldown_for(self, side: TradeSide) -> int:
        return int(self.side_cooldowns.get(side, self.cooldown_bars))

    def reset(self) -> None:
        self._last_accepted.clear()

    def process(
        self,
        events: Sequence[TriggerEvent],
        *,
        rejected: Optional[List[dict]] = None,
    ) -> List[TradeCandidate]:
        """
        Fuse a batch of TriggerEvents into TradeCandidates.

        Events are expected to cover one or more candles; processing is chronological
        by candle_index then pair. When `rejected` is provided, dropped groups are
        appended as audit dicts with reason codes.
        """
        if not events:
            return []

        grouped: Dict[Tuple[str, int], List[TriggerEvent]] = defaultdict(list)
        for event in events:
            grouped[(event.pair, event.candle_index)].append(event)

        # Chronological order across candles
        ordered_keys = sorted(grouped.keys(), key=lambda k: (k[1], k[0]))
        candidates: List[TradeCandidate] = []

        for pair, candle_index in ordered_keys:
            group = grouped[(pair, candle_index)]
            long_events = [e for e in group if e.side == TradeSide.LONG]
            short_events = [e for e in group if e.side == TradeSide.SHORT]

            side_groups = self._resolve_side_groups(
                long_events, short_events, pair, candle_index, rejected
            )
            for side_events in side_groups:
                candidate = self._merge_same_side(side_events)
                if candidate is None:
                    continue

                cooldown_key = (candidate.pair, candidate.side)
                cooldown_bars = self.cooldown_for(candidate.side)
                last_idx = self._last_accepted.get(cooldown_key)
                if last_idx is not None and (candidate.candle_index - last_idx) < cooldown_bars:
                    if rejected is not None:
                        rejected.append(
                            {
                                "pair": candidate.pair,
                                "candle_index": candidate.candle_index,
                                "side": candidate.side.value,
                                "reason": RejectionReasonCode.COOLDOWN_ACTIVE.value,
                                "last_accepted_index": last_idx,
                                "cooldown_bars": cooldown_bars,
                            }
                        )
                    continue

                self._last_accepted[cooldown_key] = candidate.candle_index
                candidates.append(candidate)

        return candidates

    def _resolve_side_groups(
        self,
        long_events: List[TriggerEvent],
        short_events: List[TriggerEvent],
        pair: str,
        candle_index: int,
        rejected: Optional[List[dict]],
    ) -> List[List[TriggerEvent]]:
        """Decide which side event lists become candidates on this bar."""
        if long_events and not short_events:
            return [long_events]
        if short_events and not long_events:
            return [short_events]
        if not long_events and not short_events:
            return []

        from strategies.mtf_sniper.directional.setup_recipes import recipes_by_side

        recipes = recipes_by_side()
        long_names = {e.sensor_name for e in long_events}
        short_names = {e.sensor_name for e in short_events}
        long_complete = recipes["long"].is_satisfied(long_names)
        short_complete = recipes["short"].is_satisfied(short_names)

        if long_complete and short_complete:
            if rejected is not None:
                rejected.append(
                    {
                        "pair": pair,
                        "candle_index": candle_index,
                        "reason": RejectionReasonCode.CONTRADICTORY_SIGNALS.value,
                        "sensors": sorted(long_names | short_names),
                    }
                )
            return []

        if short_complete and not long_complete:
            return [short_events]
        if long_complete and not short_complete:
            return [long_events]

        # Neither setup complete — keep both for composer / anomaly policy
        return [long_events, short_events]

    def process_stream(
        self,
        events: Iterable[TriggerEvent],
        *,
        rejected: Optional[List[dict]] = None,
    ) -> List[TradeCandidate]:
        """Alias for process — accepts any iterable of events."""
        return self.process(list(events), rejected=rejected)

    def _merge_same_side(self, events: Sequence[TriggerEvent]) -> Optional[TradeCandidate]:
        if not events:
            return None

        # Deduplicate by sensor_name while preserving first-seen order
        unique: Dict[str, TriggerEvent] = {}
        for event in events:
            if event.sensor_name not in unique:
                unique[event.sensor_name] = event
        ordered = list(unique.values())

        ranked = sorted(
            ordered,
            key=lambda e: (_SENSOR_PRIORITY.get(e.sensor_type, 99), e.sensor_name),
        )
        primary = ranked[0]
        secondary = [e.sensor_name for e in ranked[1:]]

        # Merge raw metrics (later sensors overwrite colliding keys)
        raw_features: Dict = {}
        for event in ordered:
            raw_features.update(event.raw_metrics)
        raw_features["sensors"] = [e.sensor_name for e in ranked]
        raw_features["sensor_types"] = {e.sensor_name: e.sensor_type.value for e in ranked}

        ts = normalize_utc(primary.timestamp)
        candidate_id = make_candidate_id(
            primary.pair, primary.side, ts, primary.candle_index
        )
        return TradeCandidate(
            candidate_id=candidate_id,
            pair=primary.pair,
            side=primary.side,
            primary_sensor=primary.sensor_name,
            secondary_sensors=secondary,
            confluence_count=len(ranked),
            timestamp=ts,
            candle_index=primary.candle_index,
            raw_features=raw_features,
            cooldown_bars=self.cooldown_for(primary.side),
        )
