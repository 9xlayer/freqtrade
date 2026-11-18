"""
Jev Decision Engine & Candle Trajectory Extractor
================================================
Cung cấp cầu nối AI Decision Layer cho Freqtrade & AI Agent Lens:
1. CandleTrajectoryExtractor: Đóng gói chuỗi 32 nến thời gian thực thành
   vector hành vi giá (Price Action Trajectory) chuẩn hóa kèm bối cảnh MTF.
2. JevDecisionEngine: Mô hình quyết định System-1 hỗ trợ cả:
   - Native Quant System-1: Bộ phân tích cấu trúc chuỗi giá offline siêu tốc (<1ms)
   - External Mode: Gửi HTTP JSON Payload tới Jev API / LLM endpoint (khi được cấu hình tường minh)
   - Backtest Mode: Đảm bảo tính tất định (deterministic), không gọi network API khi backtest.
"""

import os
import json
import sqlite3
import hashlib
import logging
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
from enum import Enum
import math
import pandas as pd
from pandas import DataFrame

logger = logging.getLogger(__name__)

# Version hub: strategies.mtf_sniper.__init__ (single bump site).
from strategies.mtf_sniper import (
    DECISION_ENGINE_VERSION as ENGINE_VERSION,
    DECISION_MODEL_VERSION_EXTERNAL_DEFAULT,
    DECISION_MODEL_VERSION_NATIVE as MODEL_VERSION_NATIVE,
    DECISION_PROMPT_VERSION_EXTERNAL as PROMPT_VERSION_EXTERNAL,
    DECISION_PROMPT_VERSION_NATIVE as PROMPT_VERSION_NATIVE,
)

MODEL_VERSION_EXTERNAL = os.environ.get(
    "JEV_DECISION_MODEL_VERSION", DECISION_MODEL_VERSION_EXTERNAL_DEFAULT
)


