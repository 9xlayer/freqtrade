"""
Jev Decision Engine & Candle Trajectory Extractor
================================================
Provides AI Decision Layer bridge for Freqtrade & AI Agent Lens:
1. CandleTrajectoryExtractor: Packages 32 real-time candles into
   normalized Price Action Trajectory vectors with MTF context.
2. JevDecisionEngine: System-1 decision model supporting:
   - Native Quant System-1: High-speed offline price trajectory analyzer (<1ms).
   - External Mode: Sends HTTP JSON payload to Jev API / LLM endpoint (when explicitly configured).
   - Backtest Mode: Ensures full determinism without network API calls in backtesting.
"""

import hashlib
import json
import logging
import math
import os
import sqlite3
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd
from pandas import DataFrame

# Version hub: strategies.mtf_sniper.__init__ (single bump site).
from strategies.mtf_sniper import (
    DECISION_ENGINE_VERSION as ENGINE_VERSION,
)
from strategies.mtf_sniper import (
    DECISION_MODEL_VERSION_EXTERNAL_DEFAULT,
)
from strategies.mtf_sniper import (
    DECISION_MODEL_VERSION_NATIVE as MODEL_VERSION_NATIVE,
)
from strategies.mtf_sniper import (
    DECISION_PROMPT_VERSION_EXTERNAL as PROMPT_VERSION_EXTERNAL,
)
from strategies.mtf_sniper import (
    DECISION_PROMPT_VERSION_NATIVE as PROMPT_VERSION_NATIVE,
)
from strategies.mtf_sniper.directional.short.policy import (
    DEFAULT_ETH_WATERFALL_FILTER,
    DEFAULT_WATERFALL_FILTER,
    is_vacuum_breakdown,
)
from strategies.mtf_sniper.directional.short.setups import ShortSetup


logger = logging.getLogger(__name__)


MODEL_VERSION_EXTERNAL = os.environ.get(
    "JEV_DECISION_MODEL_VERSION", DECISION_MODEL_VERSION_EXTERNAL_DEFAULT
)


def _external_confirm_instructions(side: str) -> str:
    """Side-aware System-1 confirm prompt (optimal richctx)."""
    side_u = (side or "").upper()
    if side_u == "SHORT":
        return (
            "MTF Sniper SHORT candidate (waterfall / downside impulse). "
            "Score how well this sequence supports a high-probability SHORT continuation: "
            "impulsive downside body, expanding sell pressure / volume, closes accepting lower, "
            "and failed or weak opposing absorption on the upside. "
            "Do NOT require a multi-day bear trend or clean HTF downtrend — sniper shorts often "
            "fire as collapse / breakdown bursts inside broader two-sided markets. "
            "Score low when candles are truly two-sided chop with no downside close bias, "
            "or when strong bullish rejection clearly invalidates the short trigger. "
            "Use the metric legend, close_pos/vol on the trigger bar, and macro context as evidence."
        )
    if side_u == "LONG":
        return (
            "MTF Sniper LONG candidate (breakout / upside expansion). "
            "Score how well this sequence supports a high-probability LONG continuation: "
            "impulsive upside body, expanding buy pressure / volume, closes accepting higher, "
            "and failed or weak opposing supply on the downside. "
            "Prefer breakout / expansion over mean-reversion bounces. "
            "Score low when candles are truly two-sided chop with no upside close bias, "
            "or when strong bearish rejection clearly invalidates the long trigger. "
            "Use the metric legend, close_pos/vol on the trigger bar, and macro context as evidence."
        )
    return (
        f"Score whether this sequence confirms a high-probability {side_u or 'directional'} "
        "continuation with expanding pressure and without decisive opposing rejection. "
        "Score low for genuine two-sided chop or clear invalidation of the trigger."
    )


def _external_regime_criteria() -> dict[str, str]:
    """Regime labels used by external Decision (hard-veto still applies to choppy)."""
    return {
        "trend_expansion": (
            "Directional impulse — upside breakout/expansion OR downside waterfall/collapse "
            "with volume confirmation and accepting closes"
        ),
        "pullback_setup": (
            "Orderly retracement toward dynamic support/resistance with declining opposing volume"
        ),
        "choppy_ranging": (
            "True two-sided overlap with no directional close bias — not a single wick on an "
            "otherwise impulsive sniper candle"
        ),
    }


def _format_external_state(
    *,
    pair: str,
    side: str,
    current_price: float,
    seq: list[dict[str, Any]],
    macro: dict[str, Any],
    candle_limit: int = 12,
) -> str:
    """Build Typesafe ``state`` text with metric legend + richer trajectory/macro."""
    side_u = (side or "").upper()
    setup_role = (
        "SHORT sniper (waterfall / downside impulse)"
        if side_u == "SHORT"
        else (
            "LONG sniper (breakout / upside expansion)"
            if side_u == "LONG"
            else f"{side_u or 'directional'} candidate"
        )
    )
    lens = macro.get(
        "short_score" if side_u == "SHORT" else "long_score",
        50.0,
    )
    ema_dist = macro.get("ema_50_1h_dist_pct", macro.get("ema50_1h_dist_pct", 0.0))

    legend = (
        "Metric legend (read before scoring):\n"
        "- body: |close-open|/range in [0,1]; >=0.55 with vol>=1.2x often impulsive\n"
        "- lw/uw: lower/upper wick share of range in [0,1]; adverse wick fights the trade side\n"
        "- close_pos: close location in range [0,1]; SHORT prefers <=0.40, LONG >=0.60\n"
        "- range_pct: (high-low)/close * 100; pct: open->close %\n"
        "- vol: volume / 20-bar SMA; rsi: 15m RSI\n"
        "- Labels: trigger=last closed bar; T-n=n bars before trigger"
    )

    window = seq[-candle_limit:] if seq else []
    lines: list[str] = []
    n = len(window)
    for i, c in enumerate(window):
        label = "trigger" if i == n - 1 else f"T-{n - 1 - i}"
        lines.append(
            f"[{label}] body:{float(c.get('body_ratio', 0)):.2f} "
            f"lw:{float(c.get('lower_wick', 0)):.2f} uw:{float(c.get('upper_wick', 0)):.2f} "
            f"close_pos:{float(c.get('close_pos', 0.5)):.2f} "
            f"range_pct:{float(c.get('range_pct', 0)):.2f} pct:{float(c.get('pct_change', 0)):.2f} "
            f"vol:{float(c.get('vol_ratio', c.get('volume_ratio', 0))):.2f}x "
            f"rsi:{float(c.get('rsi', 50)):.1f}"
        )
    candles_str = "\n".join(lines) if lines else "(no candle trajectory)"

    return f"""Asset: {pair} (Futures 10x)
Strategy role: MTF Sniper confirm gate — {setup_role}
Proposed Trade: {side_u}
Current Price: {current_price}
Timeframe trigger: 15m | Informative: 1H / 4H / 1D

{legend}

Recent Candle Trajectory (last {len(window)} of {len(seq)} bars):
{candles_str}

Macro & Filter Context:
- 4H Trend: {macro.get("trend_4h", "NEUTRAL")} (context only; do not veto solely for HTF disagreement)
- 4H ADX: {float(macro.get("adx_4h", 20.0)):.1f}
- 1H EMA50 Distance: {float(ema_dist):.2f}%
- 1H MACD hist: {float(macro.get("macdhist_1h", 0.0)):.4f}
- 1H RSI: {float(macro.get("rsi_1h", 50.0)):.1f}
- 1D floor(20d) distance: {float(macro.get("floor_20d_dist_pct", 0.0)):.2f}%
- ATR Volatility Percentile: {float(macro.get("atr_percentile", 50.0)):.1f}%
- AI Lens Confluence Score ({"short" if side_u == "SHORT" else "long"}): {float(lens):.1f}
"""


