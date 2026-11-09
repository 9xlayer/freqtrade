# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
"""
Chiến lược kiểm thử điểm thoát LONG Đa khung thời gian (MTF Long Exit Confluence).
Inverted Symmetry từ MTF Short Exit:
- Cửa 1: Cắt xuống RSI-MA từ vùng Quá mua (Standard MA Cross-down)
- Cửa 2: Thoát nhanh A-Top Climax (Bán tháo đỉnh nhọn / Shooting Star / Dark Cloud Cover)
- Cửa 3: Phân kỳ âm RSI (Bearish Divergence - King of Tops)
- Bộ lọc kiệt quệ dải trên Bollinger Bands / ATR
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional, Union

from freqtrade.strategy import (
    IStrategy,
    informative,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
    CategoricalParameter,
)
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib


class StrategyMTFLongExit(IStrategy):
    INTERFACE_VERSION = 3

    # Thời gian giữ vị thế & Timeframe
    timeframe = "15m"
    can_short: bool = False  # Chỉ đánh LONG

    # ROI & Stoploss cấu hình nền tảng
    minimal_roi = {
        "0": 1.0  # Để tín hiệu Exit kích hoạt chốt lời chủ động
    }

    stoploss = -0.05  # Cắt lỗ bảo hiểm tài khoản 5%
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Số lượng nến khởi động
    startup_candle_count: int = 60

    # ==================== HYPEROPTABLE PARAMETERS ====================
    # 1. Cấu hình vùng đỉnh RSI
    rsi_overbought_level = DecimalParameter(65.0, 80.0, default=68.0, decimals=1, space="sell", optimize=True)
    rsi_floor_level = DecimalParameter(52.0, 62.0, default=58.0, decimals=1, space="sell", optimize=True)
    overbought_lookback = IntParameter(3, 12, default=6, space="sell", optimize=True)

    # 2. Bộ lọc kéo dãn biên độ dải trên Bollinger Bands / ATR (Exhaustion)
    use_bb_exhaustion = BooleanParameter(default=True, space="sell", optimize=True)

    # 3. Các cửa kích hoạt thoát Long độc lập
    use_bear_divergence = BooleanParameter(default=True, space="sell", optimize=True)
    use_a_climax = BooleanParameter(default=True, space="sell", optimize=True)
    ac_ob_level = DecimalParameter(70.0, 80.0, default=74.0, decimals=1, space="sell", optimize=True)
    ac_min_delta_rsi = DecimalParameter(4.0, 10.0, default=5.0, decimals=1, space="sell", optimize=True)
    ac_vol_climax_mult = DecimalParameter(1.8, 3.0, default=2.2, decimals=1, space="sell", optimize=True)
    ac_wick_ratio_min = DecimalParameter(0.35, 0.60, default=0.45, decimals=2, space="sell", optimize=True)
    ac_require_piercing = BooleanParameter(default=True, space="sell", optimize=True)

    # 4. Công tắc bộ lọc đa khung thời gian
    use_macd_support = BooleanParameter(default=True, space="sell", optimize=True)
    use_filter_30m = BooleanParameter(default=False, space="sell", optimize=True)
    use_filter_1h = BooleanParameter(default=False, space="sell", optimize=True)

    # ==================== INFORMATIVE TIMEFRAMES ====================
    @informative("30m")
    def populate_indicators_30m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_ma"] = ta.EMA(dataframe["rsi"], timeperiod=14)
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]
        return dataframe

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]
        return dataframe

    # ==================== MAIN 15M INDICATORS ====================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # EMA xác định xu hướng Long
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)

        # 15M RSI & RSI-EMA
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_ma"] = ta.EMA(dataframe["rsi"], timeperiod=14)
        dataframe["delta_rsi_drop"] = dataframe["rsi"].shift(1) - dataframe["rsi"]

        # 15M MACD
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        # Bollinger Bands & ATR cho bộ lọc Exhaustion
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe["bb_lower"] = bollinger["lower"]
        dataframe["bb_mid"] = bollinger["mid"]
        dataframe["bb_upper"] = bollinger["upper"]
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Volume Climax & Upper Wick (Shooting Star / Rejection)
        dataframe["vol_ma"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["vol_ratio"] = dataframe["volume"] / dataframe["vol_ma"]
        candle_range = dataframe["high"] - dataframe["low"]
        upper_wick = dataframe["high"] - np.maximum(dataframe["open"], dataframe["close"])
        dataframe["wick_ratio"] = np.where(candle_range > 0, upper_wick / candle_range, 0.0)

        return dataframe

    # ==================== LONG ENTRY LOGIC ====================
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Chiến lược vào lệnh Long theo đà tăng:
        - Giá nằm trên EMA 20 và EMA 20 > EMA 50 (Xu hướng tăng)
        - RSI 15M > 52 (Phe mua kiểm soát)
        - MACD Histogram > 0 (Động lượng dương)
        """
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = None

        conditions = [
            dataframe["close"] > dataframe["ema_20"],
            dataframe["ema_20"] > dataframe["ema_50"],
            dataframe["rsi"] > 52,
            dataframe["macdhist"] > 0,
            dataframe["volume"] > 0,
        ]

        if conditions:
            dataframe.loc[
                np.all(conditions, axis=0),
                "enter_long"
            ] = 1

        return dataframe

    # ==================== MTF LONG EXIT LOGIC ====================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        CHUẨN HOÁ LOGIC ĐÓNG LỆNH LONG ĐA KHUNG THỜI GIAN:
        1. Vùng đỉnh RSI 15M:
           - Từng chạm quá mua (>= rsi_overbought_level) trong các nến gần nhất
           - Tại nến trigger: RSI nằm trên sàn (>= rsi_floor_level), chặn vùng trung tính 40-55
           - RSI đang dốc xuống (RSI < RSI[1])
        2. Bộ lọc kiệt quệ dải trên Bollinger Bands / ATR
        3. 3 Cánh cửa độc lập:
           - Cửa 1: Cắt xuống RSI-MA tiêu chuẩn
           - Cửa 2: A-Top Climax (Shooting Star / Dark Cloud Cover nuốt 50% nến trước)
           - Cửa 3: Phân kỳ âm RSI (Bearish Divergence)
        """
        # 1. Điều kiện chung RSI 15M tại vùng đỉnh
        cond_from_overbought = (
            dataframe["rsi"].rolling(self.overbought_lookback.value).max() >= self.rsi_overbought_level.value
        )
        cond_at_top_zone = dataframe["rsi"] >= self.rsi_floor_level.value
        cond_rsi_sloping_down = dataframe["rsi"] < dataframe["rsi"].shift(1)

        # Bộ lọc kéo dãn biên độ kiệt quệ Bollinger Bands dải trên / ATR
        if self.use_bb_exhaustion.value:
            cond_bb_exhaustion = (
                (dataframe["high"].rolling(3).max() >= dataframe["bb_upper"]) |
                ((dataframe["high"] - dataframe["bb_mid"]) >= 1.8 * dataframe["atr"])
            )
        else:
            cond_bb_exhaustion = pd.Series(True, index=dataframe.index)

        # [CỬA 1]: Thoát tiêu chuẩn MA Cross-down
        cond_rsi_cross_ma = qtpylib.crossed_below(dataframe["rsi"], dataframe["rsi_ma"])
        cond_std_exit = (
            cond_from_overbought & cond_at_top_zone & cond_rsi_sloping_down & cond_rsi_cross_ma & cond_bb_exhaustion
        )

        # [CỬA 2]: Thoát nhanh A-Top Climax (Shooting Star / Dark Cloud Cover)
        if self.use_a_climax.value:
            candle_is_red = dataframe["close"] < dataframe["open"]
            dark_cloud = dataframe["close"] < ((dataframe["open"].shift(1) + dataframe["close"].shift(1)) / 2)
            cond_pierce_ok = dark_cloud if self.ac_require_piercing.value else pd.Series(True, index=dataframe.index)

            vol_climax = (dataframe["vol_ratio"] >= self.ac_vol_climax_mult.value) | (
                dataframe["vol_ratio"].shift(1) >= self.ac_vol_climax_mult.value
            )
            wick_rejection = (dataframe["wick_ratio"] >= self.ac_wick_ratio_min.value) | (
                dataframe["wick_ratio"].shift(1) >= self.ac_wick_ratio_min.value
            )
            extreme_ob = dataframe["rsi"].rolling(4).max() >= self.ac_ob_level.value
            rsi_plunge = (dataframe["delta_rsi_drop"] >= 8.0) & candle_is_red & cond_pierce_ok

            cond_a_climax_exit = (
                extreme_ob &
                cond_at_top_zone &
                cond_bb_exhaustion &
                candle_is_red &
                (rsi_plunge | (vol_climax & wick_rejection & cond_pierce_ok & (dataframe["delta_rsi_drop"] >= self.ac_min_delta_rsi.value)))
            )
        else:
            cond_a_climax_exit = pd.Series(False, index=dataframe.index)

        # [CỬA 3]: Thoát do Phân kỳ âm RSI (Bearish Divergence)
        if self.use_bear_divergence.value:
            recent_p = dataframe["high"].rolling(3).max()
            recent_r = dataframe["rsi"].rolling(3).max()
            prior_p = dataframe["high"].shift(3).rolling(15).max()
            prior_r = dataframe["rsi"].shift(3).rolling(15).max()

            cond_bear_div = (
                (recent_p > prior_p) &
                (recent_r < prior_r) &
                (prior_r >= self.rsi_overbought_level.value) &
                cond_from_overbought &
                cond_at_top_zone &
                cond_rsi_sloping_down
            )
            cond_div_exit = cond_bear_div
        else:
            cond_div_exit = pd.Series(False, index=dataframe.index)

        # 4. Động lượng MACD 15M hỗ trợ
        if self.use_macd_support.value:
            macd_curling_down = dataframe["macd"] <= dataframe["macd"].shift(1)
            hist_shrinking = dataframe["macdhist"] <= dataframe["macdhist"].shift(1)
            cond_15m_macd = hist_shrinking | macd_curling_down
        else:
            cond_15m_macd = pd.Series(True, index=dataframe.index)

        # 5. Bộ lọc đa khung 30M
        if self.use_filter_30m.value:
            cond_30m = (
                (dataframe["rsi_30m"] < dataframe["rsi_ma_30m"]) &
                (dataframe["macdhist_30m"] <= dataframe["macdhist_30m"].shift(1))
            )
        else:
            cond_30m = pd.Series(True, index=dataframe.index)

        # 6. Bộ lọc bối cảnh 1H
        if self.use_filter_1h.value:
            cond_1h = (
                (dataframe["rsi_1h"] >= 65.0) &
                (dataframe["macdhist_1h"] <= dataframe["macdhist_1h"].shift(1))
            )
        else:
            cond_1h = pd.Series(True, index=dataframe.index)

        # Gán nhãn các cửa thoát Long
        dataframe["exit_long"] = 0
        dataframe["exit_tag"] = None

        cond_core_div = cond_div_exit & cond_at_top_zone & cond_15m_macd & cond_30m & cond_1h
        cond_core_climax = cond_a_climax_exit & cond_15m_macd & cond_30m & cond_1h
        cond_core_std = cond_std_exit & cond_15m_macd & cond_30m & cond_1h

        # Ưu tiên tag: Phân kỳ âm -> A-Climax -> Standard MA Cross
        dataframe.loc[cond_core_std, "exit_long"] = 1
        dataframe.loc[cond_core_std, "exit_tag"] = "exit_rsi_top_cross"

        dataframe.loc[cond_core_climax, "exit_long"] = 1
        dataframe.loc[cond_core_climax, "exit_tag"] = "exit_a_top_climax"

        dataframe.loc[cond_core_div, "exit_long"] = 1
        dataframe.loc[cond_core_div, "exit_tag"] = "exit_bear_divergence"

        return dataframe
