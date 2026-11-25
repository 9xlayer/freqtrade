"""
Jev Entry Hunter — optional escalation path (live OFF in v4.4.0-deterministic).

Kept for later re-enable behind ``enable_jev_hunter`` / EntryPipeline hunter gateway.
Stage-1 MomentumRadar + Stage-2 JevHunterEngine + SQLite cache.
"""

import hashlib
import json
import logging
import math
import os
import sqlite3
import urllib.error
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame

# Version hub: strategies.mtf_sniper.__init__ (single bump site).
from strategies.mtf_sniper import (
    HUNTER_ENGINE_VERSION,
    HUNTER_MODEL_VERSION_EXTERNAL_DEFAULT,
    HUNTER_MODEL_VERSION_NATIVE,
)
from strategies.mtf_sniper.signals.momentum_radar import MomentumRadar  # noqa: F401


logger = logging.getLogger(__name__)


# Typesafe only exposes aliases like jev-latest (resolves e.g. jev-1.14.0).
# Dedicated names such as jev-hunter-v1.0 return HTTP 400 Unknown model.
HUNTER_MODEL_VERSION_EXTERNAL = os.environ.get(
    "JEV_HUNTER_MODEL_VERSION", HUNTER_MODEL_VERSION_EXTERNAL_DEFAULT
)
# Mutable / floating aliases must not be used as durable cache keys.
_MUTABLE_MODEL_ALIASES = frozenset(
    {
        "jev-latest",
        "latest",
        "jev",
        "default",
    }
)


def is_mutable_model_id(model_id: str | None) -> bool:
    """True when model id is a floating alias (e.g. jev-latest), not an immutable version."""
    if not model_id:
        return True
    mid = model_id.strip().lower()
    if mid in _MUTABLE_MODEL_ALIASES:
        return True
    return mid.endswith(("-latest", ":latest"))


class JevHunterAction(str, Enum):
    ENTER_SHORT = "enter_short"
    ENTER_LONG = "enter_long"
    NO_TRADE = "no_trade"


class JevSetupType(str, Enum):
    WATERFALL_COLLAPSE = "waterfall_collapse"
    BREAKOUT_EXPANSION = "breakout_expansion"
    ABSORPTION_TRAP = "absorption_trap"
    TREND_PULLBACK = "trend_pullback"
    CONSOLIDATION_NOISE = "consolidation_noise"
    UNKNOWN = "unknown"


class JevHunterStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"


def is_setup_valid_for_side(setup_type: str | JevSetupType, side: str) -> bool:
    """
    Validate setup/side compatibility for trend-following Hunter entries:
    - SHORT: waterfall_collapse
    - LONG: breakout_expansion
    - Mean-reversion labels (absorption_trap / trend_pullback) are not tradeable here
    """
    val = getattr(setup_type, "value", setup_type).lower()
    s = side.lower()
    if s == "short":
        return val == JevSetupType.WATERFALL_COLLAPSE.value
    elif s == "long":
        return val == JevSetupType.BREAKOUT_EXPANSION.value
    return False


def _clean_val(val: Any) -> Any:
    """
    Recursively normalize values for deterministic serialization:
    - NaN / pd.NA / None -> None
    - Inf / -Inf -> 'Infinity' / '-Infinity'
    - Float -> Round to 6 decimal places to prevent cross-OS jitter
    - Dict -> Sort keys ascending
    - List/Tuple -> List of normalized elements
    """
    if val is None:
        return None
    if isinstance(val, (bool, str, int)):
        return val
    if isinstance(val, (float, np.floating)):
        if math.isnan(val) or pd.isna(val):
            return None
        if math.isinf(val):
            return "Infinity" if val > 0 else "-Infinity"
        return round(float(val), 6)
    if pd.isna(val):
        return None
    if isinstance(val, dict):
        return {str(k): _clean_val(v) for k, v in sorted(val.items())}
    if isinstance(val, (list, tuple, np.ndarray, pd.Series)):
        return [_clean_val(v) for v in val]
    return str(val)


