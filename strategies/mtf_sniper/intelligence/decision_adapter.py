"""Decision Gate adapter — wraps in-package JevDecisionEngine."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from pandas import DataFrame

from strategies.mtf_sniper.intelligence.jev_decision import (
    CandleTrajectoryExtractor,
    JevDecisionEngine,
    JevStatus,
)

logger = logging.getLogger(__name__)


class JevDecisionAdapter:
    """Facade over ``intelligence.jev_decision``.

    When ``force_native=True`` (deterministic runtime), external AI/API is
    refused even if process env says ``JEV_MODE=external``.
    """

    def __init__(self, *, force_native: bool = False, mode: Optional[str] = None) -> None:
        self._extractor = CandleTrajectoryExtractor
        self._force_native = bool(force_native)
        resolved_mode = mode
        if self._force_native:
            resolved_mode = "native"
            # Prevent engine from reading a stale external mode from env.
            os.environ["JEV_MODE"] = "native"
        self._engine = JevDecisionEngine(mode=resolved_mode)
        if self._force_native and getattr(self._engine, "mode", "") == "external":
            self._engine.mode = "native"
        self._status = JevStatus
        self.external_calls = 0  # spy counter for deterministic-mode tests

    @property
    def status(self):
        return self._status

    @property
    def mode(self) -> str:
        return str(getattr(self._engine, "mode", "native"))

    def build_payload(
        self,
        dataframe: DataFrame,
        pair: str,
        side: str,
        *,
        lookback: int = 32,
    ) -> Dict[str, Any]:
        return self._extractor.build_payload(dataframe, pair, side, lookback=lookback)

    def evaluate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._force_native and self.mode == "external":
            logger.error(
                "[JEV] Deterministic runtime blocked external Decision mode; "
                "forcing native heuristics."
            )
            self._engine.mode = "native"
        # Spy: count only when engine would take external branch
        if getattr(self._engine, "mode", "") == "external":
            self.external_calls += 1
        return self._engine.evaluate(payload)
