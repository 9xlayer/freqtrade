"""
Jev Decision Engine & Candle Trajectory Extractor
================================================
Cung cấp cầu nối AI Decision Layer cho Freqtrade & AI Agent Lens:
1. CandleTrajectoryExtractor: Đóng gói chuỗi 15-20 nến thời gian thực thành
   vector hành vi giá (Price Action Trajectory) chuẩn hóa kèm bối cảnh MTF.
2. JevDecisionEngine: Mô hình quyết định System-1 hỗ trợ cả:
   - External Mode: Gửi HTTP JSON Payload tới Jev API / LLM endpoint (nếu cấu hình)
   - Native Quant System-1: Bộ phân tích cấu trúc chuỗi giá offline siêu tốc (<1ms)
"""

import os
import json
import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
import pandas as pd
from pandas import DataFrame

logger = logging.getLogger(__name__)


class CandleTrajectoryExtractor:
    """
    Trích xuất chuỗi nến thời gian thực và bối cảnh thị trường thành JSON Snapshot
    cho mô hình AI ra quyết định.
    """

    @staticmethod
    def extract_sequence(df: DataFrame, lookback: int = 32) -> List[Dict[str, float]]:
        """
        Trích xuất chuỗi N nến gần nhất thành chuỗi đặc trưng động lượng.
        """
        if len(df) < lookback:
            recent = df.copy()
        else:
            recent = df.iloc[-lookback:].copy()

        sequence = []
        vol_mean = recent["volume"].mean() if recent["volume"].mean() > 0 else 1.0

        for i, (_, row) in enumerate(recent.iterrows()):
            total_range = float(row.get("total_range", 0.0))
            if total_range <= 0:
                total_range = max(float(row["high"] - row["low"]), 0.0001)

            body = float(row.get("body", abs(row["close"] - row["open"])))
            c = float(row["close"])
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])

            pct_change = round(((c - o) / o) * 100, 3) if o > 0 else 0.0
            range_pct = round(((h - l) / c) * 100, 3) if c > 0 else 0.0
            body_ratio = round(body / total_range, 3)
            upper_wick = round((h - max(o, c)) / total_range, 3)
            lower_wick = round((min(o, c) - l) / total_range, 3)
            close_pos = round((c - l) / total_range, 3)
            vol_ratio = round(float(row["volume"]) / vol_mean, 2)
            rsi = round(float(row.get("rsi", 50.0)), 1)

            sequence.append({
                "idx": i - len(recent) + 1,  # -14 to 0
                "pct_change": pct_change,
                "range_pct": range_pct,
                "body_ratio": body_ratio,
                "upper_wick": upper_wick,
                "lower_wick": lower_wick,
                "close_pos": close_pos,
                "vol_ratio": vol_ratio,
                "rsi": rsi,
            })
        return sequence

    @staticmethod
    def extract_macro_context(df: DataFrame) -> Dict[str, Any]:
        """
        Trích xuất bối cảnh đa khung thời gian từ hàng cuối cùng.
        """
        last = df.iloc[-1]
        c = float(last["close"])

        # 1H context
        ema_50_1h = float(last.get("ema_50_1h", c))
        ema_50_1h_dist = round(((c - ema_50_1h) / ema_50_1h) * 100, 2) if ema_50_1h > 0 else 0.0
        macd_1h = float(last.get("macdhist_1h", 0.0))
        rsi_1h = float(last.get("rsi_1h", 50.0))

        # 4H context
        is_bull_4h = bool(last.get("is_bull_wave_4h", 0) == 1)
        is_bear_4h = bool(last.get("is_bear_wave_4h", 0) == 1)
        adx_4h = float(last.get("adx_4h", 20.0))

        # 1D context
        floor_20d = float(last.get("swing_low_20d_1d", c))
        floor_dist = round(((c - floor_20d) / floor_20d) * 100, 2) if floor_20d > 0 else 0.0

        # AI Agent Lens scores
        long_score = float(last.get("long_score", 50.0))
        short_score = float(last.get("short_score", 50.0))
        atr_pct = float(last.get("atr_pct", 50.0))

        return {
            "close_price": c,
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
        """
        last_row = df.iloc[-1]
        ts = str(last_row.get("date", ""))
        return {
            "pair": pair,
            "timestamp": ts,
            "side": side.upper(),
            "timeframe": "15m",
            "macro_context": cls.extract_macro_context(df),
            "trajectory_sequence": cls.extract_sequence(df, lookback=lookback),
        }


class JevDecisionEngine:
    """
    Bộ phán quyết AI System-1:
    - Nếu có biến môi trường JEV_API_URL: Gửi request tới external model/agent.
    - Ngược lại: Sử dụng Native Quant Sequence Classifier cục bộ siêu tốc (<1ms).
    """

    def __init__(self, api_url: Optional[str] = None, api_key: Optional[str] = None, cache_db: str = "user_data/jev_hunter_cache.sqlite"):
        self.cache_db = cache_db
        self._init_cache()

        # Auto-load .env if env vars not in process environment
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

        self.api_url = api_url or os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
        self.api_key = api_key or os.environ.get("JEV_API_KEY")

    def _init_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_db), exist_ok=True)
            with sqlite3.connect(self.cache_db) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS jev_gatekeeper_verdicts (
                        pair TEXT,
                        timestamp TEXT,
                        side TEXT,
                        decision TEXT,
                        confidence REAL,
                        market_regime TEXT,
                        raw_response TEXT,
                        PRIMARY KEY (pair, timestamp, side)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.warning(f"Jev Gatekeeper cache init error: {e}")

    def evaluate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Thực hiện đánh giá và đưa ra phán quyết:
        Kiểm tra Cache SQLite trước để không gọi trùng API.
        """
        pair = payload.get("pair", "")
        ts = payload.get("timestamp", "")
        side = payload.get("side", "")
        if pair and ts and side:
            try:
                with sqlite3.connect(self.cache_db) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT decision, confidence, market_regime, raw_response FROM jev_gatekeeper_verdicts WHERE pair=? AND timestamp=? AND side=?",
                        (pair, ts, side)
                    )
                    row = cursor.fetchone()
                    if row:
                        return {
                            "decision": row[0],
                            "confidence": row[1],
                            "market_regime": row[2],
                            "reasons": [f"Loaded from SQLite cache (Gatekeeper: {row[0]})"],
                            "raw_response": json.loads(row[3]) if row[3] else {}
                        }
            except Exception:
                pass

        if self.api_url:
            verdict = self._call_external_jev(payload)
            # Lưu vào cache SQLite nếu thành công
            if pair and ts and side and verdict.get("decision"):
                try:
                    with sqlite3.connect(self.cache_db) as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO jev_gatekeeper_verdicts (pair, timestamp, side, decision, confidence, market_regime, raw_response) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (pair, ts, side, verdict.get("decision"), verdict.get("confidence", 0.5), verdict.get("market_regime", ""), json.dumps(verdict.get("raw_response", {})))
                        )
                        conn.commit()
                except Exception:
                    pass
            return verdict
        return self._evaluate_native_sequence(payload)

    def _call_external_jev(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Gửi payload tới endpoint Jev System-1 Cloud (TypeSafe) hoặc external API.
        """
        try:
            import urllib.request

            # Format request specifically for TypeSafe System One API if pointing to typesafe.ai or /systemone
            if "typesafe.ai" in self.api_url or "/systemone" in self.api_url:
                side = payload.get("side", "LONG").upper()
                pair = payload.get("pair", "UNKNOWN")
                current_price = payload.get("current_price", 0.0)
                seq = payload.get("trajectory_sequence", [])
                macro = payload.get("macro_context", {})

                # Build concise narrative state for Jev
                candles_summary = []
                for i, c in enumerate(seq[-6:]):
                    c_type = "trigger" if i == len(seq[-6:]) - 1 else f"T-{len(seq[-6:]) - 1 - i}"
                    candles_summary.append(
                        f"[{c_type}] body:{c.get('body_ratio',0):.2f}, "
                        f"lw:{c.get('lower_wick',0):.2f}, uw:{c.get('upper_wick',0):.2f}, "
                        f"vol:{c.get('vol_ratio',0):.2f}x, rsi:{c.get('rsi',0):.1f}"
                    )
                candles_str = "\n".join(candles_summary)

                state_text = f"""Asset: {pair} (Futures 10x)
Proposed Trade: {side}
Current Price: {current_price}

Recent Candle Trajectory:
{candles_str}

Macro & Filter Context:
- 4H Trend: {macro.get('trend_4h', 'NEUTRAL')}
- 1H EMA50 Distance: {macro.get('ema50_1h_dist_pct', 0.0):.2f}%
- ATR Volatility Percentile: {macro.get('atr_percentile', 50.0):.1f}%
- AI Lens Confluence Score: {macro.get('short_score' if side == 'SHORT' else 'long_score', 50.0):.1f}
"""

                req_body = {
                    "model": "jev-latest",
                    "state": state_text,
                    "questions": {
                        "confirm_trade": {
                            "type": "noul",
                            "instructions": f"Does the recent price trajectory, candle anatomy, and order flow sequence confirm a high-probability {side.lower()} continuation without significant opposing rejection wick absorption?"
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
                data = json.dumps(req_body).encode("utf-8")
            else:
                data = json.dumps(payload).encode("utf-8")

            headers = {"Content-Type": "application/json", "User-Agent": "Freqtrade-Jev/1.0"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            req = urllib.request.Request(self.api_url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            if "answers" in result:
                answers = result["answers"]
                noul_conf = answers.get("confirm_trade", {}).get("noul", 0.5)
                regime = answers.get("market_regime", {}).get("choice", "trend_expansion")
                # Ngưỡng calibrated cho mô hình xác suất Jev (vùng < 0.25 là rủi ro/nhiễu cao)
                decision = "CONFIRM" if noul_conf >= 0.25 else "VETO"
                return {
                    "decision": decision,
                    "confidence": round(noul_conf, 2),
                    "market_regime": regime.upper(),
                    "reasons": [
                        f"Jev Cloud probability: {noul_conf:.2f}",
                        f"Regime: {regime}",
                        f"Model: {result.get('model', 'jev-latest')}"
                    ],
                    "raw_response": result
                }
            return result
        except Exception as e:
            logger.warning(f"Jev API call failed ({e}), falling back to Native Quant Evaluator.")
            return self._evaluate_native_sequence(payload)

    def _evaluate_native_sequence(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Native Quant System-1 Evaluator:
        Phân tích chuỗi 15 nến thời gian thực để phát hiện:
        1. Cấu trúc sóng: Higher Highs / Lower Lows.
        2. Pha nén (Compression) -> Pha bung (Expansion).
        3. Bẫy râu nến (Absorption / Rejection traps).
        4. Hợp lưu đa khung vĩ mô (Macro confluence).
        """
        side = payload.get("side", "LONG")
        seq = payload.get("trajectory_sequence", [])
        macro = payload.get("macro_context", {})

        if len(seq) < 5:
            return {"decision": "CONFIRM", "confidence": 0.50, "regime": "INSUFFICIENT_DATA", "reason": "Short history"}

        reasons = []
        confidence = 0.50

        # --- A. Phân tích nến kích hoạt (Nến cuối cùng - idx 0) ---
        last_candle = seq[-1]
        prev_candle = seq[-2]

        is_impulse = (last_candle["body_ratio"] >= 0.55) and (last_candle["vol_ratio"] >= 1.20)
        has_adverse_wick = (last_candle["lower_wick"] >= 0.35) if side == "SHORT" else (last_candle["upper_wick"] >= 0.35)

        if has_adverse_wick:
            # Có râu từ chối giá ngược hướng -> Cảnh báo bẫy rút chân
            confidence -= 0.25
            reasons.append("Adverse wick absorption detected on trigger candle")
        elif is_impulse:
            confidence += 0.15
            reasons.append("Impulsive breakout candle with expanding volume")

        # --- B. Phân tích pha nén biên độ (Pre-breakout Compression) ---
        # Kiểm tra 4 nến trước đó (từ -5 đến -2): nếu thân nến nhỏ và volume thấp -> có sự tích lũy nén
        prior_candles = seq[-5:-1]
        avg_prior_vol = sum(c["vol_ratio"] for c in prior_candles) / len(prior_candles)
        avg_prior_range = sum(c["range_pct"] for c in prior_candles) / len(prior_candles)

        if avg_prior_vol < 1.0 and avg_prior_range < 0.6:
            # Nén biên độ thành công trước khi bứt phá
            confidence += 0.10
            reasons.append("Clean pre-breakout volatility compression confirmed")

        # --- C. Phân tích cấu trúc sóng gần nhất (Sequential Order Flow) ---
        # Đếm số nến đóng theo chiều sóng trong 5 nến gần nhất
        favorable_closes = sum(1 for c in seq[-5:] if (c["close_pos"] <= 0.40 if side == "SHORT" else c["close_pos"] >= 0.60))
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

        # Phạt nặng nếu ATR ở vùng cực đại bão quét (>90%)
        if atr_pct >= 90.0:
            confidence -= 0.20
            reasons.append(f"Extreme ATR volatility risk ({atr_pct:.1f}%)")

        confidence = max(0.0, min(1.0, round(confidence, 2)))

        # Ngưỡng phán quyết (Threshold: >= 0.70 là Duyệt, < 0.70 là Phủ quyết)
        decision = "CONFIRM" if confidence >= 0.70 else "VETO"
        regime = "TREND_EXPANSION" if is_impulse else ("CHOPPY_RANGING" if confidence < 0.60 else "PULLBACK_SETUP")

        return {
            "decision": decision,
            "confidence": confidence,
            "market_regime": regime,
            "reasons": reasons,
        }
