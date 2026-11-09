import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from freqtrade.strategy import (
    IStrategy,
    informative,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
)
import talib.abstract as ta
try:
    from technical import qtpylib
except ImportError:
    import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.persistence import Trade


class StrategyMTFShortExit(IStrategy):
    """
    Chiến lược giao dịch Short tích hợp bộ thoát lệnh Đa Khung Thời Gian (MTF Confluence):
    - 1H:  Bộ lọc cạn kiệt lực bán vĩ mô (Seller Exhaustion Filter)
    - 30M: Xác nhận chuyển pha & giữ cấu trúc hồi phục (Confirmation Setup)
    - 15M: Kích hoạt đóng lệnh ngay chân râu nến đáy (RSI bứt phá từ quá bán cắt lên MA)
    """

    INTERFACE_VERSION = 3

    # Khung thời gian chính
    timeframe = "15m"

    # Cho phép giao dịch Short (Futures)
    can_short: bool = True

    # Quản lý lệnh
    stoploss = -0.05
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    process_only_new_candles = True

    # ROI fallback
    minimal_roi = {
        "0": 1.0  # 100% fallback, để nhường toàn bộ quyền đóng lệnh cho MTF Exit Signal
    }

    # ==================== THAM SỐ CẤU HÌNH (HYPEROPT SẴN SÀNG) ====================
    # 1. Cấu hình vùng đáy RSI
    rsi_oversold_level = DecimalParameter(20.0, 35.0, default=32.0, decimals=1, space="sell", optimize=True)
    rsi_ceiling_level = DecimalParameter(30.0, 45.0, default=42.0, decimals=1, space="sell", optimize=True)
    oversold_lookback = IntParameter(3, 10, default=6, space="sell", optimize=True)

    # 2. Thoát nhanh V-Bounce khi RSI dựng đứng xa MA
    # Cấu hình Cửa 2: V-Bounce Climax Chống nhiễu (Anti-Noise Strict Climax)
    use_v_bounce = BooleanParameter(default=True, space="sell", optimize=True)
    vb_os_level = DecimalParameter(20.0, 30.0, default=26.0, decimals=1, space="sell", optimize=True)
    vb_min_delta_rsi = DecimalParameter(4.0, 10.0, default=5.0, decimals=1, space="sell", optimize=True)
    vb_vol_climax_mult = DecimalParameter(1.8, 3.0, default=2.2, decimals=1, space="sell", optimize=True)
    vb_wick_ratio_min = DecimalParameter(0.35, 0.60, default=0.45, decimals=2, space="sell", optimize=True)
    vb_require_piercing = BooleanParameter(default=True, space="sell", optimize=True)

    # 3. Bộ lọc kéo dãn biên độ kiệt quệ Bollinger Bands / ATR
    use_bb_exhaustion = BooleanParameter(default=True, space="sell", optimize=True)

    # 4. Nhận diện Phân kỳ dương RSI (Bullish Divergence)
    use_bull_divergence = BooleanParameter(default=True, space="sell", optimize=True)

    # 5. Công tắc bật/tắt bộ lọc đa khung
    use_macd_supp = BooleanParameter(default=True, space="sell", optimize=True)
    use_filter_30m = BooleanParameter(default=False, space="sell", optimize=True)
    use_filter_1h = BooleanParameter(default=False, space="sell", optimize=True)

    # ==================== 1H INFORMATIVE TIMEFRAME ====================
    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI 1H
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # MACD 1H
        macd = ta.MACD(dataframe)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        dataframe["rsi_ma"] = ta.EMA(dataframe["rsi"], timeperiod=14)
        dataframe["volume_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_99"] = ta.EMA(dataframe, timeperiod=99)
        return dataframe

    # ==================== 30M INFORMATIVE TIMEFRAME ====================
    @informative("30m")
    def populate_indicators_30m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI 30M & Đường trung bình SMA của RSI
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_ma"] = ta.SMA(dataframe["rsi"], timeperiod=14)

        # MACD 30M
        macd = ta.MACD(dataframe)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]
        return dataframe

    # ==================== 15M BASE INDICATORS ====================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 1. RSI 15M & RSI-MA (Đường tím & Đường vàng)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_ma"] = ta.SMA(dataframe["rsi"], timeperiod=14)

        # 2. MACD 15M
        macd = ta.MACD(dataframe)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        # 3. Bollinger Bands & ATR (Phục vụ đo độ kéo dãn biên độ & Anti-BB Walking)
        bollinger = qtpylib.bollinger_bands(dataframe["close"], window=20, stds=2)
        dataframe["bb_lower"] = bollinger["lower"]
        dataframe["bb_mid"] = bollinger["mid"]
        dataframe["bb_upper"] = bollinger["upper"]

        # 4. Phục vụ nhận diện V-Bounce Climax & Rejection Wick
        dataframe["vol_ma_20"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["vol_ratio"] = dataframe["volume"] / np.where(dataframe["vol_ma_20"] > 0, dataframe["vol_ma_20"], 1)
        lower_wick = np.minimum(dataframe["open"], dataframe["close"]) - dataframe["low"]
        upper_wick = dataframe["high"] - np.maximum(dataframe["open"], dataframe["close"])
        candle_range = dataframe["high"] - dataframe["low"]
        dataframe["wick_ratio"] = lower_wick / np.where(candle_range > 0, candle_range, 1)
        dataframe["upper_wick_ratio"] = upper_wick / np.where(candle_range > 0, candle_range, 1)
        dataframe["delta_rsi"] = dataframe["rsi"] - dataframe["rsi"].shift(1)

        # 5. Chỉ báo hỗ trợ Entry Short (Breakdown xu hướng)
        dataframe["ema_10"] = ta.EMA(dataframe, timeperiod=10)
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        return dataframe

    # ==================== SHORT ENTRY LOGIC ====================
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Chiến lược vào lệnh Short Sniper MTF Confluence V3 (Đã tối ưu triệt tiêu tín hiệu giả):
        1. CÒ BÓP TỨC THỜI (Trigger Point):
           - RSI 15M vừa cắt xuống RSI-MA (qtpylib.crossed_below)
           - Nến giảm (close <= open) hoặc có râu từ chối giá trên (upper_wick >= 35%)
        2. BỘ LỌC BOLLINGER BANDS & MOMENTUM (Anti-BB Walking):
           - CẤM Short khi chuỗi nến trước bám dải trên: bắt buộc close < ema_10 và close < bb_upper
        3. BỘ LỌC ĐỘNG LƯỢNG RSI (Anti-Rising RSI):
           - RSI 15M đang dốc xuống (rsi < rsi.shift(1))
        4. BỘ LỌC XU HƯỚNG VĨ MÔ (Trend Guard 1H):
           - Chống Short khi 1H đang Super Uptrend: không cho Short nếu close_1h > ema_21_1h > ema_99_1h
        5. KHUNG 1H & 30M:
           - RSI 1H ở vùng đỉnh phân phối (56.0 <= rsi_1h <= 78.0)
           - MACD 1H & 30M suy yếu (macd < signal & hist < 0)
        6. CEILING BOX & CHỐNG PHÁ ĐỈNH:
           - Giá ở trong biên 1.8% của đỉnh 160 nến gần nhất (40h)
           - Chống Short nến xanh phá đỉnh cao mới (Anti-Parabolic ATH)
        7. CHỐNG BÁN ĐÁY:
           - RSI 15M >= 38, RSI 1H >= 45
        """
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = None

        # 1. Trigger tức thời 15M: Cò cắt dứt khoát RSI xuống dưới RSI-MA
        cond_trigger_15m = qtpylib.crossed_below(dataframe["rsi"], dataframe["rsi_ma"])
        cond_candle_bear = (dataframe["close"] <= dataframe["open"]) | (dataframe["upper_wick_ratio"] >= 0.35)
        cond_15m_trigger = cond_trigger_15m & cond_candle_bear & (dataframe["macd"] < dataframe["macdsignal"])

        # 2. Bộ lọc Bollinger Bands (Anti-BB Walking) & EMA 10
        was_walking_upper_bb = (
            (dataframe["high"].rolling(4).max() >= dataframe["bb_upper"] * 0.998) |
            (dataframe["close"].rolling(3).max() >= dataframe["bb_upper"] * 0.995)
        )
        cond_bb_safe = np.where(
            was_walking_upper_bb,
            (dataframe["close"] < dataframe["ema_10"]) & (dataframe["close"] < dataframe["bb_upper"] * 0.992),
            (dataframe["close"] < dataframe["bb_upper"])
        )
        cond_below_ema10 = dataframe["close"] < dataframe["ema_10"]

        # 3. Bộ lọc Động lượng RSI (Anti-Rising RSI: RSI phải dốc xuống)
        cond_rsi_sloping_down = dataframe["rsi"] < dataframe["rsi"].shift(1)

        # 4. Khung 1H: MACD bán & RSI vùng đỉnh phân phối (nâng sàn lên 56.0)
        cond_1h_macd = (dataframe["macd_1h"] < dataframe["macdsignal_1h"]) & (dataframe["macdhist_1h"] < 0)
        cond_1h_rsi  = (dataframe["rsi_1h"] < dataframe["rsi_ma_1h"]) & (dataframe["rsi_1h"] >= 56.0) & (dataframe["rsi_1h"] <= 78.0)
        cond_1h_ok   = cond_1h_macd & cond_1h_rsi

        # 5. Bộ lọc xu hướng lớn (Trend Guard 1H): Cấm Short khi 1H đang Super Uptrend
        is_super_uptrend_1h = (
            (dataframe["close_1h"] > dataframe["ema_21_1h"]) &
            (dataframe["ema_21_1h"] > dataframe["ema_99_1h"]) &
            (dataframe["ema_21_1h"] > dataframe["ema_21_1h"].shift(1))
        )
        cond_trend_guard = ~is_super_uptrend_1h | (dataframe["close_1h"] <= dataframe["ema_21_1h"]) | (dataframe["upper_wick_ratio"] >= 0.50)

        # 6. Khung 30M: MACD bán & RSI dưới MA
        cond_30m_macd = (dataframe["macd_30m"] < dataframe["macdsignal_30m"]) & (dataframe["macdhist_30m"] < 0)
        cond_30m_rsi  = (dataframe["rsi_30m"] < dataframe["rsi_ma_30m"])
        cond_30m_ok   = cond_30m_macd & cond_30m_rsi

        # 7. Cấu trúc Đỉnh phân phối & Chống phá đỉnh (Ceiling Box & Anti-Parabolic ATH)
        recent_peak = dataframe["high"].rolling(160).max()
        cond_at_ceiling = dataframe["high"] >= recent_peak * 0.982
        is_ath_breakout = (dataframe["high"] >= recent_peak.shift(1)) & (dataframe["close"] > dataframe["open"]) & (dataframe["upper_wick_ratio"] < 0.30)
        cond_not_ath_breakout = ~is_ath_breakout

        # 8. Bộ lọc chống bán đáy (Anti-Oversold)
        cond_not_oversold = (dataframe["rsi"] >= 38.0) & (dataframe["rsi_1h"] >= 45.0)

        # Tổng hợp điều kiện Short Sniper V3
        cond_full = (
            cond_15m_trigger &
            cond_bb_safe &
            cond_below_ema10 &
            cond_rsi_sloping_down &
            cond_1h_ok &
            cond_trend_guard &
            cond_30m_ok &
            cond_at_ceiling &
            cond_not_ath_breakout &
            cond_not_oversold &
            (dataframe["volume"] > 0)
        )

        dataframe.loc[cond_full, "enter_tag"] = "entry_mtf_sniper_v3"
        dataframe.loc[cond_full, "enter_short"] = 1

        return dataframe

    # ==================== MTF SHORT EXIT LOGIC ====================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        CHUẨN HOÁ LOGIC ĐÓNG LỆNH SHORT ĐA KHUNG THỜI GIAN:
        1. RSI 15M:
           - Từng rơi sâu vào quá bán (<= rsi_oversold_level) trong các nến gần nhất
           - Tại nến trigger: RSI nằm dưới trần (<= rsi_ceiling_level), triệt tiêu hoàn toàn vùng 40-60
           - RSI đang dốc dần lên
           - RSI cắt dứt khoát lên trên đường RSI-MA (đường vàng)
        2. MACD 15M:
           - Không cần Golden Cross, chỉ cần cong lên và Histogram co về trục 0
        3. Bộ lọc 30M & 1H (Tùy chọn kết hợp):
           - 30M: RSI > MA và Histogram co cụm
           - 1H: Cạn kiệt đà xả lớn
        """
        # 1. Điều kiện chung RSI 15M tại vùng đáy
        cond_from_oversold = (
            dataframe["rsi"].rolling(self.oversold_lookback.value).min() <= self.rsi_oversold_level.value
        )
        cond_at_bottom_zone = dataframe["rsi"] <= self.rsi_ceiling_level.value
        cond_rsi_sloping_up = dataframe["rsi"] > dataframe["rsi"].shift(1)

        # Bộ lọc kéo dãn biên độ kiệt quệ Bollinger Bands / ATR
        if self.use_bb_exhaustion.value:
            cond_bb_exhaustion = (
                (dataframe["low"].rolling(3).min() <= dataframe["bb_lower"]) |
                ((dataframe["bb_mid"] - dataframe["low"]) >= 1.8 * dataframe["atr"])
            )
        else:
            cond_bb_exhaustion = pd.Series(True, index=dataframe.index)

        # [CỬA 1]: Thoát tiêu chuẩn MA Crossover (Kèm lọc kéo dãn Bollinger)
        cond_rsi_cross_ma = qtpylib.crossed_above(dataframe["rsi"], dataframe["rsi_ma"])
        cond_std_exit = (
            cond_from_oversold & cond_at_bottom_zone & cond_rsi_sloping_up & cond_rsi_cross_ma & cond_bb_exhaustion
        )
        # [CỬA 2]: Thoát nhanh V-Bounce (Anti-Noise Strict Climax Reversal)
        if self.use_v_bounce.value:
            candle_is_green = dataframe["close"] > dataframe["open"]
            piercing = dataframe["close"] > ((dataframe["open"].shift(1) + dataframe["close"].shift(1)) / 2)
            cond_pierce_ok = piercing if self.vb_require_piercing.value else pd.Series(True, index=dataframe.index)

            vol_climax = (dataframe["vol_ratio"] >= self.vb_vol_climax_mult.value) | (
                dataframe["vol_ratio"].shift(1) >= self.vb_vol_climax_mult.value
            )
            wick_rejection = (dataframe["wick_ratio"] >= self.vb_wick_ratio_min.value) | (
                dataframe["wick_ratio"].shift(1) >= self.vb_wick_ratio_min.value
            )
            extreme_os = dataframe["rsi"].rolling(4).min() <= self.vb_os_level.value
            rsi_surge = (dataframe["delta_rsi"] >= 8.0) & candle_is_green & cond_pierce_ok

            cond_vbounce_exit = (
                extreme_os &
                cond_at_bottom_zone &
                cond_bb_exhaustion &
                candle_is_green &
                (rsi_surge | (vol_climax & wick_rejection & cond_pierce_ok & (dataframe["delta_rsi"] >= self.vb_min_delta_rsi.value)))
            )
        else:
            cond_vbounce_exit = pd.Series(False, index=dataframe.index)



        # [CỬA 3]: Thoát do Phân kỳ dương RSI (Bullish Divergence)
        if self.use_bull_divergence.value:
            recent_p = dataframe["low"].rolling(3).min()
            recent_r = dataframe["rsi"].rolling(3).min()
            prior_p = dataframe["low"].shift(3).rolling(15).min()
            prior_r = dataframe["rsi"].shift(3).rolling(15).min()

            cond_bull_div = (
                (recent_p < prior_p) &
                (recent_r > prior_r) &
                (prior_r <= self.rsi_oversold_level.value) &
                cond_from_oversold &
                cond_at_bottom_zone &
                cond_rsi_sloping_up
            )
        else:
            cond_bull_div = pd.Series(False, index=dataframe.index)

        # 2. Điều kiện MACD 15M hỗ trợ xu hướng (Histogram co cụm giảm tốc đà rơi ngay tại râu nến đáy)
        if self.use_macd_supp.value:
            cond_15m_macd_support = (
                (dataframe["macdhist"] >= dataframe["macdhist"].shift(1)) |
                (dataframe["macd"] >= dataframe["macd"].shift(1))
            )
        else:
            cond_15m_macd_support = pd.Series(True, index=dataframe.index)

        # 3. Điều kiện bộ lọc 30M
        if self.use_filter_30m.value:
            cond_30m_pass = (
                (dataframe["rsi_30m"] > dataframe["rsi_ma_30m"]) &
                (dataframe["macdhist_30m"] >= dataframe["macdhist_30m"].shift(1))
            )
        else:
            cond_30m_pass = pd.Series(True, index=dataframe.index)

        # 4. Điều kiện bộ lọc 1H
        if self.use_filter_1h.value:
            cond_1h_pass = (
                (dataframe["rsi_1h"].rolling(4).min() <= 35) &
                (dataframe["macdhist_1h"] >= dataframe["macdhist_1h"].shift(1))
            )
        else:
            cond_1h_pass = pd.Series(True, index=dataframe.index)

        # Khởi tạo mặc định
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = None

        # Tổng hợp tín hiệu thoát lệnh Short (3 Cửa độc lập)
        exit_mask_div = cond_bull_div & cond_15m_macd_support & cond_30m_pass & cond_1h_pass
        exit_mask_vbounce = cond_vbounce_exit & cond_15m_macd_support & cond_30m_pass & cond_1h_pass
        exit_mask_std = cond_std_exit & cond_15m_macd_support & cond_30m_pass & cond_1h_pass

        dataframe.loc[exit_mask_std, "exit_short"] = 1
        dataframe.loc[exit_mask_std, "exit_tag"] = "exit_rsi_bottom_cross"

        dataframe.loc[exit_mask_vbounce, "exit_short"] = 1
        dataframe.loc[exit_mask_vbounce, "exit_tag"] = "exit_v_bounce_surge"

        dataframe.loc[exit_mask_div, "exit_short"] = 1
        dataframe.loc[exit_mask_div, "exit_tag"] = "exit_bull_divergence"

        return dataframe
