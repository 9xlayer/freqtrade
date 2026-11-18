"""Explicit MTF Sniper runtime modes (deterministic phase vs AI-assisted).

Hunter remains out of scope for this phase. Deterministic mode:
- Forces Hunter OFF
- Forces Decision Gate to native/backtest heuristics only (no external AI/API)
- Preserves confirm_trade_entry + Risk Engine checks
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional


class RuntimeMode(str, Enum):
    """Authoritative runtime modes for StrategyMTFSniper."""

    DETERMINISTIC = "deterministic"
    DECISION_NATIVE = "decision_native"
    DECISION_EXTERNAL = "decision_external"


_ALIASES = {
    "deterministic": RuntimeMode.DETERMINISTIC,
    "det": RuntimeMode.DETERMINISTIC,
    "pure": RuntimeMode.DETERMINISTIC,
    "decision_native": RuntimeMode.DECISION_NATIVE,
    "native": RuntimeMode.DECISION_NATIVE,
    "decision": RuntimeMode.DECISION_NATIVE,
    "decision_external": RuntimeMode.DECISION_EXTERNAL,
    "external": RuntimeMode.DECISION_EXTERNAL,
    "ai": RuntimeMode.DECISION_EXTERNAL,
}


@dataclass(frozen=True)
class RuntimeProfile:
    mode: RuntimeMode
    enable_jev_hunter: bool
    enable_hunter_long: bool
    enable_jev_decision: bool
    decision_jev_mode: str  # native | external | backtest
    allow_external_ai: bool
    source: str


def resolve_runtime_mode(
    strat_cfg: Optional[Mapping[str, Any]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    default: RuntimeMode = RuntimeMode.DETERMINISTIC,
) -> RuntimeMode:
    """Resolve mode from strategy_params then env ``MTF_RUNTIME_MODE``."""
    env = environ if environ is not None else os.environ
    cfg = strat_cfg or {}
    raw = cfg.get("mtf_runtime_mode")
    if raw is None or str(raw).strip() == "":
        raw = env.get("MTF_RUNTIME_MODE", default.value)
    key = str(raw).strip().lower()
    return _ALIASES.get(key, default)


def build_runtime_profile(
    strat_cfg: Optional[Mapping[str, Any]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    enable_jev_decision_cfg: Optional[bool] = None,
    enable_jev_hunter_cfg: Optional[bool] = None,
    enable_hunter_long_cfg: Optional[bool] = None,
) -> RuntimeProfile:
    """
    Build the effective runtime profile.

    Deterministic mode never enables Hunter and never allows external Decision API.
    Decision Gate may still run native heuristics when ``enable_jev_decision`` is true.
    """
    env = environ if environ is not None else os.environ
    cfg = strat_cfg or {}
    mode = resolve_runtime_mode(cfg, environ=env)

    def _bool(val: Any, default: bool) -> bool:
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        return str(val).strip().lower() in ("true", "1", "yes", "on")

    # Config/env intent (may be overridden by mode)
    want_decision = (
        enable_jev_decision_cfg
        if enable_jev_decision_cfg is not None
        else _bool(
            cfg.get("enable_jev_decision", env.get("ENABLE_JEV_DECISION")),
            True,
        )
    )
    want_hunter = (
        enable_jev_hunter_cfg
        if enable_jev_hunter_cfg is not None
        else _bool(cfg.get("enable_jev_hunter", env.get("ENABLE_JEV_HUNTER")), False)
    )
    want_hunter_long = (
        enable_hunter_long_cfg
        if enable_hunter_long_cfg is not None
        else _bool(cfg.get("enable_hunter_long", env.get("ENABLE_HUNTER_LONG")), False)
    )

    if mode == RuntimeMode.DETERMINISTIC:
        return RuntimeProfile(
            mode=mode,
            enable_jev_hunter=False,
            enable_hunter_long=False,
            enable_jev_decision=want_decision,
            decision_jev_mode="native",
            allow_external_ai=False,
            source="mtf_runtime_mode=deterministic",
        )

    if mode == RuntimeMode.DECISION_NATIVE:
        return RuntimeProfile(
            mode=mode,
            enable_jev_hunter=False,  # hunter still out of scope this phase
            enable_hunter_long=False,
            enable_jev_decision=want_decision,
            decision_jev_mode="native",
            allow_external_ai=False,
            source="mtf_runtime_mode=decision_native",
        )

    # DECISION_EXTERNAL — still keep Hunter off unless explicitly re-enabled later
    return RuntimeProfile(
        mode=mode,
        enable_jev_hunter=bool(want_hunter),
        enable_hunter_long=bool(want_hunter and want_hunter_long),
        enable_jev_decision=want_decision,
        decision_jev_mode="external",
        allow_external_ai=True,
        source="mtf_runtime_mode=decision_external",
    )