def build_canonical_payload(
    df_32: DataFrame,
    pair: str,
    timestamp: str,
    event_trigger: str,
    macro_info: dict[str, Any] | None = None,
    model_version: str = "",
    engine_version: str = HUNTER_ENGINE_VERSION,
) -> dict[str, Any]:
    """
    Create canonical payload containing all data affecting Hunter verdict:
    1. OHLCV candle sequence
    2. Derived features directly consumed by Native & External engines
       - rel_vol
       - volume_ratio_20
    3. Macro Structural Anchors
    4. Event metadata
    5. Version identity (model_version, engine_version)
    """
    macro_dict = macro_info or {}
    last_candle = df_32.iloc[-1]
    current_price = float(last_candle["close"])

    # Identify consumed OHLCV and derived columns
    core_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df_32.columns]
    derived_cols = [c for c in ["rel_vol", "volume_ratio_20"] if c in df_32.columns]
    all_consumed_cols = core_cols + derived_cols

    candles_data = []
    for _, row in df_32[all_consumed_cols].iterrows():
        c_dict = {col: _clean_val(row[col]) for col in all_consumed_cols}
        candles_data.append(c_dict)

    canonical = {
        "pair": pair,
        "timestamp": timestamp,
        "event_trigger": event_trigger,
        "current_price": _clean_val(current_price),
        "candles": candles_data,
        "macro": _clean_val(macro_dict),
        "model_version": model_version,
        "engine_version": engine_version,
    }
    return canonical


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """
    Compute deterministic SHA-256 hash of canonical payload.
    Ensures 100% determinism independent of key ordering or float representation.
    """
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:16]