def _parse_unit_interval(value: Any, default: float | None = None) -> float | None:
    """Parse a finite float in [0, 1]. Returns default/None on invalid input."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or math.isinf(parsed) or parsed < 0.0 or parsed > 1.0:
        return default
    return parsed


def _external_weight_ai() -> float:
    """Weight for AI noul probability in hybrid score (default 0.10)."""
    raw = os.environ.get("JEV_DECISION_WEIGHT_AI", "0.10")
    val = _parse_unit_interval(raw, default=0.10)
    return 0.10 if val is None else val


def _external_choppy_penalty() -> float:
    """Penalty for choppy/ranging market regime in hybrid score (default 0.10)."""
    raw = os.environ.get("JEV_DECISION_CHOPPY_PENALTY", "0.10")
    val = _parse_unit_interval(raw, default=0.10)
    return 0.10 if val is None else val


def _external_confirm_threshold() -> float | None:
    """
    Confirm threshold for Decision Gate.
    Must be finite and inside [0, 1]; invalid env values yield None (fail closed).
    Defaults to 0.70 for hybrid evaluation.
    """
    raw = os.environ.get("JEV_CONFIRM_THRESHOLD", "0.70")
    return _parse_unit_interval(raw, default=None)


EXTERNAL_CONFIRM_THRESHOLD = _external_confirm_threshold()


def build_decision_cache_key(
    payload_hash: str,
    *,
    model_version: str,
    engine_version: str,
    prompt_version: str,
    confirm_threshold: str,
    fail_policy: str,
    mode: str,
) -> str:
    """Deterministic cache identity across payload + model/prompt/threshold/policy/mode."""
    identity = {
        "payload_hash": payload_hash or "",
        "model_version": model_version or "",
        "engine_version": engine_version or "",
        "prompt_version": prompt_version or "",
        "confirm_threshold": confirm_threshold,
        "fail_policy": fail_policy or "",
        "mode": mode or "",
    }
    blob = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class JevDecision(str, Enum):
    CONFIRM = "CONFIRM"
    VETO = "VETO"


class JevSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class JevStatus(str, Enum):
    SUCCESS = "SUCCESS"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


class JevMarketRegime(str, Enum):
    TREND_EXPANSION = "TREND_EXPANSION"
    PULLBACK_SETUP = "PULLBACK_SETUP"
    CHOPPY_RANGING = "CHOPPY_RANGING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNKNOWN = "UNKNOWN"


def make_verdict(
    decision: str | JevDecision,
    side: str | JevSide = JevSide.NONE,
    confidence: float = 0.0,
    market_regime: str | JevMarketRegime = JevMarketRegime.UNKNOWN,
    reasons: list[str] | None = None,
    source: str = "native",
    model_version: str = MODEL_VERSION_NATIVE,
    engine_version: str = ENGINE_VERSION,
    status: str | JevStatus = JevStatus.SUCCESS,
    raw_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Ensures 100% verdict compliance with Universal Decision Contract Schema:
    {
        "decision": "CONFIRM" | "VETO",
        "side": "LONG" | "SHORT" | "NONE",
        "confidence": float [0.0, 1.0],
        "market_regime": str,
        "reasons": List[str],
        "source": str,
        "model_version": str,
        "engine_version": str,
        "status": "SUCCESS" | "UNAVAILABLE" | "INVALID",
        "raw_response": dict
    }
    """
    try:
        conf_float = 0.0 if (math.isnan(confidence) or math.isinf(confidence)) else confidence
    except (TypeError, ValueError):
        conf_float = 0.0
    conf_bounded = max(0.0, min(1.0, round(conf_float, 2)))

    stat_str = str(getattr(status, "value", status)).upper()
    if stat_str not in ("SUCCESS", "UNAVAILABLE", "INVALID"):
        stat_str = JevStatus.INVALID.value

    dec_str = str(getattr(decision, "value", decision)).upper()
    # Confirm only if status is SUCCESS and action is CONFIRM
    final_decision = (
        JevDecision.CONFIRM.value
        if (dec_str == "CONFIRM" and stat_str == "SUCCESS")
        else JevDecision.VETO.value
    )

    side_str = str(getattr(side, "value", side)).upper()
    final_side = side_str if side_str in ("LONG", "SHORT") else JevSide.NONE.value

    regime_str = str(getattr(market_regime, "value", market_regime)).upper()

    return {
        "decision": final_decision,
        "side": final_side,
        "confidence": conf_bounded,
        "market_regime": regime_str,
        "reasons": list(reasons or []),
        "source": source,
        "model_version": model_version,
        "engine_version": engine_version,
        "status": stat_str,
        "raw_response": dict(raw_response or {}),
    }


