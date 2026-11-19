"""Authoritative effective configuration + fingerprint for MTF Sniper."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from strategies.mtf_sniper import STRATEGY_VERSION
from strategies.mtf_sniper.core.runtime_mode import RuntimeProfile, build_runtime_profile


@dataclass(frozen=True)
class EffectiveConfig:
    """Single resolved view of knobs that must not silently diverge."""

    strategy_version: str
    runtime_mode: str
    enable_jev_decision: bool
    enable_jev_hunter: bool
    enable_hunter_long: bool
    decision_jev_mode: str
    allow_external_ai: bool
    block_macro_bull_shorts: bool
    enable_dynamic_sizing: bool
    jev_fail_policy: str
    # Long effective (strategy Parameter defaults after JSON load)
    long_params: dict[str, Any] = field(default_factory=dict)
    short_params: dict[str, Any] = field(default_factory=dict)
    pair_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = ""

    def fingerprint(self) -> str:
        payload = asdict(self)
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_log_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fingerprint"] = self.fingerprint()
        return d


def build_effective_config(
    strategy: Any,
    *,
    profile: RuntimeProfile | None = None,
) -> EffectiveConfig:
    """Snap strategy Parameter values + runtime profile into one object."""
    strat_cfg = {}
    if hasattr(strategy, "config") and strategy.config:
        strat_cfg = strategy.config.get("strategy_params", {}) or {}

    profile = profile or build_runtime_profile(strat_cfg)

    def _pval(name: str, default: Any = None) -> Any:
        param = getattr(strategy, name, None)
        if param is None:
            return default
        return getattr(param, "value", param)

    long_params = {
        "long_loc_tolerance_1h": _pval("long_loc_tolerance_1h"),
        "long_max_1h_rsi_exhaustion": _pval("long_max_1h_rsi_exhaustion"),
        "long_lookback_resistance": _pval("long_lookback_resistance"),
        "long_candle_body_ratio_min": _pval("long_candle_body_ratio_min"),
        "long_max_dist_ema21": _pval("long_max_dist_ema21"),
        "long_max_rsi_entry": _pval("long_max_rsi_entry"),
        "long_cooldown_candles": _pval("long_cooldown_candles"),
        "long_overextended_dist_max": _pval("long_overextended_dist_max"),
        "long_block_1d_macd": _pval("long_block_1d_macd"),
    }
    short_params = {
        "short_loc_tolerance_1h": _pval("short_loc_tolerance_1h"),
        "short_min_1h_rsi_exhaustion": _pval("short_min_1h_rsi_exhaustion"),
        "short_lookback_support": _pval("short_lookback_support"),
        "short_candle_body_ratio_min": _pval("short_candle_body_ratio_min"),
        "short_max_dist_ema21": _pval("short_max_dist_ema21"),
        "short_min_rsi_entry": _pval("short_min_rsi_entry"),
        "short_cooldown_candles": _pval("short_cooldown_candles"),
    }
    pair_overrides = dict(getattr(strategy, "custom_pair_params", {}) or {})

    return EffectiveConfig(
        strategy_version=str(getattr(strategy, "strategy_version", STRATEGY_VERSION)),
        runtime_mode=profile.mode.value,
        enable_jev_decision=profile.enable_jev_decision,
        enable_jev_hunter=profile.enable_jev_hunter,
        enable_hunter_long=profile.enable_hunter_long,
        decision_jev_mode=profile.decision_jev_mode,
        allow_external_ai=profile.allow_external_ai,
        block_macro_bull_shorts=bool(getattr(strategy, "block_macro_bull_shorts", False)),
        enable_dynamic_sizing=bool(getattr(strategy, "enable_dynamic_sizing", False)),
        jev_fail_policy=str(getattr(strategy, "jev_fail_policy", "allow")),
        long_params=long_params,
        short_params=short_params,
        pair_overrides=pair_overrides,
        source=profile.source,
    )


def warn_pair_override_divergences(effective: EffectiveConfig) -> list[str]:
    """Return warnings when pair overrides mask strategy Parameter values."""
    warnings: list[str] = []
    for pair, ov in (effective.pair_overrides or {}).items():
        for key in ("long_max_dist_ema21", "long_candle_body_ratio_min"):
            if (
                key in ov
                and key in effective.long_params
                and ov[key] != effective.long_params[key]
            ):
                warnings.append(
                    f"{pair}: pair override {key}={ov[key]} masks "
                    f"strategy Parameter {effective.long_params[key]}"
                )
        for key in ("short_max_dist_ema21", "short_candle_body_ratio_min"):
            if (
                key in ov
                and key in effective.short_params
                and ov[key] != effective.short_params[key]
            ):
                warnings.append(
                    f"{pair}: pair override {key}={ov[key]} masks "
                    f"strategy Parameter {effective.short_params[key]}"
                )
    return warnings
