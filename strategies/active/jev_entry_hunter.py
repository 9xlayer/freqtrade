"""
Jev Active Entry Hunter (Stage-1 Radar + Stage-2 Jev Decision Engine)
=====================================================================
Kiến trúc AI-Driven Alpha:
1. Stage-1 (MomentumRadar): Quét các sự kiện bùng nổ xung lực / bứt phá trong <1ms.
2. Stage-2 (JevHunterEngine): Jev Cloud đọc 32 nến (8 giờ) + Neo vĩ mô và phán quyết:
   - "enter_short": Sóng xả tự do (waterfall), phá vỡ hỗ trợ.
   - "enter_long": Bùng nổ tăng trưởng hoặc bắt đáy hấp thụ thanh lý.
   - "no_trade": Bẫy rút chân, nhiễu dao động.
3. Persistent SQLite Cache: Tự động lưu phán quyết của Jev theo (pair, timestamp),
   giúp backtest siêu tốc (<0.1ms/nến) và tiết kiệm 100% chi phí token API.
"""

import os
import json
import sqlite3
import logging
import urllib.request
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
import pandas as pd
from pandas import DataFrame

logger = logging.getLogger(__name__)


class MomentumRadar:
    """
    Tầng 1: Radar quét cơ hội (High-Recall Candidate Generator)
    Chạy cục bộ siêu tốc (<1ms), chỉ đánh thức Jev khi thị trường có biến động thực sự.
    """

    @classmethod
    def find_candidates(cls, df: DataFrame, cooldown_bars: int = 12) -> List[Tuple[int, str]]:
        """
        Quét nhanh toàn bộ dataframe để tìm các điểm xung lực đáng chú ý:
        - Cooldown 12 nến (3 tiếng) giữa các lần đánh thức JEV
        - Đột biến Volume (>= 4.0x) kèm biên độ lớn (>= 2.2x ATR)
        - Hoặc phá vỡ ngưỡng 24 giờ (96 nến) kèm Volume >= 2.5x
        """
        vol_sma = df["volume"].rolling(20).mean()
        vol_ratio = df["volume"] / vol_sma
        range_ratio = (df["high"] - df["low"]) / df["atr"]

        is_surge_short = (df["close"] < df["open"]) & (vol_ratio >= 4.0) & (range_ratio >= 2.2)
        is_surge_long = (df["close"] > df["open"]) & (vol_ratio >= 4.0) & (range_ratio >= 2.2)

        l96 = df["low"].rolling(96).min().shift(1)
        h96 = df["high"].rolling(96).max().shift(1)
        is_breakout_short = (df["close"] < l96) & (vol_ratio >= 2.5)
        is_breakout_long = (df["close"] > h96) & (vol_ratio >= 2.5)

        candidates = []
        last_idx = -100
        for i in range(100, len(df)):
            if i - last_idx < cooldown_bars:
                continue
            if is_surge_short.iloc[i]:
                candidates.append((i, "MEGA_SURGE_SHORT"))
                last_idx = i
            elif is_surge_long.iloc[i]:
                candidates.append((i, "MEGA_SURGE_LONG"))
                last_idx = i
            elif is_breakout_short.iloc[i]:
                candidates.append((i, "BREAKOUT_24H_SHORT"))
                last_idx = i
            elif is_breakout_long.iloc[i]:
                candidates.append((i, "BREAKOUT_24H_LONG"))
                last_idx = i

        return candidates