class CandleTrajectoryExtractor:
    """
    Extracts real-time candle sequence and market context into JSON Snapshot
    for AI decision model.
    Ensures numerical safety: zero-division protection, NaN/Inf checks, column validation.
    """

    REQUIRED_COLS = ["open", "high", "low", "close", "volume"]

    @classmethod
    def extract_sequence(cls, df: DataFrame, lookback: int = 32) -> list[dict[str, float]]:
        """
        Extracts recent N candles into momentum feature sequence.
        Candle index runs from -(lookback-1) to 0 (trigger candle).
        """
        if df is None or not isinstance(df, DataFrame) or df.empty:
            return []

        missing = [c for c in cls.REQUIRED_COLS if c not in df.columns]
        if missing:
            logger.warning(f"CandleTrajectoryExtractor: Missing required columns: {missing}")
            return []

        if len(df) < lookback:
            recent = df.copy()
        else:
            recent = df.iloc[-lookback:].copy()

        # Clean NaN/Inf via ffill and bfill
        recent = recent.ffill().bfill()

        # Normalize volume via rolling 20 SMA or recent mean
        vol_col = recent["volume"].fillna(0.0)
        vol_mean = (
            float(vol_col.rolling(20, min_periods=1).mean().iloc[-1]) if len(vol_col) > 0 else 1.0
        )
        if vol_mean <= 0.0 or math.isnan(vol_mean):
            vol_mean = 1.0

        sequence = []
        n_candles = len(recent)

        for i, (_, row) in enumerate(recent.iterrows()):
            try:
                c = float(row["close"])
                o = float(row["open"])
                h = float(row["high"])
                low_val = float(row["low"])
                v = float(row["volume"])
            except (ValueError, TypeError):
                continue

            if math.isnan(c) or math.isnan(o) or math.isnan(h) or math.isnan(low_val):
                continue

            # Anti-division by zero cho total_range
            total_range = max(h - low_val, 1e-6)
            body = abs(c - o)

            pct_change = round(((c - o) / o) * 100, 3) if o > 0 else 0.0
            range_pct = round(((h - low_val) / c) * 100, 3) if c > 0 else 0.0
            body_ratio = round(body / total_range, 3)
            upper_wick = round((h - max(o, c)) / total_range, 3)
            lower_wick = round((min(o, c) - low_val) / total_range, 3)
            close_pos = round((c - low_val) / total_range, 3)
            vol_ratio = round(v / vol_mean, 2) if vol_mean > 0 else 1.0

            rsi_val = row.get("rsi", 50.0)
            try:
                rsi = round(float(rsi_val), 1)
                if math.isnan(rsi):
                    rsi = 50.0
            except (ValueError, TypeError):
                rsi = 50.0

            # Index runs from -(n_candles - 1) to 0
            candle_idx = i - n_candles + 1

            sequence.append(
                {
                    "idx": candle_idx,
                    "index": candle_idx,
                    "pct_change": pct_change,
                    "range_pct": range_pct,
                    "body_ratio": body_ratio,
                    "upper_wick": upper_wick,
                    "lower_wick": lower_wick,
                    "close_pos": close_pos,
                    "vol_ratio": vol_ratio,
                    "volume_ratio": vol_ratio,
                    "rsi": rsi,
                }
            )
        return sequence

    @classmethod
    def extract_macro_context(cls, df: DataFrame) -> dict[str, Any]:
        """
        Extracts MTF context from the latest row.
        Unifies key format for External API and Native Quant.
        """
        if df is None or not isinstance(df, DataFrame) or df.empty:
            return {}

        last = df.iloc[-1]
        try:
            c = float(last["close"])
        except (ValueError, TypeError):
            c = 0.0

        # 1H context
        ema_50_1h = float(last.get("ema_50_1h", c)) if not pd.isna(last.get("ema_50_1h")) else c
        ema_50_1h_dist = round(((c - ema_50_1h) / ema_50_1h) * 100, 2) if ema_50_1h > 0 else 0.0
        macd_1h = (
            float(last.get("macdhist_1h", 0.0)) if not pd.isna(last.get("macdhist_1h")) else 0.0
        )
        rsi_1h = float(last.get("rsi_1h", 50.0)) if not pd.isna(last.get("rsi_1h")) else 50.0

        # 4H context
        is_bull_4h = last.get("is_bull_wave_4h", 0) == 1
        is_bear_4h = last.get("is_bear_wave_4h", 0) == 1
        adx_4h = float(last.get("adx_4h", 20.0)) if not pd.isna(last.get("adx_4h")) else 20.0

        # 1D context
        raw_floor = last.get("swing_low_20d_1d")
        floor_valid = False
        floor_20d = c
        if raw_floor is not None and not pd.isna(raw_floor):
            parsed_floor = float(raw_floor)
            if math.isfinite(parsed_floor) and parsed_floor > 0.0:
                floor_20d = parsed_floor
                floor_valid = True
        floor_dist = round(((c - floor_20d) / floor_20d) * 100, 2) if floor_20d > 0 else 0.0

        # AI Agent Lens scores
        long_score = (
            float(last.get("long_score", 50.0)) if not pd.isna(last.get("long_score")) else 50.0
        )
        short_score = (
            float(last.get("short_score", 50.0)) if not pd.isna(last.get("short_score")) else 50.0
        )
        atr_pct = float(last.get("atr_pct", 50.0)) if not pd.isna(last.get("atr_pct")) else 50.0
        rel_vol = (
            float(last.get("rel_vol", 1.0)) if not pd.isna(last.get("rel_vol")) else 1.0
        )

        # 1W macro regime context (from MarketRegimeSensor / @informative("1w"))
        raw_regime_1w = last.get("market_regime_1w")
        regime_1w = (
            raw_regime_1w
            if isinstance(raw_regime_1w, str) and raw_regime_1w
            else "unknown"
        )
        macdhist_1w = (
            float(last.get("macdhist_1w", 0.0)) if not pd.isna(last.get("macdhist_1w")) else 0.0
        )
        adx_1w = float(last.get("adx_1w", 0.0)) if not pd.isna(last.get("adx_1w")) else 0.0
        rsi_1w = float(last.get("rsi_1w", 50.0)) if not pd.isna(last.get("rsi_1w")) else 50.0
        is_bear_wave_1w = bool(last.get("is_bear_wave_1w", False))

        return {
            "current_price": c,
            "ema_50_1h_dist_pct": ema_50_1h_dist,
            "macdhist_1h": macd_1h,
            "rsi_1h": rsi_1h,
            "trend_4h": "BULL" if is_bull_4h else ("BEAR" if is_bear_4h else "NEUTRAL"),
            "adx_4h": adx_4h,
            "floor_20d_dist_pct": floor_dist,
            "floor_20d_valid": floor_valid,
            "long_score": long_score,
            "short_score": short_score,
            "atr_percentile": atr_pct,
            "rel_vol": rel_vol,
            "regime_1w": regime_1w,
            "macdhist_1w": macdhist_1w,
            "adx_1w": adx_1w,
            "rsi_1w": rsi_1w,
            "is_bear_wave_1w": is_bear_wave_1w,
        }

    @classmethod
    def build_payload(
        cls,
        df: DataFrame,
        pair: str,
        side: str,
        lookback: int = 32,
        setup: str | None = None,
    ) -> dict[str, Any]:
        """
        Packages payload for Jev Decision Engine.
        Ensures complete payload structure:
        - current_price
        - timeframe ("15m")
        - normalized timestamp
        - side ("LONG" / "SHORT")
        - pair
        - setup
        - trajectory_sequence
        - macro_context
        - payload_hash (cho version-aware cache)
        """
        if df is None or not isinstance(df, DataFrame) or df.empty:
            return {
                "pair": pair,
                "timestamp": "",
                "side": side.upper(),
                "timeframe": "15m",
                "current_price": 0.0,
                "setup": setup or "",
                "macro_context": {},
                "trajectory_sequence": [],
                "payload_hash": "",
            }

        last_row = df.iloc[-1]
        ts = str(last_row.get("date", ""))
        current_price = float(last_row.get("close", 0.0))
        macro_ctx = cls.extract_macro_context(df)
        traj_seq = cls.extract_sequence(df, lookback=lookback)

        # Compute deterministic payload_hash for caching
        traj_repr = json.dumps(traj_seq, sort_keys=True, default=str)
        macro_repr = json.dumps(macro_ctx, sort_keys=True, default=str)
        traj_hash = hashlib.sha256(traj_repr.encode("utf-8")).hexdigest()[:12]
        macro_hash = hashlib.sha256(macro_repr.encode("utf-8")).hexdigest()[:12]
        setup_tok = (setup or "").lower().strip()
        hash_seed = (
            f"{pair}_{ts}_15m_{side.upper()}_{setup_tok}_{current_price:.4f}_"
            f"{traj_hash}_{macro_hash}_{ENGINE_VERSION}"
        )
        payload_hash = hashlib.sha256(hash_seed.encode("utf-8")).hexdigest()[:16]

        return {
            "pair": pair,
            "timestamp": ts,
            "side": side.upper(),
            "timeframe": "15m",
            "current_price": current_price,
            "setup": setup or "",
            "macro_context": macro_ctx,
            "trajectory_sequence": traj_seq,
            "payload_hash": payload_hash,
        }


