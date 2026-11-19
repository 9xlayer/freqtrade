"""
Post-AI Hunter acceptance — legacy active StrategyMTFSniper thresholds.

Called only after JEV Hunter returns a successful directional verdict.
Does not call AI; fail-closed on missing structure / unsafe regime.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.directional.common_gates import (
    DEFAULT_MARKET_SAFETY,
    MarketSafetyConfig,
    is_long_hunter_macro_ok,
    is_pdh_safe_long_row,
    is_short_hunter_macro_ok,
    is_sideway_safe_row,
    is_volatility_safe_row,
)
from strategies.mtf_sniper.directional.long.hunter_gates import (
    DEFAULT_LONG_HUNTER_GATES,
    LongHunterGateConfig,
    evaluate_long_hunter_breakout,
)
from strategies.mtf_sniper.directional.short.hunter_gates import (
    DEFAULT_SHORT_HUNTER_GATES,
    ShortHunterGateConfig,
    evaluate_short_hunter_waterfall,
)


# Setup types the legacy strategy actually wrote to enter_tag.
_SHORT_SETUP = "waterfall_collapse"
_LONG_SETUP = "breakout_expansion"


def accept_hunter_verdict(
    *,
    side: TradeSide,
    setup: str,
    confidence: float,
    row: Mapping[str, Any],
    market: MarketSafetyConfig | None = None,
    short_gates: ShortHunterGateConfig | None = None,
    long_gates: LongHunterGateConfig | None = None,
) -> tuple[bool, list[str]]:
    """
    Return (accepted, rejection_reasons).

    Short: waterfall_collapse + bear macro + ShortHunterGateConfig (conf≥0.85, vol/chop…).
    Long:  breakout_expansion + bull macro + LongHunterGateConfig (conf≥0.90, …).
    """
    market_cfg = market or DEFAULT_MARKET_SAFETY
    setup_l = (setup or "").lower().strip()

    vol_safe = is_volatility_safe_row(row, market_cfg)
    sideway_safe = is_sideway_safe_row(row, market_cfg)

    if side == TradeSide.SHORT:
        if setup_l != _SHORT_SETUP:
            return False, ["SETUP_NOT_WATERFALL"]
        if not is_short_hunter_macro_ok(row):
            return False, ["MACRO_NOT_BEAR"]
        ok, gate_reasons = evaluate_short_hunter_waterfall(
            row,
            confidence=confidence,
            vol_safe=vol_safe,
            sideway_safe=sideway_safe,
            config=short_gates or DEFAULT_SHORT_HUNTER_GATES,
        )
        return ok, gate_reasons

    if side == TradeSide.LONG:
        if setup_l != _LONG_SETUP:
            return False, ["SETUP_NOT_BREAKOUT"]
        if not is_long_hunter_macro_ok(row):
            return False, ["MACRO_NOT_BULL"]
        ok, gate_reasons = evaluate_long_hunter_breakout(
            row,
            confidence=confidence,
            pdh_safe=is_pdh_safe_long_row(row),
            vol_safe=vol_safe,
            sideway_safe=sideway_safe,
            config=long_gates or DEFAULT_LONG_HUNTER_GATES,
        )
        return ok, gate_reasons

    return False, ["UNKNOWN_SIDE"]
