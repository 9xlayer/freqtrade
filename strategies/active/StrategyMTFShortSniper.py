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


class StrategyMTFShortSniper(IStrategy):

    """
    CHIẾN LƯỢC TOÀN DIỆN: MTF SHORT EXHAUSTION & DYNAMIC REJECTION (SNIPER)
    ====================================================================
    Tích hợp trọn vẹn:
    1. ĐIỂM VÀO LỆNH (ENTRY POINT - 15M / 1H / 4H Triad Confluence):
       - 15M: Nến đỏ xả áp đảo, thủng đáy nền tích luỹ 20 nến (Major Breakdown), đóng dưới EMA 10 & 21.
       - 1H:  Kiểm định vị thế cản EMA 50 1H (chống Short đáy vực), MACD 1H suy kiệt,
              Phe Bò từng có nhịp rướn kiệt sức trong 24h qua (RSI 1H >= 58).
       - 4H:  Chặn tuyệt đối các pha Short ngược Sóng Hồi Long bùng nổ của khung 4H (4H MACD Hist > 0 & tăng).
       - Cooldown: Giãn cách 24h ngăn spam lệnh khi thị trường sập sâu.

    2. ĐIỂM THOÁT LỆNH (EXIT POINT - MTF Confluence & V-Bounce):
       - 1H:  Bộ lọc cạn kiệt lực bán hoảng loạn (Seller Exhaustion Filter).
       - 30M: Xác nhận chuyển dịch động lượng (RSI 30M > MA).
       - 15M: Kích hoạt chốt lời ngay sát râu nến đáy khi RSI cắt lên RSI-MA từ vùng quá bán,
              kèm cơ chế thoát nhanh V-Bounce khi có Volume Climax và râu rút chân mạnh.
    """

    INTERFACE_VERSION = 3

    # Khung thời gian cơ sở (Trigger)
    timeframe = "15m"

    # Cho phép mở vị thế Short (Futures)
    can_short: bool = True

    # Số lượng nến khởi động để nạp dữ liệu quá khứ cho các chỉ báo đa khung (15m, 1h, 4h, 1d)
    startup_candle_count: int = 2400

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        """
        Đòn bẩy 10x cho tài khoản Futures
        """
        return 10.0

    # Cấu hình lệnh
    stoploss = -0.28  # Trần cắt lỗ tối đa -28% ký quỹ (= -2.8% chart), Custom Stoploss sẽ siết về -25% (-2.5%) cho BTC
    use_custom_stoploss = True
    use_exit_signal = False  # Để lệnh có đủ không gian dao động hốt trọn +3.5% đến +4.5%
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    process_only_new_candles = True

    # Bảng thông số đo ni đóng giày theo từng cặp tiền (Pair-specific Optimization)
    custom_pair_params = {
        "BTC/USDT:USDT": {
            "stoploss": -0.25,          # SL -2.5% trên chart (-25% ký quỹ x10)
            "max_dist_ema21": 1.0,      # Retest nến 1.0%
            "candle_body_ratio_min": 0.62,
        },
        "ETH/USDT:USDT": {
            "stoploss": -0.28,          # SL -2.8% trên chart (-28% ký quỹ x10)
            "max_dist_ema21": 1.2,      # Co giãn 1.2% theo hệ số biến động của ETH
            "candle_body_ratio_min": 0.62,
        },
    }

    def _get_pair_params(self, pair: str) -> dict:
        for k, v in self.custom_pair_params.items():
            if k == pair or k.split(":")[0] == pair.split(":")[0]:
                return v
        return self.custom_pair_params.get("BTC/USDT:USDT", {})

    # ROI mục tiêu: Thả trần để nuôi lệnh ăn sóng lớn (Chỉ chốt cố định khi lãi cực lớn +100% ký quỹ = 10% chart)
    minimal_roi = {
        "0": 1.00,    # +100% ký quỹ (= +10.0% trên chart)
        "720": 0.70,  # Sau 12 tiếng +70% ký quỹ (= +7.0% trên chart)
    }

    # =========================================================================
    # 1. THAM SỐ CẤU HÌNH ENTRY (HYPEROPT SẴN SÀNG)
    # =========================================================================
    # 1H Confluence
    loc_tolerance_1h = DecimalParameter(0.990, 1.005, default=0.991, decimals=3, space="buy", optimize=True)
    min_1h_rsi_exhaustion = DecimalParameter(52.0, 70.0, default=55.1, decimals=1, space="buy", optimize=True)
    
    # 4H Confluence
    block_4h_long_wave = BooleanParameter(default=True, space="buy", optimize=True)
    
    # 1D Macro Confluence (Bộ lọc Vĩ mô: Chống bão Siêu Uptrend khung Ngày)
    block_1d_bull_wave = BooleanParameter(default=True, space="buy", optimize=True)

    # 15M Price Action
    lookback_support = IntParameter(12, 30, default=17, space="buy", optimize=True)
    candle_body_ratio_min = DecimalParameter(0.45, 0.70, default=0.62, decimals=2, space="buy", optimize=True)
    max_dist_ema21 = DecimalParameter(0.5, 2.5, default=1.0, decimals=1, space="buy", optimize=True)
    min_rsi_entry = DecimalParameter(20.0, 35.0, default=27.8, decimals=1, space="buy", optimize=True)

    # Cooldown giữa các lần kích hoạt Entry (Số nến 15M)
    cooldown_candles = IntParameter(48, 120, default=96, space="buy", optimize=True)  # 96 nến 15M = tròn 24.0 Giờ (chu kỳ Daily)

    # =========================================================================
    # 2. THAM SỐ CẤU HÌNH EXIT (CHỐT LỜI ĐÁY)
    # =========================================================================
    # Thoát tiêu chuẩn: RSI 15M cắt lên RSI-MA từ vùng quá bán
    exit_rsi_oversold = DecimalParameter(22.0, 35.0, default=23.0, decimals=1, space="sell", optimize=True)
    exit_rsi_ceiling = DecimalParameter(32.0, 45.0, default=44.3, decimals=1, space="sell", optimize=True)
    exit_oversold_lookback = IntParameter(3, 10, default=9, space="sell", optimize=True)

    # Thoát nhanh V-Bounce (Rút chân mạnh đột biến kèm Volume Climax)
    use_v_bounce_exit = BooleanParameter(default=True, space="sell", optimize=True)
    vb_min_vol_mult = DecimalParameter(1.8, 3.0, default=2.7, decimals=1, space="sell", optimize=True)
    vb_min_lower_wick = DecimalParameter(0.35, 0.60, default=0.52, decimals=2, space="sell", optimize=True)

    # =========================================================================
    # 2b. KHUNG SIÊU VĨ MÔ NGÀY (INFORMATIVE 1D - LỚP 1 CHỐNG BÃO UPTREND)
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

        # 1. Nhận diện Sóng Hồi Bò bùng nổ khung Ngày (1D): MACD 1D đang nở xanh dốc lên
        dataframe["is_bull_wave"] = (
            (dataframe["macdhist"] > 0) & 
            (dataframe["macdhist"] > dataframe["macdhist_prev"])
        )

        # 2. Nhận diện Siêu Chu kỳ Bull Run Vĩ mô:
        # Nhánh a: Golden Cross Bull Run: Giá > EMA 50 & EMA 50 > EMA 200 & RSI 1D > 60
        # Nhánh b: Xếp chồng Bull Stack hoàn hảo (Close > EMA 21 > 50 > 100 > 200) kèm xu hướng xác nhận (ADX 1D > 20)
        # Nhánh c: Siêu sóng bơm Parabolic vĩ mô cản tàu (ADX 1D > 50 & +DI 1D > -DI 1D & Close > EMA 200)
        is_golden_bull = (
            (dataframe["close"] > dataframe["ema_50"]) &
            (dataframe["ema_50"] > dataframe["ema_200"]) &
            (dataframe["rsi"] > 60)
        )
        is_bull_stack = (
            (dataframe["close"] > dataframe["ema_21"]) &
            (dataframe["ema_21"] > dataframe["ema_50"]) &
            (dataframe["ema_50"] > dataframe["ema_100"]) &
            (dataframe["ema_100"] > dataframe["ema_200"]) &
            (dataframe["adx"] > 20)
        )
        is_parabolic_surge = (
            (dataframe["adx"] > 50) &
            (dataframe["plus_di"] > dataframe["minus_di"]) &
            (dataframe["close"] > dataframe["ema_200"])
        )
        dataframe["is_macro_bull_run"] = is_golden_bull | is_bull_stack | is_parabolic_surge

        # 3. Nhận diện Vùng Đáy Kiệt sức Chu kỳ (Deep Capitulation Floor): Giá quá cách xa EMA 200 Ngày (< -28%)
        dataframe["is_deep_capitulation"] = (
            ((dataframe["close"] - dataframe["ema_200"]) / dataframe["ema_200"]) < -0.28
        )

        # 4. Độ rộng dải Bollinger Bands khung Ngày (1D BB Width) phục vụ lọc Sideway nén cạn kiệt
        bb_1d = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_width"] = (bb_1d["upperband"] - bb_1d["lowerband"]) / bb_1d["middleband"]
        return dataframe

    # =========================================================================
    # 3. KHUNG VĨ MÔ 4H (INFORMATIVE 4H)
    # =========================================================================
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # MACD 4H
        macd_4h = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd_4h["macdhist"]
        dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)
        
        # Nhận diện sóng Hồi Long bùng nổ trên 4H (MACD dương và đang tăng mạnh)
        dataframe["is_bull_wave"] = (dataframe["macdhist"] > 0) & (dataframe["macdhist"] > dataframe["macdhist_prev"])
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)

        # Độ rộng dải Bollinger Bands khung 4H (4H BB Width) & ADX 4H phục vụ lọc Sideway nén cạn kiệt
        bb_4h = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_width"] = (bb_4h["upperband"] - bb_4h["lowerband"]) / bb_4h["middleband"]
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        return dataframe

    # =========================================================================
    # 4. KHUNG TRUNG HẠN 1H (INFORMATIVE 1H)
    # =========================================================================
    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # EMA 50 1H
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)

        # MACD 1H
        macd_1h = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd_1h["macdhist"]
        dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)

        # RSI 14 & Đỉnh RSI cao nhất trong 24 nến 1H (24 Giờ)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_max_24"] = dataframe["rsi"].rolling(24).max()

        # Phục vụ bộ lọc Exit
        dataframe["volume_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)
        return dataframe

    # =========================================================================
    # 5. KHUNG 30M (INFORMATIVE 30M - PHỤC VỤ EXIT)
    # =========================================================================
    @informative("30m")
    def populate_indicators_30m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_ma"] = ta.SMA(dataframe["rsi"], timeperiod=14)
        macd_30m = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd_30m["macdhist"]
        return dataframe

    # =========================================================================
    # 6. KHUNG CƠ SỞ 15M (TRIGGER ENTRY & EXIT)
    # =========================================================================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 1. Các chỉ báo Entry
        dataframe["ema_10"] = ta.EMA(dataframe, timeperiod=10)
        dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        dataframe["body"] = (dataframe["close"] - dataframe["open"]).abs()
        dataframe["total_range"] = (dataframe["high"] - dataframe["low"]).clip(lower=0.0001)

        # Đáy thấp nhất của lookback_support nến trước đó (Nền tích luỹ bị gãy)
        dataframe["swing_low"] = dataframe["low"].shift(1).rolling(self.lookback_support.value).min()
        dataframe["swing_high_12"] = dataframe["high"].shift(1).rolling(12).max()

        # Biên độ dao động High - Low trong 24 giờ gần nhất (96 nến 15M) phục vụ lọc Sideway nén
        dataframe["range_24h"] = (dataframe["high"].rolling(96).max() - dataframe["low"].rolling(96).min()) / dataframe["close"]

        # 2. Các chỉ báo Exit
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_ma"] = ta.SMA(dataframe["rsi"], timeperiod=14)

        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        # Bollinger Bands & Kéo dãn biên độ
        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_lower"] = bb["lowerband"]
        dataframe["bb_mid"] = bb["middleband"]

        # Chỉ số phục vụ V-Bounce Climax
        dataframe["vol_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["vol_ratio"] = dataframe["volume"] / dataframe["vol_sma"].clip(lower=0.0001)
        dataframe["lower_wick"] = (dataframe[["open", "close"]].min(axis=1) - dataframe["low"]) / dataframe["total_range"]
        dataframe["delta_rsi"] = dataframe["rsi"] - dataframe["rsi"].shift(1)

        self.custom_df = dataframe
        return dataframe

    # =========================================================================
    # 7. QUY TRÌNH BẮT ĐIỂM VÀO LỆNH (POPULATE ENTRY TREND)
    # =========================================================================
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata.get("pair", "")
        pair_cfg = self._get_pair_params(pair)
        max_dist_val = pair_cfg.get("max_dist_ema21", self.max_dist_ema21.value)
        body_ratio_val = pair_cfg.get("candle_body_ratio_min", self.candle_body_ratio_min.value)

        # [ĐIỀU KIỆN 1 - NẾN ĐỎ XẢ ÁP ĐẢO 15M]
        is_red = dataframe["close"] < dataframe["open"]
        is_decisive_red = (
            is_red &
            ((dataframe["body"] / dataframe["total_range"]) >= body_ratio_val) &
            (dataframe["body"] >= dataframe["atr"] * 0.5)
        )

        # [ĐIỀU KIỆN 2 - GÃY ĐÁY NỀN TÍCH LUỸ (MAJOR BREAKDOWN)]
        is_breakdown_major = (
            (dataframe["close"] < dataframe["swing_low"]) &
            (dataframe["close"].shift(1) >= dataframe["swing_low"])
        )

        # [ĐIỀU KIỆN 3 - NẰM DƯỚI TRẦN ĐỘNG EMA 15M]
        is_below_emas = (dataframe["close"] < dataframe["ema_10"]) & (dataframe["close"] < dataframe["ema_21"])

        # [ĐIỀU KIỆN 4 - ĐỒNG PHA VĨ MÔ 1H]
        # a. Vị thế gần cản EMA 50 1H (chống short đáy vực)
        cond_1h_pos = dataframe["close_1h"] >= (dataframe["ema_50_1h"] * self.loc_tolerance_1h.value)
        # b. Áp lực 1H suy kiệt (Hist dốc xuống hoặc âm)
        cond_1h_macd = ((dataframe["macdhist_1h"] - dataframe["macdhist_prev_1h"]) < 0) | (dataframe["macdhist_1h"] < 0)
        # c. Phe Bò từng kiệt sức tại đỉnh trong 24h qua (RSI 1H >= 58)
        cond_1h_exhaustion = dataframe["rsi_max_24_1h"] >= self.min_1h_rsi_exhaustion.value

        cond_1h_aligned = cond_1h_pos & cond_1h_macd & cond_1h_exhaustion

        # [ĐIỀU KIỆN 5 - ĐỒNG PHA VĨ MÔ 4H]
        # Tuyệt đối CẤM Short khi 4H đang trong Sóng Hồi Long bùng nổ
        if self.block_4h_long_wave.value:
            cond_4h_aligned = dataframe["is_bull_wave_4h"].fillna(0) == 0
        else:
            cond_4h_aligned = pd.Series(True, index=dataframe.index)

        # [ĐIỀU KIỆN 5a - LỚP BẢO VỆ CHU KỲ VĨ MÔ: CHỐNG SHORT CẢN TÀU BULL RUN]
        # Tuyệt đối CẤM Short khi thị trường đang trong Siêu Chu kỳ Bull Run (Golden Cross EMA50 > EMA200 1D & Close > EMA50 1D & RSI 1D > 60)
        if "is_macro_bull_run_1d" in dataframe.columns:
            cond_macro_safe = dataframe["is_macro_bull_run_1d"].fillna(0) == 0
        else:
            cond_macro_safe = pd.Series(True, index=dataframe.index)

        # [ĐIỀU KIỆN 5b - LỚP 1: BỘ LỌC VĨ MÔ KHUNG NGÀY 1D]
        # Tuyệt đối CẤM Short khi khung Ngày (1D) đang trong Sóng Hồi Bò bùng nổ
        if self.block_1d_bull_wave.value and "is_bull_wave_1d" in dataframe.columns:
            cond_1d_aligned = dataframe["is_bull_wave_1d"].fillna(0) == 0
        else:
            cond_1d_aligned = pd.Series(True, index=dataframe.index)

        # [ĐIỀU KIỆN 5c - CHỐNG SHORT ĐÈ SÀN HỖ TRỢ VĨ MÔ 1D (FLOOR PROTECTION)]
        # Nếu giá đang nằm quá sát đáy thấp nhất 20 ngày qua (< 3.5% cách đáy) -> CẤM SHORT (tránh đè sàn hỗ trợ tổ chức)
        if "swing_low_20d_1d" in dataframe.columns:
            cond_floor_safe = ((dataframe["close"] - dataframe["swing_low_20d_1d"]) / dataframe["swing_low_20d_1d"]) >= 0.035
        else:
            cond_floor_safe = pd.Series(True, index=dataframe.index)

        # [ĐIỀU KIỆN 5d - CHỐNG SHORT ĐÈ SÀN VÙNG ĐÁY KIỆT SỨC CHU KỲ (DEEP CAPITULATION)]
        if "is_deep_capitulation_1d" in dataframe.columns:
            cond_capitulation_safe = dataframe["is_deep_capitulation_1d"].fillna(0) == 0
        else:
            cond_capitulation_safe = pd.Series(True, index=dataframe.index)

        # [ĐIỀU KIỆN 5e - BỘ LỌC CHỐNG SHORT BẪY SIDEWAY / NÉN CẠN KIỆT THANH KHOẢN (CHOP SQUEEZE DEFENSE)]
        # Cấm Short khi thị trường đang rơi vào trạng thái nén nghẽn biên độ cực hẹp:
        # Nhánh 1: BB Width 4H < 2.0% (biên độ nén nghẹt thở trên 4H)
        # Nhánh 2: ADX 4H < 15 (thị trường 4H hoàn toàn mất xu hướng, sideway giằng co)
        # Nhánh 3: BB Width 1D < 10.0% kèm biên độ 24H (15M) < 2.2% (sideway cạn kiệt thanh khoản khung ngày)
        is_chop_squeeze = pd.Series(False, index=dataframe.index)
        if "bb_width_4h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | (dataframe["bb_width_4h"] < 0.02)
        if "adx_4h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | (dataframe["adx_4h"] < 15)
        if "bb_width_1d" in dataframe.columns and "range_24h" in dataframe.columns:
            is_chop_squeeze = is_chop_squeeze | ((dataframe["bb_width_1d"] < 0.10) & (dataframe["range_24h"] < 0.022))
        cond_sideway_safe = ~is_chop_squeeze

        # [ĐIỀU KIỆN 6 - CHỐNG SHORT ĐUỔI ĐÁY]
        cond_not_chasing = (
            ((dataframe["ema_21"] - dataframe["close"]) / dataframe["close"] <= (max_dist_val / 100.0)) &
            (dataframe["rsi"] >= self.min_rsi_entry.value)
        )

        # TỔNG HỢP TÍN HIỆU ENTRY
        raw_signal = (
            is_decisive_red &
            is_breakdown_major &
            is_below_emas &
            cond_1h_aligned &
            cond_4h_aligned &
            cond_macro_safe &
            cond_1d_aligned &
            cond_floor_safe &
            cond_capitulation_safe &
            cond_sideway_safe &
            cond_not_chasing &
            (dataframe["volume"] > 0)
        )

        # ÁP DỤNG COOLDOWN 24H (96 NẾN 15M)
        cooldown = self.cooldown_candles.value
        entry_mask = pd.Series(False, index=dataframe.index)
        last_entry_idx = -9999

        raw_indices = dataframe.index[raw_signal].tolist()
        for idx in raw_indices:
            if (idx - last_entry_idx) >= cooldown:
                entry_mask.loc[idx] = True
                last_entry_idx = idx

        dataframe.loc[entry_mask, "enter_tag"] = "entry_short_mtf_sniper"
        dataframe.loc[entry_mask, "enter_short"] = 1

        return dataframe

    # =========================================================================
    # 8. QUY TRÌNH CHỐT LỜI RÂU ĐÁY (POPULATE EXIT TREND)
    # =========================================================================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # [CỬA 1 - THOÁT TIÊU CHUẨN: RSI 15M CẮT MA TỪ VÙNG QUÁ BÁN]
        cond_from_oversold = (
            dataframe["rsi"].rolling(self.exit_oversold_lookback.value).min() <= self.exit_rsi_oversold.value
        )
        cond_at_bottom = dataframe["rsi"] <= self.exit_rsi_ceiling.value
        cond_rsi_cross_ma = qtpylib.crossed_above(dataframe["rsi"], dataframe["rsi_ma"])
        
        # MACD 15M ủng hộ: Đang cong lên và Histogram tiến sát 0
        cond_macd_supporting = (
            (dataframe["macd"] >= dataframe["macd"].shift(1)) &
            (dataframe["macdhist"] > dataframe["macdhist"].shift(1))
        )

        # Bộ lọc kéo dãn biên độ Bollinger / ATR
        cond_bb_stretched = (
            (dataframe["low"].rolling(3).min() <= dataframe["bb_lower"]) |
            ((dataframe["bb_mid"] - dataframe["low"]) >= 1.5 * dataframe["atr"])
        )

        # Đồng thuận thoát 30M
        cond_30m_exit = (
            (dataframe["rsi_30m"] > dataframe["rsi_ma_30m"]) |
            (dataframe["macdhist_30m"] > dataframe["macdhist_30m"].shift(1))
        )

        exit_standard = (
            cond_from_oversold &
            cond_at_bottom &
            cond_rsi_cross_ma &
            cond_macd_supporting &
            cond_bb_stretched &
            cond_30m_exit
        )

        # [CỬA 2 - THOÁT NHANH V-BOUNCE: CLIMAX REVERSAL BẮT ĐÁY]
        if self.use_v_bounce_exit.value:
            candle_green = dataframe["close"] > dataframe["open"]
            vol_climax = (dataframe["vol_ratio"] >= self.vb_min_vol_mult.value) | (dataframe["vol_ratio"].shift(1) >= self.vb_min_vol_mult.value)
            wick_rejection = dataframe["lower_wick"] >= self.vb_min_lower_wick.value
            rsi_fast_rebound = dataframe["delta_rsi"] >= 5.0

            exit_v_bounce = (
                cond_from_oversold &
                candle_green &
                wick_rejection &
                (vol_climax | rsi_fast_rebound)
            )
        else:
            exit_v_bounce = pd.Series(False, index=dataframe.index)

        # LƯU TÍN HIỆU PHỤC VỤ CUSTOM_EXIT (CHẶN THOÁT NON DƯỚI +1.5%)
        dataframe["signal_mtf"] = exit_standard.astype(int)
        dataframe["signal_v_bounce"] = exit_v_bounce.astype(int)
        dataframe["exit_short"] = 0

        return dataframe


    # =========================================================================
    # 9. CUSTOM EXIT (TREND INVALIDATION, TIMEOUT & PROFIT EXITS)
    # =========================================================================
    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        df = getattr(self, "custom_df", None)
        if df is None:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return None

        row = df.loc[df["date"] == current_time]
        if row.empty:
            return None
        r = row.iloc[0]

        duration_minutes = (current_time - trade.open_date_utc).total_seconds() / 60

        # 1. CẮT SỚM KHI XU HƯỚNG ĐẢO CHIỀU (BREAK OF STRUCTURE) - Không đợi quét SL -2.5%!
        # Nếu sau >= 2 tiếng (8 nến 15M), giá phá vỡ cấu trúc đỉnh nến (swing high) hoặc 1H đảo chiều:
        if duration_minutes >= 120 and current_profit < -0.003:
            cond_structure_broken = r.get("close", 0) > r.get("swing_high_12", 0)
            cond_htf_reversal = (
                (r.get("close_1h", 0) > r.get("ema_50_1h", 0)) and 
                (r.get("macdhist_1h", 0) > 0) and
                (r.get("macdhist", 0) > 0)
            )
            if cond_structure_broken or cond_htf_reversal:
                return "exit_trend_invalidated"

        # 2. THỜI HẠN TỐI ĐA (24H TIMEOUT): Nếu sau 24h giá không tạo đáy mới và nằm trên EMA 21:
        if duration_minutes >= 24 * 60:
            if (r.get("close", 0) > r.get("ema_21", 0)) or (current_profit < 0.015):
                return "exit_timeout_24h"

        # 3. CHỐT LỜI THEO TÍN HIỆU ĐÁY (Chỉ cho phép thoát khi lãi >= +1.5%)
        if current_profit >= 0.015:
            if r.get("signal_v_bounce", 0) == 1:
                return "exit_v_bounce_climax"
            if r.get("signal_mtf", 0) == 1:
                return "exit_mtf_exhaustion"

        return None

    # =========================================================================
    # 9. DYNAMIC CUSTOM STOPLOSS (BẢO VỆ VỊ THẾ)
    # =========================================================================
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        Cắt lỗ kỹ thuật linh hoạt:
        1. Khi có lãi >= +3.5%: Kích hoạt Trailing Stop khóa lợi nhuận (bám sát 2.0%).
        2. Khi có lãi >= +2.0%: Kéo Stop Loss về Breakeven (+0.2% bảo toàn vốn).
        3. Sau 24h nếu giá không tạo đáy mới và chưa có lãi >= 1.5%: Thắt chặt SL về -0.5% để cắt sớm, không để om lệnh 60h dính SL -2.5%.
        4. Mặc định giữ mức Stoploss kỹ thuật (-2.5%).
        """
        # 1. Khi có lãi >= +35% ký quỹ (+3.5% chart): Kích hoạt Trailing Stop khóa lợi nhuận (bám sát 20% ký quỹ)
        if current_profit >= 0.35:
            return -0.20

        # 2. Khi có lãi >= +20% ký quỹ (+2.0% chart): Kéo Stop Loss về +10% ký quỹ (+1.0% chart = +15 USDT)
        # Giữ trọn 100% vị thế Runner, nhưng bảo đảm bỏ túi tối thiểu +15 USDT nếu giá quay đầu
        if current_profit >= 0.20:
            return -stoploss_from_open(0.10, current_profit, is_short=trade.is_short, leverage=10.0)

        # 3. Sau 24h nếu vị thế đang lỗ (profit < 0): Thắt chặt SL về -5% ký quỹ (-0.5% chart)
        duration_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if duration_hours >= 24 and current_profit < 0.0:
            sl_rel = stoploss_from_open(-0.05, current_profit, is_short=trade.is_short, leverage=10.0)
            return -max(sl_rel, 0.01)

        # 4. Mặc định: Giữ mức Stoploss kỹ thuật theo từng cặp tiền
        # BTC: -0.25 (-2.5% chart), ETH: -0.28 (-2.8% chart)
        pair_cfg = self._get_pair_params(pair)
        base_sl = pair_cfg.get("stoploss", self.stoploss)
        if base_sl > self.stoploss:
            sl_rel = stoploss_from_open(base_sl, current_profit, is_short=trade.is_short, leverage=10.0)
            return -max(sl_rel, 0.01)

        return self.stoploss
