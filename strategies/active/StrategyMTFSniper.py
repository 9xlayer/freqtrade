import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
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


class StrategyMTFSniper(IStrategy):

    """
    CHIẾN LƯỢC TOÀN DIỆN ĐA CHIỀU: MTF SNIPER (LONG + SHORT)
    =======================================================
    Chiến lược giao dịch định lượng hai chiều (All-Weather Quant Strategy)
    tích hợp cả Short Sniper và Long Sniper trên đa khung thời gian:
    1. ĐIỂM VÀO LỆNH (15M / 1H / 4H / 1D Triad Confluence):
       - Short: Thủng Hỗ trợ 17 nến, nến đỏ đặc áp đảo, 1H Bò kiệt sức, 4H & 1D Bearish.
       - Long:  Vượt Kháng cự 17 nến, nến xanh đặc áp đảo, 1H Gấu kiệt sức, 4H & 1D Bullish.
       - Retest EMA 21: Không Short/Long đuổi, vào lệnh tại điểm hồi quy an toàn.
       - Cooldown 24h: Ngăn chặn giao dịch trả thù và bẫy rung lắc.
    2. QUẢN TRỊ VỊ THẾ & RỦI RO (Institutional Risk Controls):
       - Hard SL: BTC -2.5%, ETH -2.8% (x10 leverage = -25% to -28% ROE).
       - Khóa hòa vốn Breakeven: Tại +2.0% kéo SL về bảo toàn vốn (+0.2% chart cho Long / +1.0% cho Short).
       - Trailing Stop: Kích hoạt tại +3.5%, bám sát 2.0% từ đỉnh/đáy.
       - Streak Circuit Breaker: 3 lệnh thua liên tiếp -> Tạm dừng 72 giờ.
       - Portfolio Correlation Guard: Không mở vị thế thứ 2 nếu vị thế 1 đang âm > 0.8% chart.
    """

    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short: bool = True
    startup_candle_count: int = 400

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        return 10.0

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
        },
        "ETH/USDT:USDT": {
            "stoploss": -0.28,
            "short_max_dist_ema21": 1.2,
            "short_candle_body_ratio_min": 0.60,
        },
    }

    def _get_pair_params(self, pair: str) -> dict:
        for k, v in self.custom_pair_params.items():
            if k == pair or k.split(":")[0] == pair.split(":")[0]:
                return v
        return self.custom_pair_params.get("BTC/USDT:USDT", {})

    minimal_roi = {
        "0": 1.00,
        "720": 0.70,
    }

    # =========================================================================
    # 1. THAM SỐ CẤU HÌNH VÀO LỆNH (HYPEROPT SẴN SÀNG)
    # =========================================================================
    # Short Confluence Parameters (Pristine Proven Values)
    short_loc_tolerance_1h = DecimalParameter(0.990, 1.005, default=0.991, decimals=3, space="buy", optimize=False)
    short_min_1h_rsi_exhaustion = DecimalParameter(52.0, 70.0, default=55.1, decimals=1, space="buy", optimize=False)
    short_lookback_support = IntParameter(12, 30, default=17, space="buy", optimize=False)
    short_candle_body_ratio_min = DecimalParameter(0.45, 0.70, default=0.62, decimals=2, space="buy", optimize=False)
    short_max_dist_ema21 = DecimalParameter(0.5, 2.5, default=1.0, decimals=1, space="buy", optimize=False)
    short_min_rsi_entry = DecimalParameter(20.0, 35.0, default=27.8, decimals=1, space="buy", optimize=False)
    short_cooldown_candles = IntParameter(48, 120, default=96, space="buy", optimize=False)

    # Long Confluence Parameters (Optimizable)
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
        # Khoảng cách so với EMA 200 ngày (cho Long & Short đánh giá quá nhiệt)
        dataframe["dist_from_ema_200"] = (dataframe["close"] - dataframe["ema_200"]) / dataframe["ema_200"]
        dataframe["is_overextended_top"] = dataframe["dist_from_ema_200"] > 0.35

        # Bollinger Bands Width 1D
        bb_1d = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_width"] = (bb_1d["upperband"] - bb_1d["lowerband"]) / bb_1d["middleband"]
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
        # Direction of the 4h trend. ETH shorts need -DI > +DI; BTC does not.
        dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)
        return dataframe

    # =========================================================================
    # 4. KHUNG TRUNG HẠN 1H (INFORMATIVE 1H)
    # =========================================================================
    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        macd_1h = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd_1h["macdhist"]
        dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_max_24"] = dataframe["rsi"].rolling(24).max()
        dataframe["rsi_min_24"] = dataframe["rsi"].rolling(24).min()
        return dataframe

    # =========================================================================
    # 5. KHUNG CƠ SỞ 15M (TRIGGER ENTRY)
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
        # A. NHÁNH SHORT SNIPER
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

        cond_1h_short_pos = dataframe["close_1h"] >= (dataframe["ema_50_1h"] * self.short_loc_tolerance_1h.value)
        cond_1h_short_macd = ((dataframe["macdhist_1h"] - dataframe["macdhist_prev_1h"]) < 0) | (dataframe["macdhist_1h"] < 0)
        cond_1h_short_exhaustion = dataframe["rsi_max_24_1h"] >= self.short_min_1h_rsi_exhaustion.value
        cond_1h_short = cond_1h_short_pos & cond_1h_short_macd & cond_1h_short_exhaustion

        cond_4h_short = (dataframe["is_bull_wave_4h"].fillna(0) == 0) if self.block_4h_counter_wave.value else pd.Series(True, index=dataframe.index)
        # ETH shorts taken while 4h +DI leads -DI lost in 4 of 5 years (2022-2026).
        # The same gate removes profitable BTC shorts, so it stays ETH-only.
        if pair.startswith("ETH") and "minus_di_4h" in dataframe.columns and "plus_di_4h" in dataframe.columns:
            cond_4h_di_short = dataframe["minus_di_4h"] > dataframe["plus_di_4h"]
        else:
            cond_4h_di_short = pd.Series(True, index=dataframe.index)
        cond_macro_safe_short = (dataframe["is_macro_bull_run_1d"].fillna(0) == 0) if "is_macro_bull_run_1d" in dataframe.columns else pd.Series(True, index=dataframe.index)
        cond_1d_short = (dataframe["is_bull_wave_1d"].fillna(0) == 0) if (self.block_1d_counter_wave.value and "is_bull_wave_1d" in dataframe.columns) else pd.Series(True, index=dataframe.index)
        cond_floor_safe = (((dataframe["close"] - dataframe["swing_low_20d_1d"]) / dataframe["swing_low_20d_1d"]) >= 0.035) if "swing_low_20d_1d" in dataframe.columns else pd.Series(True, index=dataframe.index)
        cond_capitulation_safe = (dataframe["is_deep_capitulation_1d"].fillna(0) == 0) if "is_deep_capitulation_1d" in dataframe.columns else pd.Series(True, index=dataframe.index)

        cond_not_chasing_short = (
            ((dataframe["ema_21"] - dataframe["close"]) / dataframe["close"] <= (s_max_dist / 100.0)) &
            (dataframe["rsi"] >= self.short_min_rsi_entry.value)
        )

        is_chop_squeeze = pd.Series(False, index=dataframe.index)
        if "bb_width_4h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | (dataframe["bb_width_4h"] < 0.02)
        if "adx_4h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | (dataframe["adx_4h"] < self.min_adx_4h.value)
        if "bb_width_1d" in dataframe.columns and "range_24h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | ((dataframe["bb_width_1d"] < 0.10) & (dataframe["range_24h"] < 0.022))
        cond_sideway_safe = ~is_chop_squeeze

        raw_signal_short = (
            is_decisive_red & is_breakdown_major & is_below_emas &
            cond_1h_short & cond_4h_short & cond_4h_di_short & cond_macro_safe_short & cond_1d_short &
            cond_floor_safe & cond_capitulation_safe & cond_sideway_safe &
            cond_not_chasing_short & (dataframe["volume"] > 0)
        )

        # ---------------------------------------------------------------------
        # B. NHÁNH LONG SNIPER (ĐẢO NGƯỢC CHUẨN XÁC)
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

        cond_4h_long = (dataframe["macdhist_4h"] > 0) if "macdhist_4h" in dataframe.columns else pd.Series(True, index=dataframe.index)

        cond_1d_long = (
            (dataframe["close"] > dataframe["ema_50_1d"]) & 
            ((dataframe["macdhist_1d"] > 0) if self.long_block_1d_macd.value else True)
        ) if ("ema_50_1d" in dataframe.columns and "macdhist_1d" in dataframe.columns) else pd.Series(True, index=dataframe.index)

        cond_macro_safe_long = (dataframe["is_macro_bear_market_1d"].fillna(0) == 0) if "is_macro_bear_market_1d" in dataframe.columns else pd.Series(True, index=dataframe.index)

        cond_overextended_safe = (
            dataframe["dist_from_ema_200_1d"] <= self.long_overextended_dist_max.value
        ) if "dist_from_ema_200_1d" in dataframe.columns else pd.Series(True, index=dataframe.index)

        cond_not_chasing_long = (
            ((dataframe["close"] - dataframe["ema_21"]) / dataframe["ema_21"] <= (l_max_dist / 100.0)) &
            (dataframe["rsi"] <= self.long_max_rsi_entry.value)
        )

        raw_signal_long = (
            is_decisive_green & is_breakout_major & is_above_emas &
            cond_1h_long & cond_4h_long & cond_1d_long & cond_macro_safe_long &
            cond_overextended_safe & cond_sideway_safe &
            cond_not_chasing_long & (dataframe["volume"] > 0)
        )

        # ---------------------------------------------------------------------
        # C. ÁP DỤNG COOLDOWN ĐỘC LẬP CHO SHORT & LONG
        # ---------------------------------------------------------------------
        s_cooldown = self.short_cooldown_candles.value
        entry_mask_short = pd.Series(False, index=dataframe.index)
        last_short_idx = -9999
        for idx in dataframe.index[raw_signal_short].tolist():
            if (idx - last_short_idx) >= s_cooldown:
                entry_mask_short.loc[idx] = True
                last_short_idx = idx

        l_cooldown = self.long_cooldown_candles.value
        entry_mask_long = pd.Series(False, index=dataframe.index)
        last_long_idx = -9999
        for idx in dataframe.index[raw_signal_long].tolist():
            if (idx - last_long_idx) >= l_cooldown:
                entry_mask_long.loc[idx] = True
                last_long_idx = idx

        dataframe.loc[entry_mask_short, "enter_tag"] = "entry_short_mtf_sniper"
        dataframe.loc[entry_mask_short, "enter_short"] = 1

        dataframe.loc[entry_mask_long, "enter_tag"] = "entry_long_mtf_sniper"
        dataframe.loc[entry_mask_long, "enter_long"] = 1

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
                if profit < -0.10:
                    consecutive_losses += 1
                elif profit > 0.0:
                    break

            if consecutive_losses >= 3:
                last_loss_date = recent_trades[0].close_date
                if last_loss_date:
                    if (current_time.timestamp() - last_loss_date.timestamp()) < (3 * 86400):
                        return False

        # --- LỚP 2: PORTFOLIO CORRELATION GUARD ---
        open_trades = Trade.get_trades_proxy(is_open=True)
        for ot in open_trades:
            # a. Không mở 2 lệnh ngược chiều nhau trên cùng danh mục (đang Long BTC thì không Short ETH)
            ot_side = "short" if ot.is_short else "long"
            if ot_side != side:
                return False

            # b. Nếu lệnh cặp khác đang âm > 0.8% trên chart (-8% ROE với x10), cấm mở lệnh thứ 2
            if ot.pair != pair:
                df_other, _ = self.dp.get_analyzed_dataframe(ot.pair, self.timeframe)
                if df_other is not None and not df_other.empty:
                    curr_row = df_other.loc[df_other["date"] <= current_time]
                    if not curr_row.empty:
                        curr_price = curr_row.iloc[-1]["close"]
                        profit_ratio = ot.calc_profit_ratio(curr_price)
                        if profit_ratio < -0.08:
                            return False

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

        # Cắt sớm khi xu hướng bị gãy cấu trúc (Break of Structure sau >= 2h nếu lệnh đang âm)
        if duration_minutes >= 120 and current_profit < -0.003:
            if trade.is_short:
                cond_structure_broken = r.get("close", 0) > r.get("swing_high_12", 0)
                cond_htf_reversal = (
                    (r.get("close_1h", 0) > r.get("ema_50_1h", 0)) and
                    (r.get("macdhist_1h", 0) > 0) and
                    (r.get("macdhist", 0) > 0)
                )
                if cond_structure_broken or cond_htf_reversal:
                    return "exit_short_trend_invalidated"
            else:
                # Long: cắt nếu gãy đáy swing low 12 nến hoặc 1H đảo chiều sập sâu
                cond_structure_broken = r.get("close", 0) < r.get("swing_low_12", 0)
                cond_htf_reversal = (
                    (r.get("close_1h", 0) < r.get("ema_50_1h", 0)) and
                    (r.get("macdhist_1h", 0) < 0) and
                    (r.get("macdhist", 0) < 0)
                )
                if cond_structure_broken or cond_htf_reversal:
                    return "exit_long_trend_invalidated"

        # Thời hạn tối đa 24h
        if duration_minutes >= 24 * 60:
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
        """
        Quản trị Stoploss động:
        1. Nhánh Long: Áp dụng Trailing bậc thang mở rộng để gồng trend trong Bull Run:
           - Lãi >= +20% ROE (+2% chart): Dời SL về Breakeven (+2% ROE / +0.2% chart).
           - Lãi >= +35% ROE (+3.5% chart): Khóa chắc +10% ROE (+1.0% chart).
           - Lãi >= +50% ROE (+5% chart): Khóa chắc +25% ROE (+2.5% chart).
           - Lãi >= +80% ROE (+8% chart): Trailing bám cách đỉnh 30% ROE (3.0% chart).
           - Lãi >= +120% ROE (+12% chart): Trailing siết chặt 20% ROE (2.0% chart).
        2. Nhánh Short: Giữ nguyên logic bảo vệ gốc:
           - Khi lãi >= +35% ROE: Kích hoạt Trailing Stop bám sát 20% ROE.
           - Khi lãi >= +20% ROE: Kéo Stoploss về Breakeven (+10% ROE).
        3. Sau 24h nếu âm: Thắt chặt SL về -5% ROE (-0.5% chart).
        4. Mặc định: Giữ SL kỹ thuật theo cặp (BTC: -25% ROE, ETH: -28% ROE).
        """
        if not trade.is_short:
            if current_profit >= 1.20:
                return -0.20
            if current_profit >= 0.80:
                return -0.30
            if current_profit >= 0.50:
                return -stoploss_from_open(0.25, current_profit, is_short=False, leverage=10.0)
            if current_profit >= 0.35:
                return -stoploss_from_open(0.10, current_profit, is_short=False, leverage=10.0)
            if current_profit >= 0.20:
                return -stoploss_from_open(0.02, current_profit, is_short=False, leverage=10.0)
        else:
            if current_profit >= 0.35:
                return -0.20
            if current_profit >= 0.20:
                return -stoploss_from_open(0.10, current_profit, is_short=True, leverage=10.0)

        duration_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if duration_hours >= 24 and current_profit < 0.0:
            sl_rel = stoploss_from_open(-0.05, current_profit, is_short=trade.is_short, leverage=10.0)
            return -max(sl_rel, 0.01)

        pair_cfg = self._get_pair_params(pair)
        base_sl = pair_cfg.get("stoploss", self.stoploss)
        if base_sl > self.stoploss:
            sl_rel = stoploss_from_open(base_sl, current_profit, is_short=trade.is_short, leverage=10.0)
            return -max(sl_rel, 0.01)

        return self.stoploss