class JevDecisionEngine:
    """
    AI System-1 Decision Engine:
    - Explicit mode via JEV_MODE (native | external | backtest).
    - Default: 'native' (zero HTTP requests, 100% local <1ms).
    - External Mode: Calls API only when JEV_MODE='external' and JEV_API_KEY is set.
    - SQLite caching with multiprocess safety (WAL mode, busy_timeout=5000).
    - Fully deterministic in backtesting.
    """

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        cache_db: str = "user_data/jev_hunter_cache.sqlite",
        mode: str | None = None,
        fail_policy: str | None = None,
        regime_1w_bear_penalty: float | None = None,
    ):
        self.cache_db = cache_db
        # Parse .env into a local dict — never mutate process os.environ.
        dotenv_cfg = self._read_dotenv()
        self._init_cache()

        def _cfg(key: str, default: str = "") -> str:
            val = os.environ.get(key, "").strip()
            if key in os.environ and val != "":
                return os.environ.get(key, default)
            return str(dotenv_cfg.get(key, default))

        # Mode: explicit ctor arg wins; else env / dotenv; default native.
        configured_mode = (mode or _cfg("JEV_MODE", "native")).lower()
        self.mode = (
            configured_mode if configured_mode in ("native", "external", "backtest") else "native"
        )

        self.fail_policy = (fail_policy or _cfg("JEV_FAIL_POLICY", "reject")).lower()
        self.api_url = api_url or _cfg("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
        self.api_key = api_key or _cfg("JEV_API_KEY", "") or None
        raw_1w_pen = (
            regime_1w_bear_penalty
            if regime_1w_bear_penalty is not None
            else _parse_unit_interval(_cfg("JEV_1W_BEAR_PENALTY", "0.0"), default=0.0)
        )
        self.regime_1w_bear_penalty = (
            max(0.0, min(1.0, float(raw_1w_pen))) if raw_1w_pen is not None else 0.0
        )
        self.engine_version = ENGINE_VERSION
        self.external_model_version = _cfg("JEV_DECISION_MODEL_VERSION", MODEL_VERSION_EXTERNAL)
        self.prompt_version = (
            PROMPT_VERSION_EXTERNAL if self.mode == "external" else PROMPT_VERSION_NATIVE
        )

        logger.info(
            f"JevDecisionEngine initialized. Mode={self.mode}, FailPolicy={self.fail_policy}, "
            f"EngineVer={ENGINE_VERSION}, PromptVer={self.prompt_version}"
        )

    @staticmethod
    def _read_dotenv() -> dict:
        """
        Load key=value pairs from .env files without writing os.environ.

        Priority (first wins per key; process env still overrides in ``_cfg``):
        1. ``./.env`` (cwd)
        2. package-relative ``strategies/.env`` (parents[2] from this file)
        Keys already seen are not overwritten by later files.
        """
        out: dict = {}
        for env_path in [Path(".env"), Path(__file__).resolve().parents[2] / ".env"]:
            if not env_path.exists():
                continue
            try:
                with env_path.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip('"').strip("'")
                            if k and k not in out:
                                out[k] = v
            except Exception as err:
                logger.debug(f"[JevDecision] Env load error ({env_path}): {err}")
                continue
        return out

    def _init_cache(self):
        try:
            db_dir = Path(self.cache_db).parent
            if db_dir != Path():
                db_dir.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.cache_db) as conn:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA busy_timeout = 5000;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS jev_gatekeeper_verdicts (
                        cache_key TEXT PRIMARY KEY,
                        payload_hash TEXT,
                        pair TEXT,
                        timestamp TEXT,
                        side TEXT,
                        decision TEXT,
                        confidence REAL,
                        market_regime TEXT,
                        source TEXT,
                        model_version TEXT,
                        engine_version TEXT,
                        prompt_version TEXT,
                        confirm_threshold TEXT,
                        fail_policy TEXT,
                        mode TEXT,
                        status TEXT,
                        reasons TEXT,
                        raw_response TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Migrate any legacy table that lacks cache_key identity columns.
                cols = {
                    r[1]
                    for r in conn.execute("PRAGMA table_info(jev_gatekeeper_verdicts)").fetchall()
                }
                if "cache_key" not in cols:
                    logger.info(
                        "[JevDecisionEngine] Migrating gatekeeper cache to cache_key identity schema..."
                    )
                    conn.execute(
                        "ALTER TABLE jev_gatekeeper_verdicts RENAME TO jev_gatekeeper_verdicts_legacy"
                    )
                    conn.execute("""
                        CREATE TABLE jev_gatekeeper_verdicts (
                            cache_key TEXT PRIMARY KEY,
                            payload_hash TEXT,
                            pair TEXT,
                            timestamp TEXT,
                            side TEXT,
                            decision TEXT,
                            confidence REAL,
                            market_regime TEXT,
                            source TEXT,
                            model_version TEXT,
                            engine_version TEXT,
                            prompt_version TEXT,
                            confirm_threshold TEXT,
                            fail_policy TEXT,
                            mode TEXT,
                            status TEXT,
                            reasons TEXT,
                            raw_response TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    # Do not copy legacy rows — old identity was incomplete by design.
                    conn.execute("DROP TABLE jev_gatekeeper_verdicts_legacy")
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_gatekeeper_lookup
                    ON jev_gatekeeper_verdicts(pair, timestamp, side, payload_hash)
                """)
                conn.commit()
        except Exception as e:
            logger.warning(f"Jev Gatekeeper cache init warning: {e}")

    def _cache_model_version(self) -> str:
        if self.mode == "external":
            return self.external_model_version
        return MODEL_VERSION_NATIVE

    def _cache_threshold_token(self) -> str:
        w1_pen = getattr(self, "regime_1w_bear_penalty", 0.0)
        suffix = f"_w1p{w1_pen:.2f}" if w1_pen > 0.0 else ""
        if self.mode != "external":
            return f"native{suffix}"
        threshold = _external_confirm_threshold()
        if threshold is None:
            return "invalid"
        w_ai = _external_weight_ai()
        cp = _external_choppy_penalty()
        return f"{threshold:.4f}_w{w_ai:.2f}_cp{cp:.2f}_runway{suffix}"

    def _lookup_raw_external(self, p_hash: str) -> dict[str, Any] | None:
        """Look up previously cached raw external AI response for this payload and prompt version."""
        if not self.cache_db or not p_hash or self.cache_db == ":memory:":
            return None
        prompt_v = getattr(self, "prompt_version", PROMPT_VERSION_NATIVE)
        try:
            with sqlite3.connect(self.cache_db, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT raw_response
                       FROM jev_gatekeeper_verdicts
                       WHERE payload_hash=? AND prompt_version=? AND status='SUCCESS'
                         AND raw_response IS NOT NULL AND raw_response != ''
                       ORDER BY created_at DESC LIMIT 1""",
                    (p_hash, prompt_v),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        raw = json.loads(row[0])
                        if isinstance(raw, dict) and "answers" in raw:
                            return raw
                    except (json.JSONDecodeError, TypeError, KeyError) as err:
                        logger.debug(f"Raw external cache decode error: {err}")
        except Exception as e:
            logger.debug(f"Raw external cache lookup bypassed: {e}")
        return None

    def _build_cache_key(self, payload_hash: str) -> str:
        return build_decision_cache_key(
            payload_hash,
            model_version=self._cache_model_version(),
            engine_version=getattr(self, "engine_version", ENGINE_VERSION),
            prompt_version=getattr(self, "prompt_version", PROMPT_VERSION_NATIVE),
            confirm_threshold=self._cache_threshold_token(),
            fail_policy=getattr(self, "fail_policy", "reject"),
            mode=getattr(self, "mode", "native"),
        )

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Evaluate payload and emit verdict.
        1. Validate payload.
        2. Lookup SQLite cache (accept only SUCCESS status).
        3. Route by mode (native, backtest, external).
        4. Apply fail-safe policy on error.
        """
        if not payload or not isinstance(payload, dict):
            return make_verdict(
                decision=JevDecision.VETO,
                status=JevStatus.INVALID,
                market_regime=JevMarketRegime.INSUFFICIENT_DATA,
                reasons=["Empty or invalid payload structure"],
                source="validator",
            )

        pair = payload.get("pair", "UNKNOWN")
        ts = payload.get("timestamp", "")
        side = payload.get("side", JevSide.NONE.value).upper()
        p_hash = payload.get("payload_hash", "")
        seq = payload.get("trajectory_sequence", [])

        # Verify data readiness
        if not seq or len(seq) < 5:
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.INSUFFICIENT_DATA,
                status=JevStatus.INVALID,
                reasons=[f"Insufficient trajectory sequence ({len(seq)} candles, min 5 required)"],
                source="validator",
            )

        # 1. Lookup SQLite cache with full identity token
        cache_key = self._build_cache_key(p_hash) if p_hash else ""
        if self.cache_db and cache_key:
            try:
                with sqlite3.connect(self.cache_db, timeout=5.0) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """SELECT decision, confidence, market_regime, reasons, source,
                                  model_version, engine_version, status, raw_response,
                                  prompt_version, confirm_threshold, fail_policy, mode
                           FROM jev_gatekeeper_verdicts WHERE cache_key=?""",
                        (cache_key,),
                    )
                    row = cursor.fetchone()
                    if row:
                        status_cached = row[7]
                        cached_engine_ver = row[6]
                        cached_prompt = row[9] or ""
                        cached_threshold = row[10] or ""
                        cached_fail_policy = row[11] or ""
                        cached_mode = row[12] or ""
                        expected_threshold = self._cache_threshold_token()
                        identity_ok = (
                            status_cached == JevStatus.SUCCESS.value
                            and cached_engine_ver == getattr(self, "engine_version", ENGINE_VERSION)
                            and cached_prompt
                            == getattr(self, "prompt_version", PROMPT_VERSION_NATIVE)
                            and cached_threshold == expected_threshold
                            and cached_fail_policy == getattr(self, "fail_policy", "reject")
                            and cached_mode == getattr(self, "mode", "native")
                            and expected_threshold != "invalid"
                        )
                        if identity_ok:
                            try:
                                reasons_cached = json.loads(row[3]) if row[3] else []
                            except Exception:
                                reasons_cached = [str(row[3])]
                            try:
                                raw_cached = json.loads(row[8]) if row[8] else {}
                            except Exception:
                                raw_cached = {}

                            return make_verdict(
                                decision=row[0],
                                side=side,
                                confidence=row[1],
                                market_regime=row[2],
                                reasons=reasons_cached + [f"Loaded from cache ({row[4]})"],
                                source="cache",
                                model_version=row[5],
                                engine_version=row[6],
                                status=status_cached,
                                raw_response=raw_cached,
                            )
            except Exception as e:
                logger.debug(f"Cache lookup bypassed: {e}")

        # 2. Route by mode
        if self.mode == "native" or self.mode == "backtest":
            verdict = self._evaluate_native_sequence(payload)
        elif self.mode == "external":
            native_verdict = self._evaluate_native_sequence(payload)
            native_conf = float(native_verdict.get("confidence", 0.50))
            native_reasons = list(native_verdict.get("reasons", []))

            raw_cached = (
                self._lookup_raw_external(p_hash)
                if os.environ.get("JEV_REUSE_RAW_CACHE") == "1"
                else None
            )

            if not self.api_key and raw_cached is None:
                logger.warning(
                    "External mode active but JEV_API_KEY not configured. Enforcing fail policy."
                )
                if self.fail_policy == "fallback_native":
                    verdict = native_verdict
                    verdict["source"] = "native_fallback"
                else:
                    return make_verdict(
                        decision=JevDecision.VETO,
                        side=side,
                        status=JevStatus.UNAVAILABLE,
                        market_regime=JevMarketRegime.UNKNOWN,
                        reasons=["External API Key missing, rejected by fail policy"],
                        source="policy_gate",
                    )
            else:
                verdict = self._call_external_jev(
                    payload,
                    native_conf=native_conf,
                    native_reasons=native_reasons,
                    cached_raw=raw_cached,
                )
                if verdict.get("status") != JevStatus.SUCCESS.value:
                    if self.fail_policy == "fallback_native":
                        logger.info(
                            "External API unavailable, falling back to Native Quant Evaluator."
                        )
                        verdict = native_verdict
                        verdict["source"] = "native_fallback"
                    elif self.fail_policy == "allow":
                        logger.info("External API unavailable, fail policy allows pass-through.")
                    else:
                        logger.warning(
                            f"External API failed and fail policy is {self.fail_policy!r} -> VETO."
                        )
                        verdict["decision"] = JevDecision.VETO.value
        else:
            verdict = self._evaluate_native_sequence(payload)

        # 3. Write to SQLite cache on SUCCESS
        threshold_token = self._cache_threshold_token()
        if (
            self.cache_db
            and verdict.get("status") == JevStatus.SUCCESS.value
            and p_hash
            and cache_key
            and threshold_token != "invalid"
        ):
            try:
                with sqlite3.connect(self.cache_db, timeout=5.0) as conn:
                    conn.execute(
                        """INSERT OR REPLACE INTO jev_gatekeeper_verdicts
                           (cache_key, payload_hash, pair, timestamp, side, decision, confidence,
                            market_regime, source, model_version, engine_version, prompt_version,
                            confirm_threshold, fail_policy, mode, status, reasons, raw_response)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            cache_key,
                            p_hash,
                            pair,
                            ts,
                            side,
                            verdict.get("decision"),
                            verdict.get("confidence", 0.0),
                            verdict.get("market_regime", JevMarketRegime.UNKNOWN.value),
                            verdict.get("source", "native"),
                            verdict.get("model_version", self._cache_model_version()),
                            verdict.get("engine_version", ENGINE_VERSION),
                            getattr(self, "prompt_version", PROMPT_VERSION_NATIVE),
                            threshold_token,
                            getattr(self, "fail_policy", "reject"),
                            getattr(self, "mode", "native"),
                            verdict.get("status", JevStatus.SUCCESS.value),
                            json.dumps(verdict.get("reasons", [])),
                            json.dumps(verdict.get("raw_response", {})),
                        ),
                    )
                    conn.commit()
            except Exception as e:
                logger.debug(f"Cache write bypassed: {e}")

        return verdict

    def _call_external_jev(
        self,
        payload: dict[str, Any],
        native_conf: float = 0.50,
        native_reasons: list[str] | None = None,
        cached_raw: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send payload to Jev Cloud endpoint and compute hybrid score.
        AI noul acts as a continuous score weight instead of binary gate.
        """
        side = payload.get("side", JevSide.NONE.value).upper()
        pair = payload.get("pair", "UNKNOWN")
        current_price = payload.get("current_price", 0.0)
        seq = payload.get("trajectory_sequence", [])
        macro = payload.get("macro_context", {})
        model_id = getattr(self, "external_model_version", MODEL_VERSION_EXTERNAL)

        if cached_raw is not None:
            result = cached_raw
        else:
            state_text = _format_external_state(
                pair=pair,
                side=side,
                current_price=float(current_price or 0.0),
                seq=seq if isinstance(seq, list) else [],
                macro=macro if isinstance(macro, dict) else {},
                candle_limit=12,
            )

            req_body = {
                "model": model_id,
                "state": state_text,
                "questions": {
                    "confirm_trade": {
                        "type": "noul",
                        "instructions": _external_confirm_instructions(side),
                    },
                    "market_regime": {
                        "type": "choice",
                        "instructions": (
                            "Classify the immediate regime of this candle sequence for a sniper entry. "
                            "Use choppy_ranging only for genuine two-sided overlap without directional "
                            "close bias — impulsive waterfall or breakout candles are trend_expansion."
                        ),
                        "criteria": _external_regime_criteria(),
                    },
                },
            }

            try:
                data = json.dumps(req_body).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": f"Freqtrade-Jev/{ENGINE_VERSION}",
                }
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                timeout_val = float(os.environ.get("JEV_API_TIMEOUT", "2.0"))
                req = urllib.request.Request(self.api_url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return make_verdict(
                    decision=JevDecision.VETO,
                    side=side,
                    confidence=0.0,
                    market_regime=JevMarketRegime.UNKNOWN,
                    reasons=[f"External Jev API call failed: {str(e)}"],
                    source="external",
                    status=JevStatus.UNAVAILABLE,
                    raw_response={"error": str(e)},
                )

        answers = result.get("answers")
        if not isinstance(answers, dict):
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.UNKNOWN,
                reasons=["External API response missing or invalid answers structure"],
                source="external",
                status=JevStatus.INVALID,
                raw_response=result if isinstance(result, dict) else {"raw": result},
            )

        confirm_trade = answers.get("confirm_trade")
        market_regime_ans = answers.get("market_regime")
        if not isinstance(confirm_trade, dict) or "noul" not in confirm_trade:
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.UNKNOWN,
                reasons=["External API response missing confirm_trade.noul"],
                source="external",
                status=JevStatus.INVALID,
                raw_response=result,
            )
        if not isinstance(market_regime_ans, dict) or "choice" not in market_regime_ans:
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.UNKNOWN,
                reasons=["External API response missing market_regime.choice"],
                source="external",
                status=JevStatus.INVALID,
                raw_response=result,
            )

        noul_conf = _parse_unit_interval(confirm_trade.get("noul"), default=None)
        if noul_conf is None:
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.UNKNOWN,
                reasons=[
                    f"Invalid AI confidence (must be finite in [0,1]): {confirm_trade.get('noul')!r}"
                ],
                source="external",
                status=JevStatus.INVALID,
                raw_response=result,
            )

        threshold = _external_confirm_threshold()
        if threshold is None:
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.UNKNOWN,
                reasons=["Invalid JEV_CONFIRM_THRESHOLD (must be finite in [0,1])"],
                source="external",
                status=JevStatus.INVALID,
                raw_response=result,
            )

        w_ai = _external_weight_ai()
        choppy_penalty = _external_choppy_penalty()

        # Continuous AI score adjustment centered at 0.50:
        # noul=1.00 -> +w_ai, noul=0.50 -> 0.00, noul=0.00 -> -w_ai
        ai_delta = max(-w_ai, min(w_ai, w_ai * (noul_conf - 0.50) * 2.0))

        regime_choice = str(market_regime_ans.get("choice") or "unknown")
        regime = regime_choice.upper()
        actual_model = result.get("model") if isinstance(result.get("model"), str) else model_id

        regime_penalty = 0.0
        if regime in ("CHOPPY_RANGING", "CHOPPY", "RANGING"):
            regime_penalty = choppy_penalty

        # Compute composite Hybrid Score
        hybrid_score = round(native_conf + ai_delta - regime_penalty, 2)
        hybrid_score = max(0.0, min(1.0, hybrid_score))

        hard_choppy = getattr(self, "hard_choppy", False) or (
            os.environ.get("JEV_DECISION_HARD_CHOPPY", "0") == "1"
        )
        if hard_choppy and regime in ("CHOPPY_RANGING", "CHOPPY", "RANGING"):
            decision = JevDecision.VETO.value
        elif hybrid_score >= threshold:
            decision = JevDecision.CONFIRM.value
        else:
            decision = JevDecision.VETO.value

        reasons = list(native_reasons or []) + [
            f"Native score: {native_conf:.2f}",
            f"Jev Cloud probability: {noul_conf:.2f}",
            f"AI adjustment: delta={ai_delta:+.2f} (w_ai={w_ai:.2f})",
            f"Market regime: {regime}"
            + (f" (penalty: -{regime_penalty:.2f})" if regime_penalty > 0 else ""),
            f"Hybrid score: {hybrid_score:.2f} vs threshold {threshold:.2f}",
            f"Model: {actual_model}",
        ]
        if regime in ("CHOPPY_RANGING", "CHOPPY", "RANGING"):
            reasons.append("Penalized by choppy/ranging regime")
        if decision == JevDecision.VETO.value:
            reasons.append("Below confirm threshold")

        return make_verdict(
            decision=decision,
            side=side,
            confidence=hybrid_score,
            market_regime=regime,
            reasons=reasons,
            source="external",
            model_version=actual_model,
            status=JevStatus.SUCCESS,
            raw_response=result,
        )

    def _evaluate_native_sequence(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Native Quant System-1 Evaluator:
        Analyze 32-candle sequence:
        1. Trigger impulse candle.
        2. Pre-breakout compression phase.
        3. Sequential order flow closes.
        4. Macro MTF confluence.
        """
        side = payload.get("side", JevSide.NONE.value).upper()
        seq = payload.get("trajectory_sequence", [])
        macro = payload.get("macro_context", {})

        if len(seq) < 5:
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.INSUFFICIENT_DATA,
                reasons=["Short sequence history (< 5 candles)"],
                source="native",
                status=JevStatus.INVALID,
            )

        reasons = []
        confidence = 0.50

        # A. Trigger candle analysis
        last_candle = seq[-1]
        is_impulse = (last_candle.get("body_ratio", 0) >= 0.55) and (
            last_candle.get("vol_ratio", 0) >= 1.20
        )
        has_adverse_wick = (
            (last_candle.get("lower_wick", 0) >= 0.35)
            if side == "SHORT"
            else (last_candle.get("upper_wick", 0) >= 0.35)
        )

        if has_adverse_wick:
            confidence -= 0.25
            reasons.append("Adverse wick absorption detected on trigger candle")
        elif is_impulse:
            confidence += 0.15
            reasons.append("Impulsive breakout candle with expanding volume")

        # B. Pre-breakout compression
        prior_candles = seq[-5:-1]
        if prior_candles:
            avg_prior_vol = sum(c.get("vol_ratio", 1.0) for c in prior_candles) / len(prior_candles)
            avg_prior_range = sum(c.get("range_pct", 1.0) for c in prior_candles) / len(
                prior_candles
            )
            if avg_prior_vol < 1.0 and avg_prior_range < 0.6:
                confidence += 0.10
                reasons.append("Clean pre-breakout volatility compression confirmed")

        # C. Sequential order flow
        favorable_closes = sum(
            1
            for c in seq[-5:]
            if (
                c.get("close_pos", 0.5) <= 0.40
                if side == "SHORT"
                else c.get("close_pos", 0.5) >= 0.60
            )
        )
        if favorable_closes >= 3:
            confidence += 0.10
            reasons.append(
                f"Directional order flow aligned ({favorable_closes}/5 candles favorable)"
            )

        # D. Macro MTF context
        trend_4h = macro.get("trend_4h", "NEUTRAL")
        atr_pct = macro.get("atr_percentile", 50.0)

        if side == "SHORT":
            if trend_4h == "BEAR":
                confidence += 0.10
                reasons.append("4H Bear Wave macro alignment")
            score = macro.get("short_score", 50.0)
        else:
            if trend_4h == "BULL":
                confidence += 0.10
                reasons.append("4H Bull Wave macro alignment")
            score = macro.get("long_score", 50.0)

        if score >= 80.0:
            confidence += 0.10
            reasons.append(f"AI Lens Confluence Score strong ({score:.1f})")

        raw_setup = payload.get("setup") or ""
        setup = raw_setup.lower() if isinstance(raw_setup, str) else ""
        is_waterfall_setup = (
            setup == ShortSetup.WATERFALL_COLLAPSE.value
            or setup in ("waterfall_drop", "waterfall_collapse")
            or "waterfall" in setup
        )

        pair = str(payload.get("pair") or "")
        base_asset = pair.split("/", 1)[0].split(":", 1)[0].strip().upper()
        wf_cfg = DEFAULT_ETH_WATERFALL_FILTER if base_asset == "ETH" else DEFAULT_WATERFALL_FILTER

        # E. Market structure & headroom
        floor_valid = bool(macro.get("floor_20d_valid", True))
        floor_dist = macro.get("floor_20d_dist_pct", 0.0)
        dist_ema50_1h = macro.get("ema_50_1h_dist_pct", 0.0)
        rsi_1h = macro.get("rsi_1h", 50.0)
        rel_vol = macro.get("rel_vol", 1.0)
        is_vacuum = (
            floor_valid
            and is_waterfall_setup
            and is_vacuum_breakdown(
                floor_dist,
                rel_vol,
                vacuum_floor_dist_max=wf_cfg.vacuum_floor_dist_max,
                min_surge_vol=wf_cfg.vacuum_min_vol_ratio,
            )
        )

        # Heavy penalty if ATR in extreme volatility zone (>90%)
        # Waived for waterfall ONLY during vacuum breakdown
        if atr_pct > wf_cfg.max_atr_pct:
            if is_vacuum:
                reasons.append(
                    f"High ATR volatility aligned with waterfall vacuum breakdown ({atr_pct:.1f}%)"
                )
            else:
                confidence -= 0.20
                reasons.append(f"Extreme ATR volatility risk ({atr_pct:.1f}%)")

        if side == "SHORT":
            if is_waterfall_setup and floor_valid and floor_dist <= 0.0:
                confidence += 0.10
                reasons.append(
                    f"Waterfall clean floor breakdown confirmed (floor_dist <= {floor_dist:.1f}%)"
                )
            elif floor_valid and floor_dist >= 15.0 and dist_ema50_1h < 0.0 and rsi_1h < 50.0:
                confidence += 0.10
                reasons.append(
                    f"High-runway short momentum alignment (floor_dist >= {floor_dist:.1f}%, 1H momentum bear)"
                )

            # Reversal / counter-trend guards:
            # 1. 1H counter-wave trap: Price above 1H EMA50 with bullish RSI (>50).
            # Never waived (even during vacuum breakdown).
            if dist_ema50_1h > 0.0 and rsi_1h > 50.0:
                confidence -= 0.10
                reasons.append("Counter-trend trap on short (above 1H EMA50 with bullish RSI)")
            # 2. Exhausted floor risk: Selling into hard floor (< 8%) without vacuum confirmation.
            # Waived ONLY if is_vacuum.
            elif not is_vacuum and floor_dist < 8.0:
                confidence -= 0.10
                reasons.append("Counter-trend trap or exhausted floor risk on short")
        elif side == "LONG":
            if dist_ema50_1h > 0.0 and rsi_1h > 50.0:
                confidence += 0.05
                reasons.append("1H Bull momentum alignment")
            elif dist_ema50_1h < 0.0 and rsi_1h < 50.0:
                confidence -= 0.10
                reasons.append("Counter-trend trap on long (below 1H EMA50)")

            regime_1w = macro.get("regime_1w") or ""
            is_bear_wave_1w = bool(macro.get("is_bear_wave_1w", False)) or regime_1w in (
                "cycle_top",
                "bear_trending",
            )
            w1_penalty = getattr(self, "regime_1w_bear_penalty", 0.0)
            if is_bear_wave_1w and w1_penalty > 0.0:
                confidence -= w1_penalty
                reasons.append(
                    f"1W bear-wave macro penalty ({regime_1w or 'bear_wave'}, -{w1_penalty:.2f})"
                )

        confidence = max(0.0, min(1.0, round(confidence, 2)))

        # Native Heuristic threshold: >= 0.70 CONFIRM, < 0.70 VETO
        decision = JevDecision.CONFIRM.value if confidence >= 0.70 else JevDecision.VETO.value
        regime = (
            JevMarketRegime.TREND_EXPANSION.value
            if is_impulse
            else (
                JevMarketRegime.CHOPPY_RANGING.value
                if confidence < 0.60
                else JevMarketRegime.PULLBACK_SETUP.value
            )
        )

        return make_verdict(
            decision=decision,
            side=side,
            confidence=confidence,
            market_regime=regime,
            reasons=reasons,
            source="native",
            model_version=MODEL_VERSION_NATIVE,
            status=JevStatus.SUCCESS,
        )
