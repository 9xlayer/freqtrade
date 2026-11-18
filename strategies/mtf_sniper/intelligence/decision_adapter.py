"""Decision Gate adapter — wraps in-package JevDecisionEngine."""
from __future__ import annotations

from typing import Any, Dict

from pandas import DataFrame

from strategies.mtf_sniper.intelligence.jev_decision import (
    CandleTrajectoryExtractor,
    JevDecisionEngine,
    JevStatus,
)


class JevDecisionAdapter:
    """Facade over ``intelligence.jev_decision``."""

    def __init__(self) -> None:
        self._extractor = CandleTrajectoryExtractor
        self._engine = JevDecisionEngine()
        self._status = JevStatus

    @property
    def status(self):
        return self._status

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
        return self._engine.evaluate(payload)
