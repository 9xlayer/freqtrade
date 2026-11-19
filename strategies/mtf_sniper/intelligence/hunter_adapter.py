"""Hunter adapter — wraps in-package JevHunterEngine / MomentumRadar (archive API)."""

from __future__ import annotations

from typing import Any

from pandas import DataFrame

from strategies.mtf_sniper.intelligence.jev_entry_hunter import (
    JevHunterEngine,
    JevSetupType,
    MomentumRadar,
)


class JevHunterAdapter:
    """
    Facade over ``intelligence.jev_entry_hunter``.

    Live use: soft-reject escalation via ``HunterEscalationGateway``.
    ``find_candidates`` (MomentumRadar scan) remains available for tools/tests.
    """

    def __init__(self) -> None:
        self._engine = JevHunterEngine()
        self._radar = MomentumRadar
        self._setup_type = JevSetupType

    @property
    def setup_type(self):
        return self._setup_type

    def find_candidates(
        self,
        dataframe: DataFrame,
        *,
        cooldown_bars: int = 12,
        directional_cooldown: bool = True,
    ) -> list[tuple[int, str]]:
        return self._radar.find_candidates(
            dataframe,
            cooldown_bars=cooldown_bars,
            directional_cooldown=directional_cooldown,
        )

    def evaluate_candidate(
        self,
        df_32: DataFrame,
        pair: str,
        ts_str: str,
        event_type: str,
        macro_info: dict[str, Any],
    ) -> dict[str, Any]:
        return self._engine.evaluate_candidate(df_32, pair, ts_str, event_type, macro_info)
