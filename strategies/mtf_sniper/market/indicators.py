"""
MTF indicator population — extracted from strategies/active/StrategyMTFSniper.

These functions mutate and return dataframes for each timeframe. Informative
decorators remain on the Strategy class; they delegate here for parity.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame


def populate_indicators_1d(dataframe: DataFrame) -> DataFrame:
    dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
    dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
    dataframe["ema_100"] = ta.EMA(dataframe, timeperiod=100)
    dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
    dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
    dataframe["rsi_prev"] = dataframe["rsi"].shift(1)
    macd_1d: Any = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
    dataframe["macdhist"] = macd_1d["macdhist"]
    dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)
    dataframe["swing_low_20d"] = dataframe["low"].rolling(20).min()
    dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
    dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
    dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)

    dataframe["is_bull_wave"] = (dataframe["macdhist"] > 0) & (
        dataframe["macdhist"] > dataframe["macdhist_prev"]
    )
    dataframe["is_bear_wave"] = (dataframe["macdhist"] < 0) & (
        dataframe["macdhist"] < dataframe["macdhist_prev"]
    )

    is_golden_bull = (
        (dataframe["close"] > dataframe["ema_50"])
        & (dataframe["ema_50"] > dataframe["ema_200"])
        & (dataframe["rsi"] > 60)
    )
    is_bull_stack = (
        (dataframe["close"] > dataframe["ema_21"])
        & (dataframe["ema_21"] > dataframe["ema_50"])
        & (dataframe["ema_50"] > dataframe["ema_100"])
        & (dataframe["ema_100"] > dataframe["ema_200"])
        & (dataframe["adx"] > 20)
    )
    is_parabolic_surge = (
        (dataframe["adx"] > 50)
        & (dataframe["plus_di"] > dataframe["minus_di"])
        & (dataframe["close"] > dataframe["ema_200"])
    )
    dataframe["is_macro_bull_run"] = is_golden_bull | is_bull_stack | is_parabolic_surge

    is_death_cross_bear = (dataframe["close"] < dataframe["ema_200"]) | (
        (dataframe["close"] < dataframe["ema_50"]) & (dataframe["ema_50"] < dataframe["ema_200"])
    )
    dataframe["is_macro_bear_market"] = is_death_cross_bear
    dataframe["is_deep_capitulation"] = (
        (dataframe["close"] - dataframe["ema_200"]) / dataframe["ema_200"]
    ) < -0.28
    dataframe["dist_from_ema_200"] = (dataframe["close"] - dataframe["ema_200"]) / dataframe[
        "ema_200"
    ]
    dataframe["is_overextended_top"] = dataframe["dist_from_ema_200"] > 0.35

    bb_1d: Any = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
    dataframe["bb_width"] = (bb_1d["upperband"] - bb_1d["lowerband"]) / bb_1d["middleband"]
    dataframe["pdh"] = dataframe["high"].shift(1)
    dataframe["pdl"] = dataframe["low"].shift(1)
    return dataframe


def populate_indicators_4h(dataframe: DataFrame) -> DataFrame:
    macd_4h: Any = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
    dataframe["macdhist"] = macd_4h["macdhist"]
    dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)
    dataframe["is_bull_wave"] = (dataframe["macdhist"] > 0) & (
        dataframe["macdhist"] > dataframe["macdhist_prev"]
    )
    dataframe["is_bear_wave"] = (dataframe["macdhist"] < 0) & (
        dataframe["macdhist"] < dataframe["macdhist_prev"]
    )
    dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
    bb_4h: Any = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
    dataframe["bb_width"] = (bb_4h["upperband"] - bb_4h["lowerband"]) / bb_4h["middleband"]
    dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
    dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
    dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)
    return dataframe


def populate_indicators_1h(dataframe: DataFrame) -> DataFrame:
    dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
    dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
    macd_1h: Any = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
    dataframe["macdhist"] = macd_1h["macdhist"]
    dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)
    dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
    dataframe["rsi_max_24"] = dataframe["rsi"].rolling(24).max()
    dataframe["rsi_min_24"] = dataframe["rsi"].rolling(24).min()
    return dataframe


def populate_indicators_15m(
    dataframe: DataFrame,
    *,
    short_lookback_support: int = 17,
    long_lookback_resistance: int = 22,
) -> DataFrame:
    dataframe["ema_10"] = ta.EMA(dataframe, timeperiod=10)
    dataframe["ema_21"] = ta.EMA(dataframe, timeperiod=21)
    dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
    dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
    dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

    macd: Any = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
    dataframe["macdhist"] = macd["macdhist"]

    dataframe["body"] = (dataframe["close"] - dataframe["open"]).abs()
    dataframe["total_range"] = (dataframe["high"] - dataframe["low"]).clip(lower=0.0001)
    dataframe["atr_pct"] = dataframe["atr"].rolling(100).rank(pct=True) * 100

    dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
    dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
    dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)

    dataframe["vol_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)
    dataframe["rel_vol"] = dataframe["volume"] / dataframe["vol_sma"].replace(0, np.nan).fillna(1.0)

    dataframe["upper_wick"] = dataframe["high"] - dataframe[["open", "close"]].max(axis=1)
    dataframe["lower_wick"] = dataframe[["open", "close"]].min(axis=1) - dataframe["low"]
    dataframe["upper_wick_ratio"] = dataframe["upper_wick"] / dataframe["total_range"]
    dataframe["lower_wick_ratio"] = dataframe["lower_wick"] / dataframe["total_range"]
    dataframe["close_pos_ratio"] = (dataframe["close"] - dataframe["low"]) / dataframe[
        "total_range"
    ]

    l_trend = (
        (dataframe["ema_10"] > dataframe["ema_21"]).astype(int) * 7
        + (dataframe["ema_21"] > dataframe["ema_200"]).astype(int) * 7
        + (dataframe["close"] > dataframe["ema_10"]).astype(int) * 6
    )
    s_trend = (
        (dataframe["ema_10"] < dataframe["ema_21"]).astype(int) * 7
        + (dataframe["ema_21"] < dataframe["ema_200"]).astype(int) * 7
        + (dataframe["close"] < dataframe["ema_10"]).astype(int) * 6
    )
    l_mom = (
        ((dataframe["rsi"] > 50) & (dataframe["rsi"] < 75)).astype(int) * 10
        + (dataframe["rsi"] >= 75).astype(int) * 4
        + (dataframe["macdhist"] > 0).astype(int) * 5
        + (dataframe["macdhist"] > dataframe["macdhist"].shift(1)).astype(int) * 5
    )
    s_mom = (
        ((dataframe["rsi"] < 50) & (dataframe["rsi"] > 25)).astype(int) * 10
        + (dataframe["rsi"] <= 25).astype(int) * 4
        + (dataframe["macdhist"] < 0).astype(int) * 5
        + (dataframe["macdhist"] < dataframe["macdhist"].shift(1)).astype(int) * 5
    )
    l_str = (dataframe["plus_di"] > dataframe["minus_di"]).astype(int) * 10 + (
        (dataframe["plus_di"] > dataframe["minus_di"]) & (dataframe["adx"] >= 20)
    ).astype(int) * 10
    s_str = (dataframe["minus_di"] > dataframe["plus_di"]).astype(int) * 10 + (
        (dataframe["minus_di"] > dataframe["plus_di"]) & (dataframe["adx"] >= 20)
    ).astype(int) * 10
    is_green = dataframe["close"] > dataframe["open"]
    is_red = dataframe["close"] < dataframe["open"]
    l_vol = (
        (is_green & (dataframe["rel_vol"] >= 1.5)).astype(int) * 20
        + (is_green & (dataframe["rel_vol"] >= 1.0) & (dataframe["rel_vol"] < 1.5)).astype(int) * 12
        + (is_green & (dataframe["rel_vol"] < 1.0)).astype(int) * 6
    )
    s_vol = (
        (is_red & (dataframe["rel_vol"] >= 1.5)).astype(int) * 20
        + (is_red & (dataframe["rel_vol"] >= 1.0) & (dataframe["rel_vol"] < 1.5)).astype(int) * 12
        + (is_red & (dataframe["rel_vol"] < 1.0)).astype(int) * 6
    )
    l_htf = (dataframe.get("macdhist_4h", pd.Series(0, index=dataframe.index)) > 0).astype(
        int
    ) * 10 + (dataframe["close"] > dataframe.get("ema_50_1d", dataframe["close"])).astype(int) * 10
    s_htf = (dataframe.get("is_bear_wave_4h", pd.Series(0, index=dataframe.index)) == 1).astype(
        int
    ) * 10 + (dataframe["close"] < dataframe.get("ema_50_1d", dataframe["close"])).astype(int) * 10
    dataframe["long_score"] = l_trend + l_mom + l_str + l_vol + l_htf
    dataframe["short_score"] = s_trend + s_mom + s_str + s_vol + s_htf

    dataframe["swing_low"] = dataframe["low"].shift(1).rolling(short_lookback_support).min()
    dataframe["swing_high"] = dataframe["high"].shift(1).rolling(long_lookback_resistance).max()
    dataframe["swing_high_12"] = dataframe["high"].shift(1).rolling(12).max()
    dataframe["swing_low_12"] = dataframe["low"].shift(1).rolling(12).min()
    dataframe["range_24h"] = (
        dataframe["high"].rolling(96).max() - dataframe["low"].rolling(96).min()
    ) / dataframe["close"]
    return dataframe
