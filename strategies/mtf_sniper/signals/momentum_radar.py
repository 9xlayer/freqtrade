"""Stage-1 high-recall momentum candidate radar for live/deterministic entry scanning."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas import DataFrame


class MomentumRadar:
    """
    Stage 1: High-Recall Momentum Candidate Radar.

    Runs locally (<1ms) and triggers on volume surge or 24h range breakout impulses.
    """

    @staticmethod
    def _scan_directional_cooldown(
        length: int,
        cooldown_bars: int,
        surge_short: np.ndarray,
        breakout_short: np.ndarray,
        surge_long: np.ndarray,
        breakout_long: np.ndarray,
    ) -> list[tuple[int, str]]:
        candidates: list[tuple[int, str]] = []
        last_short_idx = -100
        last_long_idx = -100
        for i in range(100, length):
            if (i - last_short_idx) >= cooldown_bars:
                if surge_short[i]:
                    candidates.append((i, "MEGA_SURGE_SHORT"))
                    last_short_idx = i
                elif breakout_short[i]:
                    candidates.append((i, "BREAKOUT_24H_SHORT"))
                    last_short_idx = i

            if (i - last_long_idx) >= cooldown_bars:
                if surge_long[i]:
                    candidates.append((i, "MEGA_SURGE_LONG"))
                    last_long_idx = i
                elif breakout_long[i]:
                    candidates.append((i, "BREAKOUT_24H_LONG"))
                    last_long_idx = i
        return candidates

    @staticmethod
    def _scan_shared_cooldown(
        length: int,
        cooldown_bars: int,
        surge_short: np.ndarray,
        breakout_short: np.ndarray,
        surge_long: np.ndarray,
        breakout_long: np.ndarray,
    ) -> list[tuple[int, str]]:
        candidates: list[tuple[int, str]] = []
        last_idx = -100
        for i in range(100, length):
            if (i - last_idx) < cooldown_bars:
                continue
            if surge_short[i]:
                candidates.append((i, "MEGA_SURGE_SHORT"))
                last_idx = i
            elif surge_long[i]:
                candidates.append((i, "MEGA_SURGE_LONG"))
                last_idx = i
            elif breakout_short[i]:
                candidates.append((i, "BREAKOUT_24H_SHORT"))
                last_idx = i
            elif breakout_long[i]:
                candidates.append((i, "BREAKOUT_24H_LONG"))
                last_idx = i
        return candidates

    @classmethod
    def find_candidates(
        cls,
        df: DataFrame,
        cooldown_bars: int = 12,
        directional_cooldown: bool = False,
    ) -> list[tuple[int, str]]:
        """
        Scan dataframe for significant momentum impulses:
        - 12 bars cooldown (3 hours) between candidate triggers.
        - Volume surge (>= 4.0x) with wide range (>= 2.2x ATR).
        - Or 24h range breakout (96 bars) with volume >= 2.5x.
        - Zero-division protection, NaN/Inf checks.
        """
        if df is None or len(df) < 100:
            return []

        vol_sma = df["volume"].rolling(20, min_periods=1).mean().replace(0, np.nan)
        vol_ratio = (df["volume"] / vol_sma).fillna(0.0)

        atr_safe = (
            df["atr"].replace(0, np.nan) if "atr" in df else pd.Series(np.nan, index=df.index)
        )
        range_raw = df["high"] - df["low"]
        range_ratio = (range_raw / atr_safe).fillna(0.0)

        surge_short = (
            ((df["close"] < df["open"]) & (vol_ratio >= 4.0) & (range_ratio >= 2.2))
            .fillna(False)
            .to_numpy(dtype=bool, copy=False)
        )
        surge_long = (
            ((df["close"] > df["open"]) & (vol_ratio >= 4.0) & (range_ratio >= 2.2))
            .fillna(False)
            .to_numpy(dtype=bool, copy=False)
        )

        l96 = df["low"].rolling(96).min().shift(1)
        h96 = df["high"].rolling(96).max().shift(1)
        breakout_short = (
            ((df["close"] < l96) & (vol_ratio >= 2.5))
            .fillna(False)
            .to_numpy(dtype=bool, copy=False)
        )
        breakout_long = (
            ((df["close"] > h96) & (vol_ratio >= 2.5))
            .fillna(False)
            .to_numpy(dtype=bool, copy=False)
        )

        if directional_cooldown:
            return cls._scan_directional_cooldown(
                len(df), cooldown_bars, surge_short, breakout_short, surge_long, breakout_long
            )
        return cls._scan_shared_cooldown(
            len(df), cooldown_bars, surge_short, breakout_short, surge_long, breakout_long
        )