def _parse_unit_interval(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Parse a finite float in [0, 1]. Returns default/None on invalid input."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or math.isinf(parsed) or parsed < 0.0 or parsed > 1.0:
        return default
    return parsed


def _external_confirm_threshold() -> Optional[float]:
    """
    External confirm threshold (native uses 0.70).
    Must be finite and inside [0, 1]; invalid env values yield None (fail closed).
    """
    raw = os.environ.get("JEV_CONFIRM_THRESHOLD", "0.60")
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
        "payload_hash": str(payload_hash or ""),
        "model_version": str(model_version or ""),
        "engine_version": str(engine_version or ""),
        "prompt_version": str(prompt_version or ""),
        "confirm_threshold": str(confirm_threshold),
        "fail_policy": str(fail_policy or ""),
        "mode": str(mode or ""),
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
    decision: Union[str, JevDecision],
    side: Union[str, JevSide] = JevSide.NONE,
    confidence: float = 0.0,
    market_regime: Union[str, JevMarketRegime] = JevMarketRegime.UNKNOWN,
    reasons: Optional[List[str]] = None,
    source: str = "native",
    model_version: str = MODEL_VERSION_NATIVE,
    engine_version: str = ENGINE_VERSION,
    status: Union[str, JevStatus] = JevStatus.SUCCESS,
    raw_response: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Đảm bảo 100% verdict tuân thủ Universal Decision Contract Schema:
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
        conf_float = float(confidence)
        if math.isnan(conf_float) or math.isinf(conf_float):
            conf_float = 0.0
    except (TypeError, ValueError):
        conf_float = 0.0
    conf_bounded = max(0.0, min(1.0, round(conf_float, 2)))

    stat_str = str(getattr(status, "value", status)).upper()
    if stat_str not in ("SUCCESS", "UNAVAILABLE", "INVALID"):
        stat_str = JevStatus.INVALID.value

    dec_str = str(getattr(decision, "value", decision)).upper()
    # Chỉ được CONFIRM nếu trạng thái là SUCCESS và quyết định rõ ràng là CONFIRM
    final_decision = JevDecision.CONFIRM.value if (dec_str == "CONFIRM" and stat_str == "SUCCESS") else JevDecision.VETO.value

    side_str = str(getattr(side, "value", side)).upper()
    final_side = side_str if side_str in ("LONG", "SHORT") else JevSide.NONE.value

    regime_str = str(getattr(market_regime, "value", market_regime)).upper()

    return {
        "decision": final_decision,
        "side": final_side,
        "confidence": conf_bounded,
        "market_regime": regime_str,
        "reasons": list(reasons or []),
        "source": str(source),
        "model_version": str(model_version),
        "engine_version": str(engine_version),
        "status": stat_str,
        "raw_response": dict(raw_response or {}),
    }


class CandleTrajectoryExtractor:
    """
    Trích xuất chuỗi nến thời gian thực và bối cảnh thị trường thành JSON Snapshot
    cho mô hình AI ra quyết định.
    Đảm bảo an toàn toán học: chống chia cho 0, chống NaN/Inf, kiểm tra bắt buộc cột.
    """

    REQUIRED_COLS = ["open", "high", "low", "close", "volume"]

    @classmethod
    def extract_sequence(cls, df: DataFrame, lookback: int = 32) -> List[Dict[str, float]]:
        """
        Trích xuất chuỗi N nến gần nhất thành chuỗi đặc trưng động lượng.
        Index của nến chạy từ -(lookback-1) đến 0 (nến kích hoạt cuối cùng).
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

        # Làm sạch và chống NaN/Inf bằng ffill & bfill
        recent = recent.ffill().bfill()

        # Thống nhất chuẩn hóa Volume: dùng rolling 20 SMA hoặc trung bình recent
        vol_col = recent["volume"].fillna(0.0)
        vol_mean = float(vol_col.rolling(20, min_periods=1).mean().iloc[-1]) if len(vol_col) > 0 else 1.0
        if vol_mean <= 0.0 or math.isnan(vol_mean):
            vol_mean = 1.0

        sequence = []
        n_candles = len(recent)

        for i, (_, row) in enumerate(recent.iterrows()):
            try:
                c = float(row["close"])
                o = float(row["open"])
                h = float(row["high"])
                l = float(row["low"])
                v = float(row["volume"])
            except (ValueError, TypeError):
                continue

            if math.isnan(c) or math.isnan(o) or math.isnan(h) or math.isnan(l):
                continue

            # Anti-division by zero cho total_range
            total_range = max(h - l, 1e-6)
            body = abs(c - o)

            pct_change = round(((c - o) / o) * 100, 3) if o > 0 else 0.0
            range_pct = round(((h - l) / c) * 100, 3) if c > 0 else 0.0
            body_ratio = round(body / total_range, 3)
            upper_wick = round((h - max(o, c)) / total_range, 3)
            lower_wick = round((min(o, c) - l) / total_range, 3)
            close_pos = round((c - l) / total_range, 3)
            vol_ratio = round(v / vol_mean, 2) if vol_mean > 0 else 1.0

            rsi_val = row.get("rsi", 50.0)
            try:
                rsi = round(float(rsi_val), 1)
                if math.isnan(rsi):
                    rsi = 50.0
            except (ValueError, TypeError):
                rsi = 50.0

            # Index chạy từ -(n_candles - 1) đến 0 (ví dụ: -31 đến 0 cho 32 nến)
            candle_idx = i - n_candles + 1

            sequence.append({
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
            })
        return sequence

    @classmethod
    def extract_macro_context(cls, df: DataFrame) -> Dict[str, Any]:
        """
        Trích xuất bối cảnh đa khung thời gian từ hàng cuối cùng.
        Thống nhất định dạng key để External API và Native Quant dùng chung.
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
        macd_1h = float(last.get("macdhist_1h", 0.0)) if not pd.isna(last.get("macdhist_1h")) else 0.0
        rsi_1h = float(last.get("rsi_1h", 50.0)) if not pd.isna(last.get("rsi_1h")) else 50.0

        # 4H context
        is_bull_4h = bool(last.get("is_bull_wave_4h", 0) == 1)
        is_bear_4h = bool(last.get("is_bear_wave_4h", 0) == 1)
        adx_4h = float(last.get("adx_4h", 20.0)) if not pd.isna(last.get("adx_4h")) else 20.0

        # 1D context
        floor_20d = float(last.get("swing_low_20d_1d", c)) if not pd.isna(last.get("swing_low_20d_1d")) else c
        floor_dist = round(((c - floor_20d) / floor_20d) * 100, 2) if floor_20d > 0 else 0.0

        # AI Agent Lens scores
        long_score = float(last.get("long_score", 50.0)) if not pd.isna(last.get("long_score")) else 50.0
        short_score = float(last.get("short_score", 50.0)) if not pd.isna(last.get("short_score")) else 50.0
        atr_pct = float(last.get("atr_pct", 50.0)) if not pd.isna(last.get("atr_pct")) else 50.0

        return {
            "current_price": c,
            "ema_50_1h_dist_pct": ema_50_1h_dist,
            "macdhist_1h": macd_1h,
            "rsi_1h": rsi_1h,
            "trend_4h": "BULL" if is_bull_4h else ("BEAR" if is_bear_4h else "NEUTRAL"),
            "adx_4h": adx_4h,
            "floor_20d_dist_pct": floor_dist,
            "long_score": long_score,
            "short_score": short_score,
            "atr_percentile": atr_pct,
        }

    @classmethod
    def build_payload(cls, df: DataFrame, pair: str, side: str, lookback: int = 32) -> Dict[str, Any]:
        """
        Đóng gói toàn bộ thông điệp chuẩn bị gửi cho Jev Decision Engine.
        Đảm bảo bao gồm đầy đủ:
        - current_price
        - timeframe ("15m")
        - timestamp chuẩn hóa
        - side ("LONG" / "SHORT")
        - pair
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
                "macro_context": {},
                "trajectory_sequence": [],
                "payload_hash": "",
            }

        last_row = df.iloc[-1]
        ts = str(last_row.get("date", ""))
        current_price = float(last_row.get("close", 0.0))
        macro_ctx = cls.extract_macro_context(df)
        traj_seq = cls.extract_sequence(df, lookback=lookback)

        # Tính payload_hash tất định cho caching đại diện cho toàn bộ chuỗi nến và macro context
        traj_repr = json.dumps(traj_seq, sort_keys=True, default=str)
        macro_repr = json.dumps(macro_ctx, sort_keys=True, default=str)
        traj_hash = hashlib.sha256(traj_repr.encode("utf-8")).hexdigest()[:12]
        macro_hash = hashlib.sha256(macro_repr.encode("utf-8")).hexdigest()[:12]
        hash_seed = f"{pair}_{ts}_15m_{side.upper()}_{current_price:.4f}_{traj_hash}_{macro_hash}_{ENGINE_VERSION}"
        payload_hash = hashlib.sha256(hash_seed.encode("utf-8")).hexdigest()[:16]

        return {
            "pair": pair,
            "timestamp": ts,
            "side": side.upper(),
            "timeframe": "15m",
            "current_price": current_price,
            "macro_context": macro_ctx,
            "trajectory_sequence": traj_seq,
            "payload_hash": payload_hash,
        }


class JevDecisionEngine:
    """
    Bộ phán quyết AI System-1:
    - Chế độ rõ ràng thông qua biến môi trường JEV_MODE (native | external | backtest).
    - Mặc định: 'native' (không bao giờ thực hiện HTTP request, chạy 100% cục bộ <1ms).
    - External Mode: Chỉ gọi API khi JEV_MODE='external' và có JEV_API_KEY.
    - Caching SQLite thông minh, đa tiến trình an toàn (WAL mode, busy_timeout=5000), không cache lỗi.
    - Tất định hoàn toàn (deterministic) trong môi trường Backtest.
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_db: str = "user_data/jev_hunter_cache.sqlite",
        mode: Optional[str] = None,
        fail_policy: Optional[str] = None,
    ):
        self.cache_db = cache_db
        self._load_env()
        self._init_cache()

        # Mode is controlled only by JEV_MODE (native | external | backtest); default native.
        configured_mode = (mode or os.environ.get("JEV_MODE", "native")).lower()
        self.mode = configured_mode if configured_mode in ("native", "external", "backtest") else "native"

        self.fail_policy = (fail_policy or os.environ.get("JEV_FAIL_POLICY", "reject")).lower()
        self.api_url = api_url or os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
        self.api_key = api_key or os.environ.get("JEV_API_KEY")
        self.engine_version = ENGINE_VERSION
        self.external_model_version = os.environ.get("JEV_DECISION_MODEL_VERSION", MODEL_VERSION_EXTERNAL)
        self.prompt_version = (
            PROMPT_VERSION_EXTERNAL if self.mode == "external" else PROMPT_VERSION_NATIVE
        )

        logger.info(
            f"JevDecisionEngine initialized. Mode={self.mode}, FailPolicy={self.fail_policy}, "
            f"EngineVer={ENGINE_VERSION}, PromptVer={self.prompt_version}"
        )

    def _load_env(self):
        if not os.environ.get("JEV_API_KEY"):
            for env_path in [Path(".env"), Path(__file__).resolve().parents[2] / ".env"]:
                if env_path.exists():
                    try:
                        with open(env_path, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#") and "=" in line:
                                    k, v = line.split("=", 1)
                                    k, v = k.strip(), v.strip().strip('"').strip("'")
                                    if k not in os.environ:
                                        os.environ[k] = v
                    except Exception:
                        pass

    def _init_cache(self):
        try:
            db_dir = os.path.dirname(self.cache_db)
            if db_dir and db_dir not in (".", ""):
                os.makedirs(db_dir, exist_ok=True)
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
                    r[1] for r in conn.execute("PRAGMA table_info(jev_gatekeeper_verdicts)").fetchall()
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
            return str(self.external_model_version)
        return MODEL_VERSION_NATIVE

    def _cache_threshold_token(self) -> str:
        if self.mode != "external":
            return "native"
        threshold = _external_confirm_threshold()
        if threshold is None:
            return "invalid"
        return f"{threshold:.4f}"

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

    def evaluate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Thực hiện đánh giá và đưa ra phán quyết.
        1. Kiểm tra tính hợp lệ của Payload.
        2. Kiểm tra Cache SQLite (chỉ chấp nhận cache có status SUCCESS).
        3. Phân luồng theo Mode (native, backtest, external).
        4. Áp dụng Fail-Safe Policy nghiêm ngặt khi có lỗi.
        """
        if not payload or not isinstance(payload, dict):
            return make_verdict(
                decision=JevDecision.VETO,
                status=JevStatus.INVALID,
                market_regime=JevMarketRegime.INSUFFICIENT_DATA,
                reasons=["Empty or invalid payload structure"],
                source="validator"
            )

        pair = payload.get("pair", "UNKNOWN")
        ts = payload.get("timestamp", "")
        side = payload.get("side", JevSide.NONE.value).upper()
        p_hash = payload.get("payload_hash", "")
        seq = payload.get("trajectory_sequence", [])

        # Kiểm tra tính sẵn sàng của dữ liệu
        if not seq or len(seq) < 5:
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.INSUFFICIENT_DATA,
                status=JevStatus.INVALID,
                reasons=[f"Insufficient trajectory sequence ({len(seq)} candles, min 5 required)"],
                source="validator"
            )

        # 1. Tra cứu Cache SQLite — full identity (payload + model + prompt + threshold + policy + mode)
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
                            and cached_prompt == getattr(self, "prompt_version", PROMPT_VERSION_NATIVE)
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

        # 2. Xử lý theo Mode
        if self.mode == "native" or self.mode == "backtest":
            verdict = self._evaluate_native_sequence(payload)
        elif self.mode == "external":
            if not self.api_key:
                logger.warning("External mode active but JEV_API_KEY not configured. Enforcing fail policy.")
                if self.fail_policy == "fallback_native":
                    verdict = self._evaluate_native_sequence(payload)
                    verdict["source"] = "native_fallback"
                else:
                    return make_verdict(
                        decision=JevDecision.VETO,
                        side=side,
                        status=JevStatus.UNAVAILABLE,
                        market_regime=JevMarketRegime.UNKNOWN,
                        reasons=["External API Key missing, rejected by fail policy"],
                        source="policy_gate"
                    )
            else:
                verdict = self._call_external_jev(payload)
                if verdict.get("status") != JevStatus.SUCCESS.value:
                    if self.fail_policy == "fallback_native":
                        logger.info("External API unavailable, falling back to Native Quant Evaluator.")
                        verdict = self._evaluate_native_sequence(payload)
                        verdict["source"] = "native_fallback"
                    elif self.fail_policy in ("allow", "safe"):
                        logger.info("External API unavailable, fail policy allows pass-through.")
                    else:
                        logger.warning("External API failed and fail policy is reject -> VETO.")
                        verdict["decision"] = JevDecision.VETO.value
        else:
            verdict = self._evaluate_native_sequence(payload)

        # 3. Ghi vào Cache SQLite (chỉ khi SUCCESS + identity hợp lệ)
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

    def _call_external_jev(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Gửi payload tới endpoint Jev System-1 Cloud (TypeSafe).
        Khắc phục hoàn toàn lỗi mapping: current_price và ema_50_1h_dist_pct.
        Xử lý timeout, HTTP error, malformed JSON thành UNAVAILABLE dứt khoát.
        """
        side = payload.get("side", JevSide.NONE.value).upper()
        pair = payload.get("pair", "UNKNOWN")
        current_price = payload.get("current_price", 0.0)
        seq = payload.get("trajectory_sequence", [])
        macro = payload.get("macro_context", {})

        candles_summary = []
        for i, c in enumerate(seq[-6:]):
            c_type = "trigger" if i == len(seq[-6:]) - 1 else f"T-{len(seq[-6:]) - 1 - i}"
            candles_summary.append(
                f"[{c_type}] body:{c.get('body_ratio', 0):.2f}, "
                f"lw:{c.get('lower_wick', 0):.2f}, uw:{c.get('upper_wick', 0):.2f}, "
                f"vol:{c.get('vol_ratio', 0):.2f}x, rsi:{c.get('rsi', 0):.1f}"
            )
        candles_str = "\n".join(candles_summary)

        # Sửa mapping chuẩn: ema_50_1h_dist_pct
        ema_dist = macro.get("ema_50_1h_dist_pct", macro.get("ema50_1h_dist_pct", 0.0))

        state_text = f"""Asset: {pair} (Futures 10x)
Proposed Trade: {side}
Current Price: {current_price}

Recent Candle Trajectory:
{candles_str}

Macro & Filter Context:
- 4H Trend: {macro.get('trend_4h', 'NEUTRAL')}
- 1H EMA50 Distance: {ema_dist:.2f}%
- ATR Volatility Percentile: {macro.get('atr_percentile', 50.0):.1f}%
- AI Lens Confluence Score: {macro.get('short_score' if side == 'SHORT' else 'long_score', 50.0):.1f}
"""

        model_id = getattr(self, "external_model_version", MODEL_VERSION_EXTERNAL)
        req_body = {
            "model": model_id,
            "state": state_text,
            "questions": {
                "confirm_trade": {
                    "type": "noul",
                    "instructions": (
                        f"Does this sequence confirm a high-probability trend-following {side.lower()} continuation "
                        f"with expanding directional pressure and without significant opposing rejection? "
                        f"Score low if the regime is choppy/ranging or the move lacks follow-through."
                    ),
                },
                "market_regime": {
                    "type": "choice",
                    "instructions": "What is the immediate market regime demonstrated by this candle sequence?",
                    "criteria": {
                        "trend_expansion": "Impulsive breakout or expanding trend with volume confirmation",
                        "pullback_setup": "Orderly retracement towards dynamic support/resistance with declining volume",
                        "choppy_ranging": "Overlapping candles, large opposing rejection wicks, or erratic volatility"
                    }
                }
            }
        }

        try:
            data = json.dumps(req_body).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "User-Agent": f"Freqtrade-Jev/{ENGINE_VERSION}"
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            timeout_val = float(os.environ.get("JEV_API_TIMEOUT", "2.0"))
            req = urllib.request.Request(self.api_url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                result = json.loads(resp.read().decode("utf-8"))

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

            regime_choice = str(market_regime_ans.get("choice") or "unknown")
            regime = regime_choice.upper()
            actual_model = result.get("model") if isinstance(result.get("model"), str) else model_id
            reasons = [
                f"Jev Cloud probability: {noul_conf:.2f}",
                f"Threshold: {threshold:.2f}",
                f"Regime: {regime}",
                f"Model: {actual_model}",
            ]

            # Hard veto chop/range even if noul clears the numeric threshold.
            if regime in ("CHOPPY_RANGING", "CHOPPY", "RANGING"):
                decision = JevDecision.VETO.value
                reasons.append("Vetoed by choppy/ranging regime")
            elif noul_conf >= threshold:
                decision = JevDecision.CONFIRM.value
            else:
                decision = JevDecision.VETO.value
                reasons.append("Below confirm threshold")

            return make_verdict(
                decision=decision,
                side=side,
                confidence=noul_conf,
                market_regime=regime,
                reasons=reasons,
                source="external",
                model_version=actual_model,
                status=JevStatus.SUCCESS,
                raw_response=result,
            )
        except Exception as e:
            return make_verdict(
                decision=JevDecision.VETO,
                side=side,
                confidence=0.0,
                market_regime=JevMarketRegime.UNKNOWN,
                reasons=[f"External Jev API call failed: {str(e)}"],
                source="external",
                status=JevStatus.UNAVAILABLE,
                raw_response={"error": str(e)}
            )

    def _evaluate_native_sequence(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Native Quant System-1 Evaluator:
        Phân tích chuỗi 32 nến thời gian thực (đảm bảo >= 5 nến):
        1. Nến kích hoạt (Impulse / Adverse rejection wick).
        2. Pha nén biên độ (Pre-breakout Compression).
        3. Dòng lệnh liên tục (Sequential Order Flow closes).
        4. Hợp lưu đa khung vĩ mô (Macro Confluence).
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
                status=JevStatus.INVALID
            )

        reasons = []
        confidence = 0.50

        # --- A. Phân tích nến kích hoạt (Nến cuối cùng - idx 0) ---
        last_candle = seq[-1]
        is_impulse = (last_candle.get("body_ratio", 0) >= 0.55) and (last_candle.get("vol_ratio", 0) >= 1.20)
        has_adverse_wick = (
            (last_candle.get("lower_wick", 0) >= 0.35) if side == "SHORT" 
            else (last_candle.get("upper_wick", 0) >= 0.35)
        )

        if has_adverse_wick:
            confidence -= 0.25
            reasons.append("Adverse wick absorption detected on trigger candle")
        elif is_impulse:
            confidence += 0.15
            reasons.append("Impulsive breakout candle with expanding volume")

        # --- B. Phân tích pha nén biên độ (Pre-breakout Compression) ---
        prior_candles = seq[-5:-1]
        if prior_candles:
            avg_prior_vol = sum(c.get("vol_ratio", 1.0) for c in prior_candles) / len(prior_candles)
            avg_prior_range = sum(c.get("range_pct", 1.0) for c in prior_candles) / len(prior_candles)
            if avg_prior_vol < 1.0 and avg_prior_range < 0.6:
                confidence += 0.10
                reasons.append("Clean pre-breakout volatility compression confirmed")

        # --- C. Phân tích dòng lệnh liên tục (Sequential Order Flow) ---
        favorable_closes = sum(
            1 for c in seq[-5:] 
            if (c.get("close_pos", 0.5) <= 0.40 if side == "SHORT" else c.get("close_pos", 0.5) >= 0.60)
        )
        if favorable_closes >= 3:
            confidence += 0.10
            reasons.append(f"Directional order flow aligned ({favorable_closes}/5 candles favorable)")

        # --- D. Hợp lưu đa khung vĩ mô (Macro Context) ---
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

        # Phạt nặng nếu ATR ở vùng bão quét biến động cực đại (>90%)
        if atr_pct >= 90.0:
            confidence -= 0.20
            reasons.append(f"Extreme ATR volatility risk ({atr_pct:.1f}%)")

        confidence = max(0.0, min(1.0, round(confidence, 2)))

        # Native Heuristic threshold: >= 0.70 là CONFIRM, < 0.70 là VETO
        decision = JevDecision.CONFIRM.value if confidence >= 0.70 else JevDecision.VETO.value
        regime = (
            JevMarketRegime.TREND_EXPANSION.value if is_impulse 
            else (JevMarketRegime.CHOPPY_RANGING.value if confidence < 0.60 else JevMarketRegime.PULLBACK_SETUP.value)
        )

        return make_verdict(
            decision=decision,
            side=side,
            confidence=confidence,
            market_regime=regime,
            reasons=reasons,
            source="native",
            model_version=MODEL_VERSION_NATIVE,
            status=JevStatus.SUCCESS
        )
