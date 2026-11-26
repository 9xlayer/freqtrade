"""Authoritative effective configuration + fingerprint for MTF Sniper."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from strategies.mtf_sniper import STRATEGY_VERSION
from strategies.mtf_sniper.core.runtime_mode import RuntimeProfile, build_runtime_profile


@dataclass(frozen=True)
class StrategySwitchConfig:
    """Resolved runtime switches & sizing overrides (strategy_params -> env -> default)."""

    block_macro_bull_shorts: bool = False
    enable_waterfall: bool = True
    enable_dynamic_sizing: bool = False
    jev_fail_policy: str = "allow"
    enable_telemetry: bool = True
    default_leverage: float = 10.0
    counter_1d_stake_scale: float | None = None
    counter_1w_stake_scale: float | None = None
    jev_1w_bear_penalty: float = 0.0
    tag_multipliers: dict[str, float] | None = None
    risk_per_trade_pct: float | None = None
    exchange_sync_interval_secs: float = 15.0


def parse_bool(val: object, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def parse_float(
    val: object,
    default: float,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    if val is None or val == "":
        return default
    try:
        parsed = float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def _parse_tag_multipliers(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, Mapping):
        return None
    out: dict[str, float] = {}
    for k, v in raw.items():
        mult = parse_float(v, -1.0)
        if mult > 0.0:
            out[str(k)] = mult
    return out or None


def _parse_optional_unit_float(
    cfg: Mapping[str, Any],
    env: Mapping[str, str],
    cfg_key: str,
    env_key: str,
    *,
    max_val: float = 1.0,
) -> float | None:
    raw = cfg.get(cfg_key)
    if raw is None:
        raw = env.get(env_key)
    if raw is None or str(raw).strip() == "":
        return None
    parsed = parse_float(raw, -1.0, min_value=0.0, max_value=max_val)
    return parsed if 0.0 < parsed <= max_val else None


def resolve_strategy_switches(
    strat_cfg: Mapping[str, Any] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> StrategySwitchConfig:
    """Resolve runtime switches with priority: strategy_params > env > built-in defaults."""
    cfg = strat_cfg or {}
    env = environ if environ is not None else os.environ

    def _bool_switch(cfg_key: str, env_key: str, default: bool) -> bool:
        if cfg_key in cfg:
            return parse_bool(cfg[cfg_key], default)
        if env_key in env:
            return parse_bool(env.get(env_key), default)
        return default

    raw_fail_policy = cfg.get("jev_fail_policy")
    if raw_fail_policy is None:
        raw_fail_policy = env.get("JEV_FAIL_POLICY", "allow")
    fail_policy = str(raw_fail_policy).strip().lower() or "allow"

    raw_lev = cfg.get("default_leverage")
    if raw_lev is None:
        raw_lev = env.get("DEFAULT_LEVERAGE")
    default_leverage = parse_float(raw_lev, 10.0, min_value=1.0, max_value=125.0)

    counter_scale = _parse_optional_unit_float(
        cfg, env, "counter_1d_stake_scale", "COUNTER_1D_STAKE_SCALE", max_val=1.0
    )
    counter_1w_scale = _parse_optional_unit_float(
        cfg, env, "counter_1w_stake_scale", "COUNTER_1W_STAKE_SCALE", max_val=1.0
    )
    jev_1w_penalty = (
        _parse_optional_unit_float(
            cfg, env, "jev_1w_bear_penalty", "JEV_1W_BEAR_PENALTY", max_val=1.0
        )
        or 0.0
    )

    raw_sync_secs = cfg.get("exchange_sync_interval_secs")
    if raw_sync_secs is None:
        raw_sync_secs = env.get("EXCHANGE_SYNC_INTERVAL_SECS")
    sync_secs = parse_float(raw_sync_secs, 15.0, min_value=0.0, max_value=3600.0)

    risk_pct = _parse_optional_unit_float(
        cfg, env, "risk_per_trade_pct", "RISK_PER_TRADE_PCT", max_val=0.99
    )

    return StrategySwitchConfig(
        block_macro_bull_shorts=_bool_switch(
            "block_macro_bull_shorts", "BLOCK_MACRO_BULL_SHORTS", False
        ),
        enable_waterfall=_bool_switch("enable_waterfall", "ENABLE_WATERFALL", True),
        enable_dynamic_sizing=_bool_switch(
            "enable_dynamic_sizing", "ENABLE_DYNAMIC_SIZING", False
        ),
        jev_fail_policy=fail_policy,
        enable_telemetry=_bool_switch("enable_telemetry", "ENABLE_TELEMETRY", True),
        default_leverage=default_leverage,
        counter_1d_stake_scale=counter_scale,
        counter_1w_stake_scale=counter_1w_scale,
        jev_1w_bear_penalty=jev_1w_penalty,
        tag_multipliers=_parse_tag_multipliers(cfg.get("tag_multipliers")),
        risk_per_trade_pct=risk_pct,
        exchange_sync_interval_secs=sync_secs,
    )


def merge_pair_overrides(
    base_pair_params: Mapping[str, Mapping[str, Any]] | None,
    strat_cfg: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Copy base pair overrides and merge optional ``strategy_params.pair_overrides``."""
    merged: dict[str, dict[str, Any]] = {
        pair: dict(params) for pair, params in (base_pair_params or {}).items()
    }
    raw_overrides = (strat_cfg or {}).get("pair_overrides")
    if not isinstance(raw_overrides, Mapping):
        return merged
    for pair, ov in raw_overrides.items():
        if isinstance(ov, Mapping):
            pair_key = str(pair)
            merged.setdefault(pair_key, {}).update(dict(ov))
    return merged


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
    long_params: dict[str, Any] = field(default_factory=dict)
    short_params: dict[str, Any] = field(default_factory=dict)
    pair_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = ""
    enable_waterfall: bool = True
    filter_params: dict[str, Any] = field(default_factory=dict)
    sizing_params: dict[str, Any] = field(default_factory=dict)
    exchange_sync_interval_secs: float = 15.0

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
    filter_params = {
        "block_4h_counter_wave": _pval("block_4h_counter_wave"),
        "block_1d_counter_wave": _pval("block_1d_counter_wave"),
        "min_adx_4h": _pval("min_adx_4h"),
        "block_high_vol": _pval("block_high_vol"),
        "high_vol_pct": _pval("high_vol_pct"),
        "filter_wick_rejection": _pval("filter_wick_rejection"),
        "max_rejection_wick_ratio": _pval("max_rejection_wick_ratio"),
        "min_close_pos_ratio_long": _pval("min_close_pos_ratio_long"),
        "max_close_pos_ratio_short": _pval("max_close_pos_ratio_short"),
        "filter_pdh_ceiling": _pval("filter_pdh_ceiling"),
    }
    counter_scale_override = getattr(strategy, "_counter_1d_scale_override", None)
    sizing_params = {
        "counter_1d_stake_scale": (
            counter_scale_override
            if counter_scale_override is not None
            else _pval("counter_1d_stake_scale")
        ),
        "counter_1w_stake_scale": _pval("counter_1w_stake_scale"),
        "jev_1w_bear_penalty": _pval("jev_1w_bear_penalty", 0.0),
        "default_leverage": _pval("default_leverage", 10.0),
        "tag_multipliers": _pval("_tag_multipliers"),
        "risk_per_trade_pct": _pval("risk_per_trade_pct"),
    }
    pair_overrides = {
        str(k): dict(v) for k, v in (getattr(strategy, "custom_pair_params", {}) or {}).items()
    }

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
        enable_waterfall=bool(getattr(strategy, "enable_waterfall", True)),
        filter_params=filter_params,
        sizing_params=sizing_params,
        exchange_sync_interval_secs=float(
            getattr(strategy, "exchange_sync_interval_secs", 15.0)
        ),
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