class JevHunterCache:
    """
    Bộ nhớ đệm SQLite cục bộ cho các phán quyết của Jev Cloud.
    """

    def __init__(self, db_path: str = "user_data/jev_hunter_cache.sqlite"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jev_verdicts (
                    pair TEXT,
                    timestamp TEXT,
                    action TEXT,
                    setup_type TEXT,
                    confidence REAL,
                    raw_response TEXT,
                    PRIMARY KEY (pair, timestamp)
                )
            """)
            conn.commit()

    def get(self, pair: str, timestamp: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT action, setup_type, confidence, raw_response FROM jev_verdicts WHERE pair=? AND timestamp=?",
                (pair, str(timestamp))
            )
            row = cursor.fetchone()
            if row:
                return {
                    "action": row[0],
                    "setup_type": row[1],
                    "confidence": row[2],
                    "raw_response": json.loads(row[3]) if row[3] else {}
                }
        return None

    def put(self, pair: str, timestamp: str, action: str, setup_type: str, confidence: float, raw_response: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jev_verdicts (pair, timestamp, action, setup_type, confidence, raw_response) VALUES (?, ?, ?, ?, ?, ?)",
                (pair, str(timestamp), action, setup_type, confidence, json.dumps(raw_response))
            )
            conn.commit()


class JevHunterEngine:
    """
    Tầng 2: Jev Active Hunter (Bộ não ra quyết định)
    """

    def __init__(self, api_url: Optional[str] = None, api_key: Optional[str] = None, cache_db: str = "user_data/jev_hunter_cache.sqlite"):
        self._load_env()
        self.api_url = api_url or os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
        self.api_key = api_key or os.environ.get("JEV_API_KEY")
        self.cache = JevHunterCache(cache_db)

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

    def evaluate_candidate(self, df_32: DataFrame, pair: str, timestamp: str, event_trigger: str, macro_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Đánh giá chuỗi 32 nến (8 tiếng) của ứng viên để ra quyết định: enter_short, enter_long hay no_trade.
        """
        # 1. Kiểm tra Cache SQLite trước
        cached = self.cache.get(pair, timestamp)
        if cached:
            return cached

        # 2. Xây dựng narrative state cho Jev (32 nến)
        candles_summary = []
        for i, (_, c) in enumerate(df_32.iterrows()):
            c_type = "trigger" if i == len(df_32) - 1 else f"T-{len(df_32) - 1 - i}"
            range_val = c["high"] - c["low"]
            body = abs(c["close"] - c["open"])
            body_ratio = body / range_val if range_val > 0 else 0.5
            uw = (c["high"] - max(c["open"], c["close"])) / range_val if range_val > 0 else 0
            lw = (min(c["open"], c["close"]) - c["low"]) / range_val if range_val > 0 else 0
            vol_ratio = c.get("volume_ratio_20", 1.0)
            candles_summary.append(
                f"[{c_type}] O:{c['open']:.1f} C:{c['close']:.1f} (Body:{body_ratio:.2f}, LW:{lw:.2f}, UW:{uw:.2f}, Vol:{vol_ratio:.2f}x)"
            )

        last_candle = df_32.iloc[-1]
        hours = len(df_32) * 15 / 60

        macro_str = ""
        if macro_info:
            macro_str = f"""
Macro Structural Anchors:
- 4H Trend: {macro_info.get('trend_4h', 'NEUTRAL')}
- 1H EMA50 Distance: {macro_info.get('ema50_1h_dist_pct', 0.0):.2f}%
- ATR Volatility Percentile: {macro_info.get('atr_percentile', 50.0):.1f}%
- 24H High/Low Range: {macro_info.get('range_24h_pct', 0.0):.2f}%
"""

        state_text = f"""Market Event Scout:
Asset: {pair} (Futures 10x)
Timestamp: {timestamp}
Current Price: {last_candle['close']:.1f}
Lookback Window: {len(df_32)} candles ({hours:.1f} hours of price action)
Radar Event: {event_trigger}
{macro_str}
32-Candle Trajectory Sequence (oldest to newest):
""" + "\n".join(candles_summary)

        req_body = {
            "model": "jev-latest",
            "state": state_text,
            "questions": {
                "suggested_action": {
                    "type": "choice",
                    "instructions": "What is the optimal high-expectancy trading action for this price action sequence?",
                    "criteria": {
                        "enter_short": "Strong institutional selling pressure, waterfall trend continuation, or clean breakdown",
                        "enter_long": "Deep oversold capitulation with buyers stepping in, absorption wick, or spring bottom",
                        "no_trade": "Erratic chop, lack of clear follow-through, or ambiguous range bound trading"
                    }
                },
                "setup_type": {
                    "type": "choice",
                    "instructions": "What market structure phenomenon does this sequence best represent?",
                    "criteria": {
                        "waterfall_collapse": "Rapid sequential lower highs and lower lows with expanding bearish volume",
                        "absorption_trap": "Price pushed into a new extreme but met with heavy absorbing liquidity / rejection",
                        "trend_pullback": "Controlled counter-trend pause inside an existing larger trend",
                        "consolidation_noise": "Sideways churn without distinct edge"
                    }
                },
                "conviction": {
                    "type": "noul",
                    "instructions": "Does this setup offer clean directional momentum and high follow-through probability?"
                }
            }
        }

        try:
            data = json.dumps(req_body).encode("utf-8")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Freqtrade-Jev-Hunter/1.0"
            }
            req = urllib.request.Request(self.api_url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))

            ans = res.get("answers", {})
            action = ans.get("suggested_action", {}).get("choice", "no_trade")
            probs = ans.get("suggested_action", {}).get("probabilities", {})
            action_prob = probs.get(action, 0.5)
            setup = ans.get("setup_type", {}).get("choice", "consolidation_noise")

            verdict = {
                "action": action,
                "setup_type": setup,
                "confidence": round(action_prob, 2),
                "raw_response": res
            }

            # Lưu vào Cache SQLite vĩnh viễn
            self.cache.put(pair, timestamp, action, setup, round(action_prob, 2), res)
            return verdict
        except Exception as e:
            logger.warning(f"Jev Hunter API call error ({e}).")
            return {
                "action": "no_trade",
                "setup_type": "error",
                "confidence": 0.0,
                "raw_response": {"error": str(e)}
            }
