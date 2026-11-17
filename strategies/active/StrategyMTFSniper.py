import os
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
import logging
from typing import Optional
from freqtrade.strategy import (
    IStrategy,
    informative,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
    stoploss_from_open,
)
import talib.abstract as ta
try:
    from technical import qtpylib
except ImportError:
    import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.persistence import Trade

logger = logging.getLogger(__name__)


class StrategyMTFSniper(IStrategy):

    """
    CHIẾN LƯỢC TOÀN DIỆN ĐA CHIỀU: MTF SNIPER LENS (LONG + SHORT)
    ============================================================
    Chiến lược giao dịch định lượng hai chiều kết hợp giữa:
    1. MTF Sniper Architecture (15M / 1H / 4H / 1D Confluence)
    2. AI Agent Lens Feature Engineering:
       - ATR Percentile Filter: Chặn mở lệnh khi ATR ở phân vị cực đại (>= 90%) để tránh quét râu thanh lý.
       - Candle Anatomy Rejection Filter: Lọc bẫy nến từ chối giá (râu dài chống lại hướng bứt phá).
       - PDH Ceiling Guard: Chặn mua khi áp sát trần đỉnh ngày hôm trước (tránh fakeout).
    """

    INTERFACE_VERSION = 3
    strategy_version = "2.6.0"

    timeframe = "15m"
    can_short: bool = True
    startup_candle_count: int = 400

    # Default fallback flags (sẽ được ghi đè tự động bởi config.json hoặc biến môi trường)
    enable_jev_decision: bool = True
    enable_jev_hunter: bool = True
    enable_dynamic_sizing: bool = False
    jev_fail_policy: str = "allow"
    _hunter: Optional[object] = None

    @staticmethod
    def apply_cooldown(signal: pd.Series, cooldown: int) -> pd.Series:
        """
        Enforce strict candle-count cooldown using positional row index (0..len-1).
        Ensures minimum `cooldown` bars between consecutive entry signals,
        regardless of DataFrame index type (RangeIndex, DatetimeIndex, etc.).
        Condition: pos - last_pos >= cooldown.
        """
        result = pd.Series(False, index=signal.index)
        if not signal.any():
            return result
        sig_array = signal.to_numpy(dtype=bool)
        res_array = np.zeros(len(sig_array), dtype=bool)
        last_pos = -cooldown - 1
        for pos, is_sig in enumerate(sig_array):
            if is_sig and (pos - last_pos >= cooldown):
                res_array[pos] = True
                last_pos = pos
        result.iloc[:] = res_array
        return result


    @staticmethod
    def _to_bool(val: object, default: bool = False) -> bool:
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        return str(val).strip().lower() in ("true", "1", "yes", "on")

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._load_external_config()

    def _load_external_config(self) -> None:
        """
        Nạp cấu hình từ bên ngoài theo thứ tự ưu tiên với parse boolean an toàn:
        1. Mục 'strategy_params' trong config.json
        2. Biến môi trường (.env / Docker Compose / Coolify)
        3. Giá trị mặc định (Fallback)
        """
        strat_cfg = self.config.get("strategy_params", {}) if hasattr(self, "config") and self.config else {}

        # 1. JEV AI Trajectory Gatekeeper
        if "enable_jev_decision" in strat_cfg:
            self.enable_jev_decision = self._to_bool(strat_cfg["enable_jev_decision"], True)
        else:
            self.enable_jev_decision = self._to_bool(os.environ.get("ENABLE_JEV_DECISION", "true"), True)

        # 2. JEV AI Momentum & Waterfall Hunter
        if "enable_jev_hunter" in strat_cfg:
            self.enable_jev_hunter = self._to_bool(strat_cfg["enable_jev_hunter"], True)
        else:
            self.enable_jev_hunter = self._to_bool(os.environ.get("ENABLE_JEV_HUNTER", "true"), True)

        # 3. Dynamic Stake Sizing
        if "enable_dynamic_sizing" in strat_cfg:
            self.enable_dynamic_sizing = self._to_bool(strat_cfg["enable_dynamic_sizing"], False)
        else:
            self.enable_dynamic_sizing = self._to_bool(os.environ.get("ENABLE_DYNAMIC_SIZING", "false"), False)

        # 4. JEV Fail Policy ('reject' / 'allow')
        if "jev_fail_policy" in strat_cfg:
            self.jev_fail_policy = str(strat_cfg["jev_fail_policy"]).lower()
        else:
            self.jev_fail_policy = os.environ.get("JEV_FAIL_POLICY", "allow").lower()

    def bot_start(self, **kwargs) -> None:
        self._load_external_config()
        logger.info(
            f"StrategyMTFSniper v{self.strategy_version} started. "
            f"Active Profile: JEV Decision={self.enable_jev_decision}, "
            f"JEV Hunter={self.enable_jev_hunter}, "
            f"Dynamic Sizing={self.enable_dynamic_sizing}"
        )
        api_key = os.environ.get("JEV_API_KEY", "")
        if self.enable_jev_decision or self.enable_jev_hunter:
            if api_key:
                masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
                logger.info(f"[JEV AI] Engine initialized successfully! API Key detected ({masked}). Target URL: https://api.typesafe.ai/v1/systemone")
            else:
                logger.warning("[JEV AI] WARNING: JEV_API_KEY is not set in environment! Gatekeeper/Hunter will operate in mock/cached mode.")
        else:
            logger.info("[JEV AI] JEV AI features are DISABLED via configuration. Operating in pure quantitative technical mode.")

    def version(self) -> str | None:
        return f"v{self.strategy_version} (Unified Risk Gate & Institutional Remediation Edition)"

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        target_lev = 10.0
        if max_leverage:
            return min(target_lev, float(max_leverage))
        return target_lev

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str,
                            **kwargs) -> float:
        """
        Unified Risk Budget Sizing:
        - Mặc định: Flat 1.0x baseline để đảm bảo sự ổn định và nhất quán trong production.
        - Khi bật enable_dynamic_sizing: Phân bổ cân đối theo Risk Budget giữa các nhóm tag.
        """
        if not getattr(self, "enable_dynamic_sizing", False):
            return min(proposed_stake, max_stake)

        multiplier = 1.0
        if entry_tag:
            if "long_A80plus" in entry_tag:
                multiplier = 1.10
            elif "short_A80plus" in entry_tag:
                multiplier = 1.10
            elif "short_B65plus" in entry_tag:
                multiplier = 1.00
            elif "short_C_sub65" in entry_tag:
                multiplier = 0.85
            elif "jev_hunter" in entry_tag:
                multiplier = 1.00

        target_stake = proposed_stake * multiplier
        return min(target_stake, max_stake)

    stoploss = -0.28
    use_custom_stoploss = True
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    process_only_new_candles = True

    # Cấu hình riêng biệt cho từng cặp tiền
    custom_pair_params = {
        "BTC/USDT:USDT": {
            "stoploss": -0.25,
            "short_max_dist_ema21": 1.0,
            "short_candle_body_ratio_min": 0.62,
            "long_max_dist_ema21": 3.3,
            "long_candle_body_ratio_min": 0.63,
        },
        "ETH/USDT:USDT": {
            "stoploss": -0.28,
            "short_max_dist_ema21": 1.2,
            "short_candle_body_ratio_min": 0.60,
            "long_max_dist_ema21": 3.3,
            "long_candle_body_ratio_min": 0.63,
        },
    }

    def _get_pair_params(self, pair: str) -> dict:
        for k, v in self.custom_pair_params.items():
            if k == pair or k.split(":")[0] == pair.split(":")[0]:
                return v
        return {"stoploss": self.stoploss}

    minimal_roi = {
        "0": 1.00,
        "720": 0.70,
    }

    # =========================================================================
    # 1. THAM SỐ CẤU HÌNH VÀO LỆNH (HYPEROPT SẴN SÀNG)
    # =========================================================================
    # Short Confluence Parameters
    short_loc_tolerance_1h = DecimalParameter(0.990, 1.005, default=0.991, decimals=3, space="buy", optimize=False)
    short_min_1h_rsi_exhaustion = DecimalParameter(52.0, 70.0, default=55.1, decimals=1, space="buy", optimize=False)
    short_lookback_support = IntParameter(12, 30, default=17, space="buy", optimize=False)
    short_candle_body_ratio_min = DecimalParameter(0.45, 0.70, default=0.62, decimals=2, space="buy", optimize=False)
    short_max_dist_ema21 = DecimalParameter(0.5, 2.5, default=1.0, decimals=1, space="buy", optimize=False)
    short_min_rsi_entry = DecimalParameter(20.0, 35.0, default=27.8, decimals=1, space="buy", optimize=False)
    short_cooldown_candles = IntParameter(48, 120, default=96, space="buy", optimize=False)

    # Long Confluence Parameters
    long_loc_tolerance_1h = DecimalParameter(0.980, 1.010, default=1.008, decimals=3, space="buy", optimize=True)
    long_max_1h_rsi_exhaustion = DecimalParameter(40.0, 68.0, default=62.4, decimals=1, space="buy", optimize=True)
    long_lookback_resistance = IntParameter(8, 25, default=22, space="buy", optimize=True)
    long_candle_body_ratio_min = DecimalParameter(0.40, 0.70, default=0.63, decimals=2, space="buy", optimize=True)
    long_max_dist_ema21 = DecimalParameter(1.0, 3.5, default=3.3, decimals=1, space="buy", optimize=True)
    long_max_rsi_entry = DecimalParameter(65.0, 85.0, default=67.6, decimals=1, space="buy", optimize=True)
    long_cooldown_candles = IntParameter(16, 96, default=18, space="buy", optimize=True)
    long_overextended_dist_max = DecimalParameter(0.35, 1.00, default=0.59, decimals=2, space="buy", optimize=True)
    long_block_1d_macd = BooleanParameter(default=True, space="buy", optimize=True)

    block_4h_counter_wave = BooleanParameter(default=True, space="buy", optimize=False)
    block_1d_counter_wave = BooleanParameter(default=True, space="buy", optimize=False)
    min_adx_4h = DecimalParameter(12.0, 25.0, default=18.0, decimals=1, space="buy", optimize=False)

    # --- AI AGENT LENS ENHANCEMENTS ---
    block_high_vol = BooleanParameter(default=True, space="buy", optimize=False)
    high_vol_pct = DecimalParameter(85.0, 95.0, default=90.0, decimals=1, space="buy", optimize=False)
    filter_wick_rejection = BooleanParameter(default=True, space="buy", optimize=False)
    max_rejection_wick_ratio = DecimalParameter(0.20, 0.45, default=0.35, decimals=2, space="buy", optimize=False)
    min_close_pos_ratio_long = DecimalParameter(0.55, 0.75, default=0.65, decimals=2, space="buy", optimize=False)
    max_close_pos_ratio_short = DecimalParameter(0.25, 0.45, default=0.35, decimals=2, space="buy", optimize=False)
    filter_pdh_ceiling = BooleanParameter(default=True, space="buy", optimize=False)

    # =========================================================================
    # 2. KHUNG SIÊU VĨ MÔ NGÀY (INFORMATIVE 1D)
    # =========================================================================
    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_100"] = ta.EMA(dataframe, timeperiod=100)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_prev"] = dataframe["rsi"].shift(1)
        macd_1d = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd_1d["macdhist"]
        dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)
        dataframe["swing_low_20d"] = dataframe["low"].rolling(20).min()
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)

        # Sóng Hồi 1D
        dataframe["is_bull_wave"] = (dataframe["macdhist"] > 0) & (dataframe["macdhist"] > dataframe["macdhist_prev"])
        dataframe["is_bear_wave"] = (dataframe["macdhist"] < 0) & (dataframe["macdhist"] < dataframe["macdhist_prev"])

        # Siêu Chu kỳ Bull Run Vĩ mô (cho Short cản tàu)
        is_golden_bull = (dataframe["close"] > dataframe["ema_50"]) & (dataframe["ema_50"] > dataframe["ema_200"]) & (dataframe["rsi"] > 60)
        is_bull_stack = (
            (dataframe["close"] > dataframe["ema_21"]) & (dataframe["ema_21"] > dataframe["ema_50"]) &
            (dataframe["ema_50"] > dataframe["ema_100"]) & (dataframe["ema_100"] > dataframe["ema_200"]) &
            (dataframe["adx"] > 20)
        )
        is_parabolic_surge = (dataframe["adx"] > 50) & (dataframe["plus_di"] > dataframe["minus_di"]) & (dataframe["close"] > dataframe["ema_200"])
        dataframe["is_macro_bull_run"] = is_golden_bull | is_bull_stack | is_parabolic_surge

        # Siêu Chu kỳ Bear Market Vĩ mô (cho Long cản tàu)
        is_death_cross_bear = (dataframe["close"] < dataframe["ema_200"]) | ((dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_200"]))
        dataframe["is_macro_bear_market"] = is_death_cross_bear

        # Vùng Đáy Kiệt sức Chu kỳ (< -28% so với EMA 200)
        dataframe["is_deep_capitulation"] = ((dataframe["close"] - dataframe["ema_200"]) / dataframe["ema_200"]) < -0.28
        # Khoảng cách so với EMA 200 ngày
        dataframe["dist_from_ema_200"] = (dataframe["close"] - dataframe["ema_200"]) / dataframe["ema_200"]
        dataframe["is_overextended_top"] = dataframe["dist_from_ema_200"] > 0.35

        # Bollinger Bands Width 1D
        bb_1d = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_width"] = (bb_1d["upperband"] - bb_1d["lowerband"]) / bb_1d["middleband"]

        # AI Agent Lens: Previous Day High / Low (Dùng nến hôm qua đã đóng, tránh lookahead)
        dataframe["pdh"] = dataframe["high"].shift(1)
        dataframe["pdl"] = dataframe["low"].shift(1)
        return dataframe

    # =========================================================================
    # 3. KHUNG VĨ MÔ 4H (INFORMATIVE 4H)
    # =========================================================================
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        macd_4h = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd_4h["macdhist"]
        dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)
        dataframe["is_bull_wave"] = (dataframe["macdhist"] > 0) & (dataframe["macdhist"] > dataframe["macdhist_prev"])
        dataframe["is_bear_wave"] = (dataframe["macdhist"] < 0) & (dataframe["macdhist"] < dataframe["macdhist_prev"])
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)

        bb_4h = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_width"] = (bb_4h["upperband"] - bb_4h["lowerband"]) / bb_4h["middleband"]
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)
        return dataframe

    # =========================================================================
    # 4. KHUNG TRUNG HẠN 1H (INFORMATIVE 1H)
    # =========================================================================
    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        macd_1h = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd_1h["macdhist"]
        dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_max_24"] = dataframe["rsi"].rolling(24).max()
        dataframe["rsi_min_24"] = dataframe["rsi"].rolling(24).min()
        return dataframe

    # =========================================================================
    # 5. KHUNG CƠ SỞ 15M (TRIGGER ENTRY & LENS ANATOMY)
    # =========================================================================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_10"] = ta.EMA(dataframe, timeperiod=10)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd["macdhist"]

        dataframe["body"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["total_range"] = (dataframe["high"] - dataframe["low"]).clip(lower=0.0001)

        # AI Agent Lens: Phân vị biến động ATR trong 100 nến
        dataframe["atr_pct"] = dataframe["atr"].rolling(100).rank(pct=True) * 100

        # AI Agent Lens: DMI & ADX trên khung 15m
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)

        # AI Agent Lens: Khối lượng tương đối (Relative Volume)
        dataframe["vol_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["rel_vol"] = dataframe["volume"] / dataframe["vol_sma"].replace(0, np.nan).fillna(1.0)

        # AI Agent Lens: Giải phẫu nến vi mô (Candle Anatomy)
        dataframe["upper_wick"] = dataframe["high"] - dataframe[["open", "close"]].max(axis=1)
        dataframe["lower_wick"] = dataframe[["open", "close"]].min(axis=1) - dataframe["low"]
        dataframe["upper_wick_ratio"] = dataframe["upper_wick"] / dataframe["total_range"]
        dataframe["lower_wick_ratio"] = dataframe["lower_wick"] / dataframe["total_range"]
        dataframe["close_pos_ratio"] = (dataframe["close"] - dataframe["low"]) / dataframe["total_range"]

        # AI Agent Lens: Hệ thống chấm điểm hợp lưu (Confluence Score 0 - 100)
        # 1. Trend (20 pts)
        l_trend = (
            (dataframe["ema_10"] > dataframe["ema_21"]).astype(int) * 7 +
            (dataframe["ema_21"] > dataframe["ema_200"]).astype(int) * 7 +
            (dataframe["close"] > dataframe["ema_10"]).astype(int) * 6
        )
        s_trend = (
            (dataframe["ema_10"] < dataframe["ema_21"]).astype(int) * 7 +
            (dataframe["ema_21"] < dataframe["ema_200"]).astype(int) * 7 +
            (dataframe["close"] < dataframe["ema_10"]).astype(int) * 6
        )

        # 2. Momentum (20 pts)
        l_mom = (
            ((dataframe["rsi"] > 50) & (dataframe["rsi"] < 75)).astype(int) * 10 +
            ((dataframe["rsi"] >= 75)).astype(int) * 4 +
            (dataframe["macdhist"] > 0).astype(int) * 5 +
            (dataframe["macdhist"] > dataframe["macdhist"].shift(1)).astype(int) * 5
        )
        s_mom = (
            ((dataframe["rsi"] < 50) & (dataframe["rsi"] > 25)).astype(int) * 10 +
            ((dataframe["rsi"] <= 25)).astype(int) * 4 +
            (dataframe["macdhist"] < 0).astype(int) * 5 +
            (dataframe["macdhist"] < dataframe["macdhist"].shift(1)).astype(int) * 5
        )

        # 3. Strength (20 pts)
        l_str = (
            (dataframe["plus_di"] > dataframe["minus_di"]).astype(int) * 10 +
            ((dataframe["plus_di"] > dataframe["minus_di"]) & (dataframe["adx"] >= 20)).astype(int) * 10
        )
        s_str = (
            (dataframe["minus_di"] > dataframe["plus_di"]).astype(int) * 10 +
            ((dataframe["minus_di"] > dataframe["plus_di"]) & (dataframe["adx"] >= 20)).astype(int) * 10
        )

        # 4. Volume (20 pts)
        is_green = dataframe["close"] > dataframe["open"]
        is_red = dataframe["close"] < dataframe["open"]
        l_vol = (
            (is_green & (dataframe["rel_vol"] >= 1.5)).astype(int) * 20 +
            (is_green & (dataframe["rel_vol"] >= 1.0) & (dataframe["rel_vol"] < 1.5)).astype(int) * 12 +
            (is_green & (dataframe["rel_vol"] < 1.0)).astype(int) * 6
        )
        s_vol = (
            (is_red & (dataframe["rel_vol"] >= 1.5)).astype(int) * 20 +
            (is_red & (dataframe["rel_vol"] >= 1.0) & (dataframe["rel_vol"] < 1.5)).astype(int) * 12 +
            (is_red & (dataframe["rel_vol"] < 1.0)).astype(int) * 6
        )

        # 5. HTF Bias (20 pts)
        l_htf = ((dataframe.get("macdhist_4h", pd.Series(0, index=dataframe.index)) > 0).astype(int) * 10 +
                 (dataframe["close"] > dataframe.get("ema_50_1d", dataframe["close"])).astype(int) * 10)
        s_htf = ((dataframe.get("is_bear_wave_4h", pd.Series(0, index=dataframe.index)) == 1).astype(int) * 10 +
                 (dataframe["close"] < dataframe.get("ema_50_1d", dataframe["close"])).astype(int) * 10)

        dataframe["long_score"] = l_trend + l_mom + l_str + l_vol + l_htf
        dataframe["short_score"] = s_trend + s_mom + s_str + s_vol + s_htf

        # Hỗ trợ cho Short
        s_lookback = self.short_lookback_support.value
        dataframe["swing_low"] = dataframe["low"].shift(1).rolling(s_lookback).min()

        # Kháng cự cho Long
        l_lookback = self.long_lookback_resistance.value
        dataframe["swing_high"] = dataframe["high"].shift(1).rolling(l_lookback).max()

        dataframe["swing_high_12"] = dataframe["high"].shift(1).rolling(12).max()
        dataframe["swing_low_12"] = dataframe["low"].shift(1).rolling(12).min()

        dataframe["range_24h"] = (dataframe["high"].rolling(96).max() - dataframe["low"].rolling(96).min()) / dataframe["close"]

        return dataframe

    # =========================================================================
    # 6. QUY TRÌNH BẮT ĐIỂM VÀO LỆNH (POPULATE ENTRY TREND)
    # =========================================================================
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata.get("pair", "")
        pair_cfg = self._get_pair_params(pair)

        # ---------------------------------------------------------------------
        # 0. KHỞI TẠO CỘT TÍN HIỆU & KIỂM TRA TÍNH SẴN SÀNG CỦA DỮ LIỆU (FAIL-CLOSED)
        # ---------------------------------------------------------------------
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = ""

        required_informative_cols = [
            "close_1h", "ema_50_1h", "macdhist_1h", "macdhist_prev_1h",
            "macdhist_4h", "ema_50_1d", "macdhist_1d", "pdh_1d"
        ]
        missing_cols = [c for c in required_informative_cols if c not in dataframe.columns]
        if missing_cols:
            data_ready = pd.Series(False, index=dataframe.index)
        else:
            data_ready = dataframe[required_informative_cols].notna().all(axis=1)

        # ---------------------------------------------------------------------
        # 1. BỘ LỌC CHUNG TỪ AI AGENT LENS: HIGH VOLATILITY & SIDEWAYS SQUEEZE
        # ---------------------------------------------------------------------
        cond_volatility_safe = (
            (dataframe["atr_pct"] < self.high_vol_pct.value)
            if self.block_high_vol.value else pd.Series(True, index=dataframe.index)
        )

        is_chop_squeeze = pd.Series(False, index=dataframe.index)
        if "bb_width_4h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | (dataframe["bb_width_4h"] < 0.02)
        if "adx_4h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | (dataframe["adx_4h"] < self.min_adx_4h.value)
        if "bb_width_1d" in dataframe.columns and "range_24h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | ((dataframe["bb_width_1d"] < 0.10) & (dataframe["range_24h"] < 0.022))
        cond_sideway_safe = ~is_chop_squeeze

        # ---------------------------------------------------------------------
        # 2. XÁC ĐỊNH CHẾ ĐỘ THỊ TRƯỜNG VĨ MÔ (UNIFIED MACRO REGIMES)
        # ---------------------------------------------------------------------
        has_macro_cols = (
            ("ema_50_1d" in dataframe.columns) and
            ("is_macro_bull_run_1d" in dataframe.columns) and
            ("is_macro_bear_market_1d" in dataframe.columns) and
            ("macdhist_4h" in dataframe.columns)
        )
        if has_macro_cols:
            is_macro_bull_1d = (
                (dataframe["is_macro_bull_run_1d"].fillna(0) == 1) |
                ((dataframe["close"] > dataframe["ema_50_1d"]) & (dataframe["macdhist_4h"].fillna(0) > 0))
            )
            is_macro_bear_1d = (
                (dataframe["is_macro_bear_market_1d"].fillna(0) == 1) |
                ((dataframe["close"] < dataframe["ema_50_1d"]) & (dataframe["macdhist_4h"].fillna(0) < 0))
            )
        else:
            is_macro_bull_1d = pd.Series(False, index=dataframe.index)
            is_macro_bear_1d = pd.Series(False, index=dataframe.index)

        # ---------------------------------------------------------------------
        # A. CANDIDATE SHORT SNIPER
        # ---------------------------------------------------------------------
        s_max_dist = pair_cfg.get("short_max_dist_ema21", self.short_max_dist_ema21.value)
        s_body_ratio = pair_cfg.get("short_candle_body_ratio_min", self.short_candle_body_ratio_min.value)

        is_red = dataframe["close"] < dataframe["open"]
        is_decisive_red = (
            is_red &
            ((dataframe["body"] / dataframe["total_range"]) >= s_body_ratio) &
            (dataframe["body"] >= dataframe["atr"] * 0.5)
        )
        is_breakdown_major = (dataframe["close"] < dataframe["swing_low"]) & (dataframe["close"].shift(1) >= dataframe["swing_low"])
        is_below_emas = (dataframe["close"] < dataframe["ema_10"]) & (dataframe["close"] < dataframe["ema_21"])

        # Short tại vùng hồi kháng cự hợp lý: từ -0.9% đến +2.5% so với EMA 50 1H
        cond_1h_short_pos = (
            (dataframe["close_1h"] >= (dataframe["ema_50_1h"] * self.short_loc_tolerance_1h.value)) &
            (dataframe["close_1h"] <= (dataframe["ema_50_1h"] * 1.025))
        )
        cond_1h_short_macd = ((dataframe["macdhist_1h"] - dataframe["macdhist_prev_1h"]) < 0) | (dataframe["macdhist_1h"] < 0)
        cond_1h_short_exhaustion = dataframe["rsi_max_24_1h"] >= self.short_min_1h_rsi_exhaustion.value
        cond_1h_short = cond_1h_short_pos & cond_1h_short_macd & cond_1h_short_exhaustion

        cond_4h_short = (dataframe["is_bull_wave_4h"].fillna(0) == 0) if self.block_4h_counter_wave.value else pd.Series(True, index=dataframe.index)
        if pair.startswith("ETH") and "minus_di_4h" in dataframe.columns and "plus_di_4h" in dataframe.columns:
            cond_4h_di_short = dataframe["minus_di_4h"] > dataframe["plus_di_4h"]
        else:
            cond_4h_di_short = pd.Series(True, index=dataframe.index)

        # Fail-closed macro guards
        # Bộ lọc Xu hướng Lớn: Tuyệt đối không đánh ngược xu hướng (không tham sóng hồi)
        # 1. Trong Bull Regime (giá trên cả EMA 50 và EMA 200 ngày): CẤM SHORT (không short sóng hồi trong uptrend)
        is_in_macro_bull = (dataframe["close"] > dataframe["ema_50_1d"]) & (dataframe["close"] > dataframe["ema_200_1d"]) if ("ema_50_1d" in dataframe.columns and "ema_200_1d" in dataframe.columns) else pd.Series(False, index=dataframe.index)
        cond_macro_safe_short = ~is_in_macro_bull & ((dataframe["is_macro_bull_run_1d"].fillna(0) == 0) if "is_macro_bull_run_1d" in dataframe.columns else pd.Series(False, index=dataframe.index))
        cond_1d_short = (dataframe["is_bull_wave_1d"].fillna(0) == 0) if (self.block_1d_counter_wave.value and "is_bull_wave_1d" in dataframe.columns) else pd.Series(True, index=dataframe.index)
        cond_floor_safe = (((dataframe["close"] - dataframe["swing_low_20d_1d"]) / dataframe["swing_low_20d_1d"]) >= 0.035) if "swing_low_20d_1d" in dataframe.columns else pd.Series(False, index=dataframe.index)
        cond_capitulation_safe = (dataframe["is_deep_capitulation_1d"].fillna(0) == 0) if "is_deep_capitulation_1d" in dataframe.columns else pd.Series(False, index=dataframe.index)

        cond_not_chasing_short = (
            ((dataframe["ema_21"] - dataframe["close"]) / dataframe["close"] <= (s_max_dist / 100.0)) &
            (dataframe["rsi"] >= self.short_min_rsi_entry.value)
        )

        cond_candle_shape_short = (
            (dataframe["lower_wick_ratio"] <= self.max_rejection_wick_ratio.value) &
            (dataframe["close_pos_ratio"] <= self.max_close_pos_ratio_short.value)
        ) if self.filter_wick_rejection.value else pd.Series(True, index=dataframe.index)

        raw_sniper_short = (
            is_decisive_red & is_breakdown_major & is_below_emas &
            cond_1h_short & cond_4h_short & cond_4h_di_short & cond_macro_safe_short & cond_1d_short &
            cond_floor_safe & cond_capitulation_safe & cond_sideway_safe &
            cond_not_chasing_short & cond_volatility_safe & cond_candle_shape_short &
            (dataframe["volume"] > 0) & data_ready
        )

        # ---------------------------------------------------------------------
        # B. CANDIDATE LONG SNIPER
        # ---------------------------------------------------------------------
        l_max_dist = pair_cfg.get("long_max_dist_ema21", self.long_max_dist_ema21.value)
        l_body_ratio = pair_cfg.get("long_candle_body_ratio_min", self.long_candle_body_ratio_min.value)

        is_green = dataframe["close"] > dataframe["open"]
        is_decisive_green = (
            is_green &
            ((dataframe["body"] / dataframe["total_range"]) >= l_body_ratio) &
            (dataframe["body"] >= dataframe["atr"] * 0.5)
        )
        l_lookback = self.long_lookback_resistance.value
        swing_high = dataframe["high"].shift(1).rolling(l_lookback).max()
        is_breakout_major = (dataframe["close"] > swing_high) & (dataframe["close"].shift(1) <= swing_high)
        is_above_emas = (dataframe["close"] > dataframe["ema_10"]) & (dataframe["close"] > dataframe["ema_21"])

        cond_1h_long_pos = dataframe["close_1h"] >= (dataframe["ema_50_1h"] * self.long_loc_tolerance_1h.value)
        cond_1h_long_exhaustion = dataframe["rsi_min_24_1h"] <= self.long_max_1h_rsi_exhaustion.value
        cond_1h_long = cond_1h_long_pos & cond_1h_long_exhaustion

        cond_4h_long = (dataframe["macdhist_4h"] > 0) if "macdhist_4h" in dataframe.columns else pd.Series(False, index=dataframe.index)

        cond_1d_long = (
            (dataframe["close"] > dataframe["ema_50_1d"]) & 
            ((dataframe["macdhist_1d"] > 0) if self.long_block_1d_macd.value else True)
        ) if ("ema_50_1d" in dataframe.columns and "macdhist_1d" in dataframe.columns) else pd.Series(False, index=dataframe.index)

        # 2. Trong Bear Regime (giá dưới cả EMA 50 hoặc EMA 200 ngày): CẤM LONG (không bắt dao rơi/sóng hồi trong downtrend)
        is_in_macro_bear = (dataframe["close"] < dataframe["ema_200_1d"]) | (dataframe["close"] < dataframe["ema_50_1d"]) if ("ema_50_1d" in dataframe.columns and "ema_200_1d" in dataframe.columns) else pd.Series(False, index=dataframe.index)
        cond_macro_safe_long = ~is_in_macro_bear & ((dataframe["is_macro_bear_market_1d"].fillna(0) == 0) if "is_macro_bear_market_1d" in dataframe.columns else pd.Series(False, index=dataframe.index))

        cond_overextended_safe = (
            dataframe["dist_from_ema_200_1d"] <= self.long_overextended_dist_max.value
        ) if "dist_from_ema_200_1d" in dataframe.columns else pd.Series(False, index=dataframe.index)

        cond_not_chasing_long = (
            ((dataframe["close"] - dataframe["ema_21"]) / dataframe["ema_21"] <= (l_max_dist / 100.0)) &
            (dataframe["rsi"] <= self.long_max_rsi_entry.value)
        )

        cond_candle_shape_long = (
            (dataframe["upper_wick_ratio"] <= self.max_rejection_wick_ratio.value) &
            (dataframe["close_pos_ratio"] >= self.min_close_pos_ratio_long.value)
        ) if self.filter_wick_rejection.value else pd.Series(True, index=dataframe.index)

        # Strict PDH Filter (Không fallback sang high_1d)
        if "pdh_1d" in dataframe.columns and self.filter_pdh_ceiling.value:
            cond_pdh_safe_long = ~((dataframe["close"] < dataframe["pdh_1d"]) & (dataframe["close"] >= dataframe["pdh_1d"] * 0.997))
        elif not self.filter_pdh_ceiling.value:
            cond_pdh_safe_long = pd.Series(True, index=dataframe.index)
        else:
            cond_pdh_safe_long = pd.Series(False, index=dataframe.index)

        cond_score_long = dataframe["long_score"] >= 80

        raw_sniper_long = (
            is_decisive_green & is_breakout_major & is_above_emas &
            cond_1h_long & cond_4h_long & cond_1d_long & cond_macro_safe_long &
            cond_overextended_safe & cond_sideway_safe &
            cond_not_chasing_long & cond_volatility_safe & cond_candle_shape_long & cond_pdh_safe_long &
            cond_score_long & (dataframe["volume"] > 0) & data_ready
        )

        # ---------------------------------------------------------------------
        # C. KHỞI TẠO BỂ CANDIDATES VÀ GÁN TAG BAN ĐẦU CHO SNIPER
        # ---------------------------------------------------------------------
        cand_short = raw_sniper_short.copy()
        cand_short_tags = pd.Series("", index=dataframe.index)
        for idx in dataframe.index[raw_sniper_short]:
            sc = dataframe.loc[idx, "short_score"]
            tier = "A80plus" if sc >= 80 else ("B65plus" if sc >= 65 else "C_sub65")
            cand_short_tags.loc[idx] = f"short_{tier}"

        cand_long = raw_sniper_long.copy()
        cand_long_tags = pd.Series("", index=dataframe.index)
        for idx in dataframe.index[raw_sniper_long]:
            cand_long_tags.loc[idx] = "long_A80plus"

        # ---------------------------------------------------------------------
        # D. NHÁNH JEV ACTIVE MOMENTUM / WATERFALL HUNTER (TÍCH HỢP VÀO CANDIDATE POOL)
        # ---------------------------------------------------------------------
        if getattr(self, "enable_jev_hunter", True):
            try:
                import sys
                from pathlib import Path
                strat_dir = str(Path(__file__).resolve().parent)
                if strat_dir not in sys.path:
                    sys.path.insert(0, strat_dir)
                from jev_entry_hunter import MomentumRadar, JevHunterEngine, JevSetupType

                if not hasattr(self, "_hunter") or self._hunter is None:
                    self._hunter = JevHunterEngine()
                hunter = self._hunter

                candidates = MomentumRadar.find_candidates(dataframe, cooldown_bars=12, directional_cooldown=True)
                for c_idx, event_type in candidates:
                    pos = int(c_idx)
                    if pos < 0 or pos >= len(dataframe):
                        continue
                    idx = dataframe.index[pos]

                    # Nếu nến đã được chọn bởi Sniper, bỏ qua
                    if cand_short.iloc[pos] or cand_long.iloc[pos]:
                        continue

                    # Fail-closed nếu informative data chưa sẵn sàng tại nến pos
                    if not data_ready.iloc[pos]:
                        continue

                    df_32 = dataframe.iloc[max(0, pos - 31):pos + 1]
                    if len(df_32) >= 16:
                        row = dataframe.iloc[pos]
                        ts_str = str(row["date"])
                        if row.get("is_bull_wave_4h", 0) == 1:
                            trend_4h_str = "BULL"
                        elif row.get("is_bear_wave_4h", 0) == 1:
                            trend_4h_str = "BEAR"
                        else:
                            trend_4h_str = "NEUTRAL"
                        macro_info = {
                            "trend_4h": trend_4h_str,
                            "ema50_1h_dist_pct": ((row["close"] - row["ema_50_1h"]) / row["ema_50_1h"] * 100) if "ema_50_1h" in row else 0.0,
                            "atr_percentile": row.get("atr_pct", 50.0),
                            "range_24h_pct": row.get("range_24h", 0.0) * 100 if "range_24h" in row else 0.0
                        }
                        verdict = hunter.evaluate_candidate(df_32, pair, ts_str, event_type, macro_info)
                        action = verdict.get("action")
                        conf = verdict.get("confidence", 0.0)
                        setup = verdict.get("setup_type", JevSetupType.UNKNOWN.value)
                        status = verdict.get("status", "CONFIRMED")

                        # Chỉ tiếp tục nếu verdict status là CONFIRMED (loại bỏ REJECTED, UNAVAILABLE, INVALID_RESPONSE)
                        if status != "CONFIRMED":
                            continue

                        # Strict Macro Gates (Kế thừa v2.5.0 để loại bỏ hoàn toàn bẫy ngược trend)
                        is_bear_macro_h = (
                            (row.get("is_macro_bear_market_1d", 0) == 1) or
                            (row["close"] < row.get("ema_50_1d", row["close"] * 2)) or
                            (row.get("is_bear_wave_4h", 0) == 1) or
                            (row.get("macdhist_4h", 0) < 0)
                        )
                        is_bull_macro_h = (
                            (row.get("is_macro_bull_run_1d", 0) == 1) or
                            (row["close"] > row.get("ema_50_1d", 0)) or
                            (row.get("is_bull_wave_4h", 0) == 1)
                        )

                        # Kế thừa Risk Gate từ Sniper: Volatility, Sideway, Floor/PDH
                        vol_safe = bool(cond_volatility_safe.iloc[pos])
                        sideway_safe = bool(cond_sideway_safe.iloc[pos])

                        # 1. Chiều SHORT: Chỉ chấp nhận waterfall collapse khi Bear Macro rõ ràng, CẤM trong Bull
                        if action == "enter_short" and is_bear_macro_h and not is_bull_macro_h and (row.get("is_macro_bull_run_1d", 0) == 0):
                            if setup == JevSetupType.WATERFALL_COLLAPSE.value and conf >= 0.85:
                                # AI Waterfall: Cho phép râu dưới tới 0.285 (tránh lọc bỏ nến biến động mạnh như ngày 12/11/2026 có râu 0.28)
                                low_wick_ok = row.get("lower_wick_ratio", 0.0) <= 0.285
                                close_pos_ok = row.get("close_pos_ratio", 0.0) <= 0.35
                                dist_ema21 = (row["close"] - row["ema_21"]) / row["ema_21"] if "ema_21" in row else 0.0
                                # Trong cú sập thác đổ, RSI phá sâu xuống 24 nhưng không kiệt sức hoàn toàn
                                not_exhausted_short = (dist_ema21 >= -0.015) and (row.get("rsi", 50.0) >= 24.0)

                                # Floor filter riêng cho AI Waterfall:
                                # Không short khi nằm sát trên hỗ trợ 20D (0.0 <= floor_dist < 0.035, dễ bị bật nảy)
                                # Cho phép short khi còn cách xa cản (>= 3.5%) HOẶC AI xác nhận đã chính thức thủng đáy (< 0.0)
                                sw_low_20d = row.get("swing_low_20d_1d", 0.0)
                                if sw_low_20d and sw_low_20d > 0:
                                    floor_dist = (row["close"] - sw_low_20d) / sw_low_20d
                                    floor_safe = (floor_dist >= 0.035) or (floor_dist < 0.0)
                                else:
                                    floor_safe = False

                                if low_wick_ok and close_pos_ok and not_exhausted_short and vol_safe and sideway_safe and floor_safe:
                                    cand_short.iloc[pos] = True
                                    cand_short_tags.loc[idx] = f"jev_hunter_{setup}"

                        # 2. Chiều LONG: Đi theo xu hướng tăng 1D (close > ema_50_1d), CẤM trong Bear Market
                        elif action == "enter_long" and (row["close"] > row.get("ema_50_1d", 0)) and (row.get("is_macro_bear_market_1d", 0) == 0):
                            if setup in (JevSetupType.TREND_PULLBACK.value, JevSetupType.ABSORPTION_TRAP.value) and conf >= 0.85:
                                up_wick_ok = row.get("upper_wick_ratio", 0.0) <= 0.285
                                close_pos_ok = row.get("close_pos_ratio", 0.0) >= 0.65
                                dist_ema21 = (row["close"] - row["ema_21"]) / row["ema_21"] if "ema_21" in row else 0.0
                                not_exhausted_long = (dist_ema21 <= 0.015) and (row.get("rsi", 50.0) <= 72.0)
                                pdh_safe = bool(cond_pdh_safe_long.iloc[pos])

                                if up_wick_ok and close_pos_ok and not_exhausted_long and vol_safe and sideway_safe and pdh_safe:
                                    cand_long.iloc[pos] = True
                                    cand_long_tags.loc[idx] = f"jev_hunter_{setup}"
            except Exception as e:
                logger.warning(f"Jev Hunter populate error: {e}")

        # ---------------------------------------------------------------------
        # E. UNIFIED RISK GATE: ÁP DỤNG COOLDOWN ĐỒNG NHẤT CHO CANDIDATE POOL
        # ---------------------------------------------------------------------
        s_cooldown = self.short_cooldown_candles.value
        final_short_mask = self.apply_cooldown(cand_short, s_cooldown)

        l_cooldown = self.long_cooldown_candles.value
        final_long_mask = self.apply_cooldown(cand_long, l_cooldown)

        # Conflict Resolution: Nếu cả Long và Short cùng kích hoạt trên một cây nến (chop dị thường), hủy cả 2 để bảo toàn vốn
        both_mask = final_short_mask & final_long_mask
        if both_mask.any():
            logger.warning(f"[RiskGate] Detected simultaneous Long & Short candidates on {both_mask.sum()} bars. Resolving conflict by canceling both.")
            final_short_mask = final_short_mask & (~both_mask)
            final_long_mask = final_long_mask & (~both_mask)

        dataframe.loc[final_short_mask, "enter_short"] = 1
        for idx in dataframe.index[final_short_mask]:
            dataframe.loc[idx, "enter_tag"] = cand_short_tags.loc[idx]

        dataframe.loc[final_long_mask, "enter_long"] = 1
        for idx in dataframe.index[final_long_mask]:
            dataframe.loc[idx, "enter_tag"] = cand_long_tags.loc[idx]

        return dataframe

    # =========================================================================
    # 7. QUY TRÌNH THOÁT LỆNH (POPULATE EXIT TREND)
    # =========================================================================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_short"] = 0
        dataframe["exit_long"] = 0
        return dataframe

    # =========================================================================
    # 8. CONFIRM TRADE ENTRY (CIRCUIT BREAKER & CORRELATION GUARDS)
    # =========================================================================
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        # --- LỚP 1: STREAK CIRCUIT BREAKER ---
        closed_trades = Trade.get_trades_proxy(is_open=False)
        if len(closed_trades) >= 3:
            recent_trades = sorted(
                closed_trades,
                key=lambda t: t.close_date.timestamp() if t.close_date else 0.0,
                reverse=True
            )
            consecutive_losses = 0
            for t in recent_trades:
                profit = t.close_profit if t.close_profit is not None else 0.0
                if profit <= -0.10:
                    consecutive_losses += 1
                else:
                    # Lệnh hòa vốn, có lãi, hoặc lỗ nhẹ (> -10% ROE) -> Reset chuỗi thua nặng ngay lập tức
                    break

            if consecutive_losses >= 3:
                last_loss_date = recent_trades[0].close_date
                if last_loss_date:
                    if (current_time.timestamp() - last_loss_date.timestamp()) < (3 * 86400):
                        logger.warning(
                            f"[Circuit Breaker] Triggered: {consecutive_losses} consecutive losses. "
                            f"Trading paused for 72h until {last_loss_date.timestamp() + 3*86400}."
                        )
                        return False

        # --- LỚP 2: PORTFOLIO DIRECTIONAL LOCK & DISTRESS GUARD ---
        open_trades = Trade.get_trades_proxy(is_open=True)
        for ot in open_trades:
            ot_side = "short" if ot.is_short else "long"
            if ot_side != side:
                return False

            if ot.pair != pair:
                df_other, _ = self.dp.get_analyzed_dataframe(ot.pair, self.timeframe)
                if df_other is not None and not df_other.empty:
                    curr_row = df_other.loc[df_other["date"] <= current_time]
                    if not curr_row.empty:
                        curr_price = curr_row.iloc[-1]["close"]
                        profit_ratio = ot.calc_profit_ratio(curr_price)
                        if profit_ratio < -0.08:
                            return False

        # --- LỚP 3: UNIFIED JEV AI TRAJECTORY GATEKEEPER (Áp dụng cho TẤT CẢ các lệnh) ---
        if getattr(self, "enable_jev_decision", False):
            try:
                import sys
                from pathlib import Path
                strat_dir = str(Path(__file__).resolve().parent)
                if strat_dir not in sys.path:
                    sys.path.insert(0, strat_dir)
                from jev_decision import CandleTrajectoryExtractor, JevDecisionEngine, JevStatus

                analyzed_df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if analyzed_df is None or analyzed_df.empty:
                    if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                        logger.warning(f"[JEV AI] Fail-safe active -> Trade {pair} rejected: missing analyzed dataframe.")
                        return False
                    return True

                df_up_to_now = analyzed_df.loc[analyzed_df["date"] <= current_time]
                if len(df_up_to_now) < 16:
                    if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                        logger.warning(f"[JEV AI] Fail-safe active -> Trade {pair} rejected: insufficient candle history (<16 bars).")
                        return False
                    return True

                payload = CandleTrajectoryExtractor.build_payload(df_up_to_now, pair, side, lookback=32)
                engine = JevDecisionEngine()
                verdict = engine.evaluate(payload)
                logger.info(
                    f"Jev AI Verdict: {pair} {side.upper()} @ {current_time} -> {verdict.get('decision')} "
                    f"(Conf: {verdict.get('confidence')}, Regime: {verdict.get('market_regime')}, Status: {verdict.get('status')})"
                )

                v_status = verdict.get("status", JevStatus.SUCCESS.value)
                if v_status != JevStatus.SUCCESS.value:
                    if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                        logger.warning(f"[JEV AI] Fail-safe active -> Trade {pair} rejected: AI status is {v_status}")
                        return False
                    else:
                        logger.warning(f"[JEV AI] Fail-safe policy is 'allow' -> Bypassing AI gate due to status {v_status}")
                        return True

                if verdict.get("decision") != "CONFIRM":
                    logger.info(f"Jev AI Vetoed trade: {pair} {side.upper()} @ {current_time} | Reasons: {verdict.get('reasons')}")
                    return False
            except Exception as e:
                logger.warning(f"Jev AI evaluation error: {e}")
                if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                    logger.warning(f"[JEV AI] Fail-safe policy active -> Trade {pair} rejected due to AI evaluation failure: {e}")
                    return False
                logger.warning(f"[JEV AI] Fail-safe policy is 'allow' -> Bypassing AI gate on exception: {e}")
                return True

        return True

    # =========================================================================
    # 9. CUSTOM EXIT (TREND INVALIDATION & TIMEOUT)
    # =========================================================================
    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        if not self.dp:
            return None
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return None

        row = df.loc[df["date"] == current_time]
        if row.empty:
            matched = df.loc[df["date"] <= current_time]
            if matched.empty:
                return None
            r = matched.iloc[-1]
        else:
            r = row.iloc[0]

        duration_minutes = (current_time - trade.open_date_utc).total_seconds() / 60

        # Cắt sớm khi xu hướng bị gãy cấu trúc & HTF đảo chiều (sau >= 3h nếu lệnh âm > 8% ROE)
        if duration_minutes >= 180 and current_profit < -0.08:
            if trade.is_short:
                cond_structure_broken = r.get("close", 0) > r.get("swing_high_12", 0)
                cond_htf_reversal = (
                    (r.get("close_1h", 0) > r.get("ema_50_1h", 0)) and
                    (r.get("macdhist_1h", 0) > 0)
                )
                if cond_structure_broken and cond_htf_reversal:
                    return "exit_short_trend_invalidated"
            else:
                cond_structure_broken = r.get("close", 0) < r.get("swing_low_12", 0)
                cond_htf_reversal = (
                    (r.get("close_1h", 0) < r.get("ema_50_1h", 0)) and
                    (r.get("macdhist_1h", 0) < 0)
                )
                if cond_structure_broken and cond_htf_reversal:
                    return "exit_long_trend_invalidated"

        # HTF Trend Runner Mode: Nuôi các vị thế có đệm lãi lớn (>= 35% ROE) bám theo cấu trúc 1H
        if current_profit >= 0.35:
            e21_1h = r.get("ema_21_1h", 0)
            c_1h = r.get("close_1h", 0)
            if e21_1h > 0 and c_1h > 0:
                if trade.is_short and c_1h > e21_1h:
                    return "exit_short_htf_runner_broken"
                elif (not trade.is_short) and c_1h < e21_1h:
                    return "exit_long_htf_runner_broken"

        # Thời hạn tối đa 24h (chỉ áp dụng cho các lệnh chưa thoát ly đệm lãi < 35% ROE)
        if duration_minutes >= 24 * 60 and current_profit < 0.35:
            if trade.is_short and ((r.get("close", 0) > r.get("ema_21", 0)) or (current_profit < 0.015)):
                return "exit_timeout_24h"
            elif (not trade.is_short) and ((r.get("close", 0) < r.get("ema_21", 0)) or (current_profit < 0.015)):
                return "exit_timeout_24h"

        return None

    # =========================================================================
    # 10. DYNAMIC CUSTOM STOPLOSS
    # =========================================================================
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        lev = trade.leverage if (trade and trade.leverage) else 10.0

        if not trade.is_short:
            if current_profit >= 1.20:
                return -0.20
            if current_profit >= 0.80:
                return -0.30
            if current_profit >= 0.50:
                return stoploss_from_open(0.25, current_profit, is_short=False, leverage=lev)
            if current_profit >= 0.35:
                return stoploss_from_open(0.10, current_profit, is_short=False, leverage=lev)
            if current_profit >= 0.20:
                return stoploss_from_open(0.02, current_profit, is_short=False, leverage=lev)
            if current_profit >= 0.14:
                return stoploss_from_open(0.01, current_profit, is_short=False, leverage=lev)
        else:
            if current_profit >= 1.20:
                return -0.20
            if current_profit >= 0.80:
                return -0.30
            if current_profit >= 0.50:
                return stoploss_from_open(0.25, current_profit, is_short=True, leverage=lev)
            if current_profit >= 0.35:
                return stoploss_from_open(0.10, current_profit, is_short=True, leverage=lev)
            if current_profit >= 0.20:
                return stoploss_from_open(0.02, current_profit, is_short=True, leverage=lev)
            if current_profit >= 0.14:
                return stoploss_from_open(0.01, current_profit, is_short=True, leverage=lev)

        # Quản lý rủi ro vị thế kéo dài sau 24h:
        # Nếu sau 24h vị thế vẫn đang âm, siết chặt stoploss về -5% ROE (-0.5% price distance tại 10x leverage, tránh nhiễu 15m)
        duration_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if duration_hours >= 24 and current_profit < 0.0:
            sl_rel = stoploss_from_open(-0.05, current_profit, is_short=trade.is_short, leverage=lev)
            return max(sl_rel, 0.01)

        pair_cfg = self._get_pair_params(pair)
        base_sl = pair_cfg.get("stoploss", self.stoploss)
        sl_rel = stoploss_from_open(base_sl, current_profit, is_short=trade.is_short, leverage=lev)
        return max(sl_rel, 0.01)

    # =========================================================================
    # 11. CONFIRM TRADE EXIT (DYNAMIC RUNNER & CONFLICT RESOLUTION)
    # =========================================================================
    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time: datetime, **kwargs) -> bool:
        """
        Bảo vệ quyền nuôi lệnh (Trend Runner) và tránh các cơ chế dẫm đạp:
        1. Stoploss, Trailing SL, Emergency luôn được ưu tiên 100% (không bao giờ chặn).
        2. Khi chạm minimal_roi nhưng xu hướng 1H vẫn đang đè mạnh và đệm lãi >= 50% ROE:
           Tạm hoãn chốt lời để thả lỏng cho vị thế chạy theo siêu sóng.
        """
        if exit_reason in ("stop_loss", "trailing_stop_loss", "emergency_exit", "force_exit"):
            return True

        if exit_reason == "roi":
            if not self.dp:
                return True
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if df is None or df.empty:
                return True
            matched = df.loc[df["date"] <= current_time]
            if matched.empty:
                return True
            r = matched.iloc[-1]

            current_profit = trade.calc_profit_ratio(rate)
            if current_profit >= 0.50:
                c_1h = r.get("close_1h", 0)
                e21_1h = r.get("ema_21_1h", 0)
                macd_1h = r.get("macdhist_1h", 0)

                if trade.is_short:
                    if e21_1h > 0 and c_1h < e21_1h:
                        return False
                else:
                    if e21_1h > 0 and c_1h > e21_1h:
                        return False

        return True