class JevHunterCache:
    """
    Local SQLite cache for Jev Hunter verdicts.
    Supports WAL mode, concurrency-safe, version-aware, no transient errors.
    """

    def __init__(self, db_path: str = "user_data/jev_hunter_cache.sqlite"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        db_dir = Path(self.db_path).parent
        if db_dir != Path():
            db_dir.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self.db_path, timeout=30.0, isolation_level="IMMEDIATE") as conn:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA busy_timeout = 30000;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS jev_hunter_verdicts (
                        pair TEXT,
                        timestamp TEXT,
                        engine_version TEXT,
                        model_version TEXT,
                        payload_hash TEXT,
                        action TEXT,
                        setup_type TEXT,
                        confidence REAL,
                        status TEXT,
                        raw_response TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (pair, timestamp, engine_version, model_version, payload_hash)
                    )
                """)
                # Auto-migrate schema if legacy version lacks payload_hash in PRIMARY KEY
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(jev_hunter_verdicts)")
                columns = cursor.fetchall()
                pk_cols = [c[1] for c in columns if len(c) > 5 and c[5] > 0]
                if ("payload_hash" not in pk_cols or "model_version" not in pk_cols) and len(
                    pk_cols
                ) > 0:
                    logger.info(
                        "[JevHunterCache] Migrating primary key to (pair, timestamp, engine_version, model_version, payload_hash)..."
                    )
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS jev_hunter_verdicts_v3 (
                            pair TEXT,
                            timestamp TEXT,
                            engine_version TEXT,
                            model_version TEXT,
                            payload_hash TEXT,
                            action TEXT,
                            setup_type TEXT,
                            confidence REAL,
                            status TEXT,
                            raw_response TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (pair, timestamp, engine_version, model_version, payload_hash)
                        )
                    """)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO jev_hunter_verdicts_v3
                        (pair, timestamp, engine_version, model_version, payload_hash, action, setup_type, confidence, status, raw_response, created_at)
                        SELECT pair, timestamp, engine_version,
                               COALESCE(model_version, ?),
                               COALESCE(NULLIF(payload_hash, ''), 'no_hash'),
                               action, setup_type, confidence, status, raw_response, created_at
                        FROM jev_hunter_verdicts
                        """,
                        (HUNTER_MODEL_VERSION_NATIVE,),
                    )
                    conn.execute("DROP TABLE jev_hunter_verdicts")
                    conn.execute("ALTER TABLE jev_hunter_verdicts_v3 RENAME TO jev_hunter_verdicts")
                conn.commit()
        except Exception as e:
            logger.warning(f"[JevHunterCache] Failed to initialize DB at {self.db_path}: {e}")

    def get(
        self,
        pair: str,
        timestamp: str,
        engine_version: str = HUNTER_ENGINE_VERSION,
        model_version: str | None = None,
        payload_hash: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                conn.execute("PRAGMA busy_timeout = 30000;")
                cursor = conn.cursor()
                query = """
                    SELECT action, setup_type, confidence, status, raw_response, payload_hash, model_version
                    FROM jev_hunter_verdicts
                    WHERE pair=? AND timestamp=? AND engine_version=?
                """
                params = [pair, timestamp, engine_version]
                if model_version:
                    query += " AND model_version=?"
                    params.append(model_version)
                if payload_hash:
                    query += " AND payload_hash=?"
                    params.append(payload_hash)

                cursor.execute(query, tuple(params))
                row = cursor.fetchone()
                if row:
                    action, setup_type, conf, stat, raw_resp_str, p_hash, m_ver = row
                    # Do not return cached error/invalid entries
                    if stat not in (
                        JevHunterStatus.CONFIRMED.value,
                        JevHunterStatus.REJECTED.value,
                    ):
                        return None

                    return {
                        "action": action,
                        "setup_type": setup_type,
                        "confidence": float(conf),
                        "status": stat,
                        "payload_hash": p_hash,
                        "model_version": m_ver,
                        "raw_response": json.loads(raw_resp_str) if raw_resp_str else {},
                    }

                # Lookup fallback in frozen historical table
                # Allow legacy fallback only when no model_version is required or model is jev-frozen
                if model_version is None or model_version == "jev-frozen":
                    try:
                        cursor.execute(
                            "SELECT action, setup_type, confidence, raw_response FROM jev_verdicts WHERE pair=? AND timestamp=?",
                            (pair, timestamp),
                        )
                        legacy_row = cursor.fetchone()
                        if legacy_row:
                            l_action, l_setup, l_conf, l_raw = legacy_row
                            l_stat = (
                                JevHunterStatus.CONFIRMED.value
                                if l_action in ("enter_short", "enter_long")
                                else JevHunterStatus.REJECTED.value
                            )
                            return {
                                "action": l_action,
                                "setup_type": l_setup,
                                "confidence": float(l_conf),
                                "status": l_stat,
                                "payload_hash": "legacy_frozen",
                                "model_version": "jev-frozen",
                                "raw_response": json.loads(l_raw) if l_raw else {},
                            }
                    except (json.JSONDecodeError, ValueError) as err:
                        logger.debug(f"[JevHunterCache] Legacy parse error: {err}")
        except Exception as e:
            logger.warning(f"[JevHunterCache] Read error: {e}")
        return None

    def put(
        self,
        pair: str,
        timestamp: str,
        action: str,
        setup_type: str,
        confidence: float,
        status: str,
        raw_response: dict[str, Any],
        payload_hash: str = "no_hash",
        model_version: str = HUNTER_MODEL_VERSION_NATIVE,
        engine_version: str = HUNTER_ENGINE_VERSION,
    ):
        # Never cache transient failures or invalid responses
        if status not in (JevHunterStatus.CONFIRMED.value, JevHunterStatus.REJECTED.value):
            return

        p_hash = payload_hash if payload_hash else "no_hash"
        try:
            with sqlite3.connect(self.db_path, timeout=30.0, isolation_level="IMMEDIATE") as conn:
                conn.execute("PRAGMA busy_timeout = 30000;")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO jev_hunter_verdicts
                    (pair, timestamp, engine_version, model_version, payload_hash, action, setup_type, confidence, status, raw_response)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pair,
                        timestamp,
                        engine_version,
                        model_version,
                        p_hash,
                        action,
                        setup_type,
                        confidence,
                        status,
                        json.dumps(raw_response or {}, default=str),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"[JevHunterCache] Write error: {e}")


class JevHunterEngine:
    """
    Stage 2: Jev Active Hunter Decision Engine
    Supports Native Heuristic, External API, and Backtest Mode with full determinism.
    """

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        cache_db: str = "user_data/jev_hunter_cache.sqlite",
        mode: str | None = None,
        model_version: str | None = None,
        missing_model_policy: str | None = None,
    ):
        self._load_env()
        # Modes: native | external | backtest
        self.mode = (mode or os.environ.get("JEV_MODE", "native")).lower()
        if self.mode not in ("native", "external", "backtest"):
            self.mode = "native"

        self.api_url = api_url or os.environ.get(
            "JEV_API_URL", "https://api.typesafe.ai/v1/systemone"
        )
        self.api_key = api_key or os.environ.get("JEV_API_KEY")
        self.cache = JevHunterCache(cache_db)

        # External model version configuration and missing version policy
        self.external_model_version = (
            model_version
            or os.environ.get("JEV_HUNTER_MODEL_VERSION")
            or HUNTER_MODEL_VERSION_EXTERNAL
        )
        # Default no_cache: never persist verdicts under a mutable alias when backend omits version.
        self.missing_model_policy = (
            missing_model_policy or os.environ.get("JEV_HUNTER_MISSING_MODEL_POLICY", "no_cache")
        ).lower()
        if self.missing_model_policy not in ("no_cache", "use_configured"):
            self.missing_model_policy = "no_cache"

        # In-process map: configured alias -> last resolved immutable backend model.
        self._alias_resolution: dict[str, str] = {}

        logger.info(
            f"[JevHunterEngine] Initialized in mode: '{self.mode.upper()}' "
            f"(Engine: {HUNTER_ENGINE_VERSION}, ExtModel: {self.external_model_version}, "
            f"MissingModelPolicy: {self.missing_model_policy})"
        )

    def _resolve_lookup_model(self, configured_model: str) -> str:
        """
        Prefer immutable model identity for cache lookup.
        If configured is a mutable alias and we previously resolved an actual version, use that.
        """
        if not is_mutable_model_id(configured_model):
            return configured_model
        resolved = self._alias_resolution.get(configured_model)
        if resolved and not is_mutable_model_id(resolved):
            return resolved
        return configured_model

    def _remember_alias_resolution(self, configured_model: str, actual_model: str) -> None:
        if (
            is_mutable_model_id(configured_model)
            and actual_model
            and not is_mutable_model_id(actual_model)
        ):
            self._alias_resolution[configured_model] = actual_model

    def _load_env(self):
        if not os.environ.get("JEV_API_KEY"):
            for env_path in [Path(".env"), Path(__file__).resolve().parents[2] / ".env"]:
                if env_path.exists():
                    try:
                        with env_path.open(encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#") and "=" in line:
                                    k, v = line.split("=", 1)
                                    k, v = k.strip(), v.strip().strip('"').strip("'")
                                    if k not in os.environ:
                                        os.environ[k] = v
                    except (OSError, UnicodeDecodeError) as err:
                        logger.debug(f"[JevHunter] Env load error: {err}")

    def evaluate_candidate(
        self,
        df_32: DataFrame,
        pair: str,
        timestamp: str,
        event_trigger: str,
        macro_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Evaluate candidate candle sequence (16-32 candles):
        enter_short, enter_long, or no_trade with setup_type and status.
        """
        # Fail-closed if insufficient data
        if df_32 is None or len(df_32) < 16:
            return {
                "action": JevHunterAction.NO_TRADE.value,
                "setup_type": JevSetupType.UNKNOWN.value,
                "confidence": 0.0,
                "status": JevHunterStatus.INVALID_RESPONSE.value,
                "source": self.mode,
                "engine_version": HUNTER_ENGINE_VERSION,
                "error": "Insufficient candle sequence (<16 bars)",
            }

        macro_dict = macro_info or {}

        # Model identity for the cache PK column (prefer immutable / resolved actual).
        configured_model = (
            HUNTER_MODEL_VERSION_NATIVE
            if self.mode in ("native", "backtest")
            else self.external_model_version
        )
        lookup_model = (
            configured_model
            if self.mode in ("native", "backtest")
            else self._resolve_lookup_model(configured_model)
        )
        # Content hash must stay stable across alias → actual resolution.
        # Model identity lives in the cache PK column, not inside the hash seed for external mode.
        hash_model_token = (
            HUNTER_MODEL_VERSION_NATIVE if self.mode in ("native", "backtest") else ""
        )
        canonical_payload = build_canonical_payload(
            df_32,
            pair,
            timestamp,
            event_trigger,
            macro_dict,
            model_version=hash_model_token,
            engine_version=HUNTER_ENGINE_VERSION,
        )
        payload_hash = compute_payload_hash(canonical_payload)

        # 1. Cache lookup by (engine, model identity, content hash)
        cached = self.cache.get(
            pair,
            timestamp,
            engine_version=HUNTER_ENGINE_VERSION,
            model_version=lookup_model,
            payload_hash=payload_hash,
        )
        if cached:
            return cached

        # 2. Route by mode
        if self.mode == "backtest":
            # Never call live API in backtest mode.
            # If cache miss, fallback to Native Heuristic for determinism.
            return self._evaluate_native(
                df_32, pair, timestamp, event_trigger, macro_dict, payload_hash
            )

        elif self.mode == "native":
            return self._evaluate_native(
                df_32, pair, timestamp, event_trigger, macro_dict, payload_hash
            )

        elif self.mode == "external":
            return self._evaluate_external(
                df_32, pair, timestamp, event_trigger, macro_dict, payload_hash
            )

        # Safe fallback
        return {
            "action": JevHunterAction.NO_TRADE.value,
            "setup_type": JevSetupType.UNKNOWN.value,
            "confidence": 0.0,
            "status": JevHunterStatus.UNAVAILABLE.value,
            "source": self.mode,
            "engine_version": HUNTER_ENGINE_VERSION,
            "error": "Unhandled mode",
        }

    def _evaluate_native(
        self,
        df_32: DataFrame,
        pair: str,
        timestamp: str,
        event_trigger: str,
        macro_info: dict[str, Any],
        payload_hash: str,
    ) -> dict[str, Any]:
        """
        High-speed offline Native Quant evaluation (<1ms, 100% deterministic).
        """
        last_c = df_32.iloc[-1]
        is_short_event = "SHORT" in event_trigger
        is_long_event = "LONG" in event_trigger

        # Calculate volume ratio safely
        if "rel_vol" in last_c and not pd.isna(last_c["rel_vol"]):
            vol_ratio = float(last_c["rel_vol"])
        elif "volume_ratio_20" in last_c and not pd.isna(last_c["volume_ratio_20"]):
            vol_ratio = float(last_c["volume_ratio_20"])
        else:
            vol_sma = df_32["volume"].rolling(20, min_periods=1).mean().iloc[-1]
            vol_ratio = float(last_c["volume"] / vol_sma) if vol_sma > 0 else 1.0

        c_range = float(last_c["high"] - last_c["low"])
        c_body = float(abs(last_c["close"] - last_c["open"]))
        body_ratio = c_body / c_range if c_range > 0 else 0.5
        lower_wick_ratio = (
            (min(last_c["open"], last_c["close"]) - last_c["low"]) / c_range if c_range > 0 else 0.0
        )
        upper_wick_ratio = (
            (last_c["high"] - max(last_c["open"], last_c["close"])) / c_range
            if c_range > 0
            else 0.0
        )

        trend_4h = str(macro_info.get("trend_4h", "NEUTRAL")).upper()

        # Heuristic 1: Waterfall Collapse (SHORT)
        if is_short_event:
            # Waterfall condition: Solid red candle, close near low, volume expansion
            is_strong_bear = (
                (last_c["close"] < last_c["open"])
                and (body_ratio >= 0.50)
                and (lower_wick_ratio <= 0.285)
            )
            is_breakdown = ("BREAKOUT" in event_trigger) or ("SURGE" in event_trigger)
            macro_bear = (trend_4h == "BEAR") or (macro_info.get("ema50_1h_dist_pct", 0.0) < 0)

            if is_strong_bear and is_breakdown and macro_bear:
                confidence = 0.88 if vol_ratio >= 3.0 else 0.85
                verdict = {
                    "action": JevHunterAction.ENTER_SHORT.value,
                    "setup_type": JevSetupType.WATERFALL_COLLAPSE.value,
                    "confidence": confidence,
                    "status": JevHunterStatus.CONFIRMED.value,
                    "source": "native",
                    "model_version": HUNTER_MODEL_VERSION_NATIVE,
                    "engine_version": HUNTER_ENGINE_VERSION,
                    "raw_response": {
                        "trigger": event_trigger,
                        "vol_ratio": round(vol_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                    },
                }
                self.cache.put(
                    pair,
                    timestamp,
                    verdict["action"],
                    verdict["setup_type"],
                    verdict["confidence"],
                    verdict["status"],
                    verdict["raw_response"],
                    payload_hash=payload_hash,
                    model_version=HUNTER_MODEL_VERSION_NATIVE,
                )
                return verdict

        # Heuristic 2: Long Breakout Expansion (trend continuation only)
        elif is_long_event:
            is_strong_bull = (
                (last_c["close"] > last_c["open"])
                and (body_ratio >= 0.50)
                and (upper_wick_ratio <= 0.285)
            )
            is_breakout = ("BREAKOUT" in event_trigger) or ("SURGE" in event_trigger)
            macro_bull = (trend_4h == "BULL") or (macro_info.get("ema50_1h_dist_pct", 0.0) > 0)

            if is_strong_bull and is_breakout and macro_bull:
                confidence = 0.88 if vol_ratio >= 3.0 else 0.85
                verdict = {
                    "action": JevHunterAction.ENTER_LONG.value,
                    "setup_type": JevSetupType.BREAKOUT_EXPANSION.value,
                    "confidence": confidence,
                    "status": JevHunterStatus.CONFIRMED.value,
                    "source": "native",
                    "model_version": HUNTER_MODEL_VERSION_NATIVE,
                    "engine_version": HUNTER_ENGINE_VERSION,
                    "raw_response": {
                        "trigger": event_trigger,
                        "vol_ratio": round(vol_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                    },
                }
                self.cache.put(
                    pair,
                    timestamp,
                    verdict["action"],
                    verdict["setup_type"],
                    verdict["confidence"],
                    verdict["status"],
                    verdict["raw_response"],
                    payload_hash=payload_hash,
                    model_version=HUNTER_MODEL_VERSION_NATIVE,
                )
                return verdict

        # Incompatible impulse -> no_trade
        verdict = {
            "action": JevHunterAction.NO_TRADE.value,
            "setup_type": JevSetupType.CONSOLIDATION_NOISE.value,
            "confidence": 0.50,
            "status": JevHunterStatus.REJECTED.value,
            "source": "native",
            "model_version": HUNTER_MODEL_VERSION_NATIVE,
            "engine_version": HUNTER_ENGINE_VERSION,
            "raw_response": {
                "trigger": event_trigger,
                "reason": "Insufficient momentum confluence",
            },
        }
        self.cache.put(
            pair,
            timestamp,
            verdict["action"],
            verdict["setup_type"],
            verdict["confidence"],
            verdict["status"],
            verdict["raw_response"],
            payload_hash=payload_hash,
            model_version=HUNTER_MODEL_VERSION_NATIVE,
        )
        return verdict

    def _evaluate_external(
        self,
        df_32: DataFrame,
        pair: str,
        timestamp: str,
        event_trigger: str,
        macro_info: dict[str, Any],
        payload_hash: str,
    ) -> dict[str, Any]:
        """
        Call Jev Cloud API and parse HTTP JSON response.
        """
        if not self.api_key:
            logger.warning(
                "[JevHunterEngine] API key missing for external mode. Returning UNAVAILABLE."
            )
            return {
                "action": JevHunterAction.NO_TRADE.value,
                "setup_type": JevSetupType.UNKNOWN.value,
                "confidence": 0.0,
                "status": JevHunterStatus.UNAVAILABLE.value,
                "source": "external",
                "engine_version": HUNTER_ENGINE_VERSION,
                "error": "Missing JEV_API_KEY",
            }

        # Build narrative sequence safely
        candles_summary = []
        # Calculate standard vol_sma for sequence
        vol_sma_series = df_32["volume"].rolling(20, min_periods=1).mean().replace(0, np.nan)

        for i, (_, c) in enumerate(df_32.iterrows()):
            c_type = "trigger" if i == len(df_32) - 1 else f"T-{len(df_32) - 1 - i}"
            range_val = float(c["high"] - c["low"])
            body = float(abs(c["close"] - c["open"]))
            body_ratio = body / range_val if range_val > 0 else 0.5
            uw = (
                (float(c["high"]) - max(float(c["open"]), float(c["close"]))) / range_val
                if range_val > 0
                else 0.0
            )
            lw = (
                (min(float(c["open"]), float(c["close"])) - float(c["low"])) / range_val
                if range_val > 0
                else 0.0
            )

            if "rel_vol" in c and not pd.isna(c["rel_vol"]):
                vol_ratio = float(c["rel_vol"])
            elif "volume_ratio_20" in c and not pd.isna(c["volume_ratio_20"]):
                vol_ratio = float(c["volume_ratio_20"])
            else:
                sma_v = vol_sma_series.iloc[i]
                vol_ratio = float(c["volume"] / sma_v) if not pd.isna(sma_v) and sma_v > 0 else 1.0

            candles_summary.append(
                f"[{c_type}] O:{c['open']:.1f} C:{c['close']:.1f} (Body:{body_ratio:.2f}, LW:{lw:.2f}, UW:{uw:.2f}, Vol:{vol_ratio:.2f}x)"
            )

        last_candle = df_32.iloc[-1]
        hours = len(df_32) * 15 / 60

        macro_str = f"""
Macro Structural Anchors:
- 4H Trend: {macro_info.get("trend_4h", "NEUTRAL")}
- 1H EMA50 Distance: {macro_info.get("ema50_1h_dist_pct", 0.0):.2f}%
- ATR Volatility Percentile: {macro_info.get("atr_percentile", 50.0):.1f}%
- 24H High/Low Range: {macro_info.get("range_24h_pct", 0.0):.2f}%
"""

        state_text = f"""Market Event Scout:
Asset: {pair} (Futures 10x)
Timestamp: {timestamp}
Current Price: {last_candle["close"]:.1f}
Lookback Window: {len(df_32)} candles ({hours:.1f} hours of price action)
Radar Event: {event_trigger}
{macro_str}
32-Candle Trajectory Sequence (oldest to newest):
""" + "\n".join(candles_summary)

        req_body = {
            "model": self.external_model_version,
            "state": state_text,
            "questions": {
                "suggested_action": {
                    "type": "choice",
                    "instructions": (
                        "What is the optimal high-expectancy trend-following action for this sequence? "
                        "Prefer continuation with the dominant trend. Do not recommend mean-reversion bottoms or tops."
                    ),
                    "criteria": {
                        "enter_short": "Clean breakdown or waterfall continuation with expanding sell volume in a bearish regime",
                        "enter_long": "Clean upside breakout or impulsive bullish continuation with expanding buy volume in a bullish regime",
                        "no_trade": "Erratic chop, opposing rejection wicks, weak follow-through, or ambiguous range-bound action",
                    },
                },
                "setup_type": {
                    "type": "choice",
                    "instructions": "What market structure phenomenon does this sequence best represent?",
                    "criteria": {
                        "waterfall_collapse": "Rapid sequential lower highs and lower lows with expanding bearish volume",
                        "breakout_expansion": "Impulsive upside break of structure with expanding bullish volume and limited upper rejection",
                        "absorption_trap": "Price pushed into a new extreme but met with heavy absorbing liquidity / rejection",
                        "trend_pullback": "Controlled counter-trend pause inside an existing larger trend",
                        "consolidation_noise": "Sideways churn without distinct edge",
                    },
                },
            },
        }

        try:
            data = json.dumps(req_body).encode("utf-8")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"Freqtrade-Jev-Hunter/{HUNTER_ENGINE_VERSION}",
            }
            req = urllib.request.Request(self.api_url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))

            # Strict schema validation: Root must contain answers
            if not isinstance(res, dict) or "answers" not in res:
                logger.warning(f"[JevHunterEngine] API invalid root schema: {res}")
                return {
                    "action": JevHunterAction.NO_TRADE.value,
                    "setup_type": JevSetupType.UNKNOWN.value,
                    "confidence": 0.0,
                    "status": JevHunterStatus.INVALID_RESPONSE.value,
                    "source": "external",
                    "engine_version": HUNTER_ENGINE_VERSION,
                    "error": "Missing 'answers' root field in API response",
                }

            ans = res.get("answers", {})
            sug_action = ans.get("suggested_action")
            setup_info = ans.get("setup_type")

            # Requires both suggested_action and setup_type dicts
            if not isinstance(sug_action, dict) or not isinstance(setup_info, dict):
                logger.warning(f"[JevHunterEngine] API missing required answer objects: {ans}")
                return {
                    "action": JevHunterAction.NO_TRADE.value,
                    "setup_type": JevSetupType.UNKNOWN.value,
                    "confidence": 0.0,
                    "status": JevHunterStatus.INVALID_RESPONSE.value,
                    "source": "external",
                    "engine_version": HUNTER_ENGINE_VERSION,
                    "error": "suggested_action or setup_type missing or invalid",
                }

            action_raw = sug_action.get("choice")
            probs = sug_action.get("probabilities")
            setup_raw = setup_info.get("choice")

            # Validate choices and probabilities
            if (
                not action_raw
                or not isinstance(action_raw, str)
                or action_raw.lower() not in ("enter_short", "enter_long", "no_trade")
                or not isinstance(probs, dict)
                or not setup_raw
                or not isinstance(setup_raw, str)
                or setup_raw.lower() not in [e.value for e in JevSetupType]
            ):
                logger.warning(f"[JevHunterEngine] Invalid choice or probabilities format in {ans}")
                return {
                    "action": JevHunterAction.NO_TRADE.value,
                    "setup_type": JevSetupType.UNKNOWN.value,
                    "confidence": 0.0,
                    "status": JevHunterStatus.INVALID_RESPONSE.value,
                    "source": "external",
                    "engine_version": HUNTER_ENGINE_VERSION,
                    "error": "Invalid choice or probabilities format",
                }

            action_str = action_raw.lower()
            if action_str not in probs:
                logger.warning(
                    f"[JevHunterEngine] Probability missing for chosen action '{action_str}' in {probs}"
                )
                return {
                    "action": JevHunterAction.NO_TRADE.value,
                    "setup_type": JevSetupType.UNKNOWN.value,
                    "confidence": 0.0,
                    "status": JevHunterStatus.INVALID_RESPONSE.value,
                    "source": "external",
                    "engine_version": HUNTER_ENGINE_VERSION,
                    "error": f"Probability missing for action '{action_str}'",
                }

            try:
                raw_prob = float(probs[action_str])
            except (ValueError, TypeError):
                raw_prob = -1.0

            if math.isnan(raw_prob) or raw_prob < 0.0 or raw_prob > 1.0:
                logger.warning(
                    f"[JevHunterEngine] Action probability out of bounds [0.0, 1.0]: {raw_prob}"
                )
                return {
                    "action": JevHunterAction.NO_TRADE.value,
                    "setup_type": JevSetupType.UNKNOWN.value,
                    "confidence": 0.0,
                    "status": JevHunterStatus.INVALID_RESPONSE.value,
                    "source": "external",
                    "engine_version": HUNTER_ENGINE_VERSION,
                    "error": f"Invalid probability value: {raw_prob}",
                }

            action = action_str
            setup = setup_raw.lower()
            confidence = round(raw_prob, 2)
            status = (
                JevHunterStatus.CONFIRMED.value
                if action in ("enter_short", "enter_long")
                else JevHunterStatus.REJECTED.value
            )

            # Prefer immutable backend model. Never durable-cache under a mutable alias.
            backend_model = res.get("model_version") or res.get("model")
            backend_model_str = str(backend_model).strip() if backend_model else ""
            configured = self.external_model_version

            if backend_model_str and not is_mutable_model_id(backend_model_str):
                actual_model_version = backend_model_str
                can_cache = True
                self._remember_alias_resolution(configured, actual_model_version)
            elif backend_model_str and is_mutable_model_id(backend_model_str):
                # Backend echoed a floating alias — treat as missing immutable identity.
                actual_model_version = backend_model_str
                can_cache = False
                logger.info(
                    "[JevHunterEngine] Backend returned mutable model alias %r; "
                    "external verdict will not be cached.",
                    backend_model_str,
                )
            else:
                actual_model_version = configured
                if self.missing_model_policy == "use_configured" and not is_mutable_model_id(
                    configured
                ):
                    can_cache = True
                else:
                    can_cache = False
                    logger.info(
                        "[JevHunterEngine] Backend omitted immutable model_version "
                        "(configured=%r, policy=%s). Result will not be cached.",
                        configured,
                        self.missing_model_policy,
                    )

            logger.info(
                f"[JevHunterEngine] Evaluated candidate with model_version: {actual_model_version}"
            )

            verdict = {
                "action": action,
                "setup_type": setup,
                "confidence": confidence,
                "status": status,
                "source": "external",
                "model_version": actual_model_version,
                "engine_version": HUNTER_ENGINE_VERSION,
                "raw_response": res,
            }

            # Persist only under immutable model identity + content-stable payload_hash
            if can_cache:
                self.cache.put(
                    pair,
                    timestamp,
                    action,
                    setup,
                    confidence,
                    status,
                    res,
                    payload_hash=payload_hash,
                    model_version=actual_model_version,
                    engine_version=HUNTER_ENGINE_VERSION,
                )
            return verdict
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            logger.warning(f"[JevHunterEngine] API HTTP {e.code}: {body}")
            return {
                "action": JevHunterAction.NO_TRADE.value,
                "setup_type": JevSetupType.UNKNOWN.value,
                "confidence": 0.0,
                "status": JevHunterStatus.UNAVAILABLE.value,
                "source": "external",
                "engine_version": HUNTER_ENGINE_VERSION,
                "error": f"HTTP {e.code}: {body}",
            }
        except urllib.error.URLError as e:
            logger.warning(f"[JevHunterEngine] API Network/Timeout error: {e}")
            return {
                "action": JevHunterAction.NO_TRADE.value,
                "setup_type": JevSetupType.UNKNOWN.value,
                "confidence": 0.0,
                "status": JevHunterStatus.UNAVAILABLE.value,
                "source": "external",
                "engine_version": HUNTER_ENGINE_VERSION,
                "error": str(e),
            }
        except Exception as e:
            logger.warning(f"[JevHunterEngine] API response parsing error: {e}")
            return {
                "action": JevHunterAction.NO_TRADE.value,
                "setup_type": JevSetupType.UNKNOWN.value,
                "confidence": 0.0,
                "status": JevHunterStatus.INVALID_RESPONSE.value,
                "source": "external",
                "engine_version": HUNTER_ENGINE_VERSION,
                "error": str(e),
            }
