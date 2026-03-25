"""Market regime detection and classification."""

import numpy as np
import pandas as pd
from typing import Dict
from utils.logger import get_logger

logger = get_logger("regime")


class RegimeDetector:
    """Classify market regime per candle for strategy adaptation."""

    # Regime types
    STRONG_TREND_UP = "strong_trend_up"
    STRONG_TREND_DOWN = "strong_trend_down"
    RANGE = "range"
    BREAKOUT = "breakout"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"

    def __init__(self, config: dict):
        regime_cfg = config.get("regime", {})
        self.adx_trend_threshold = regime_cfg.get("adx_trend", 25)
        self.adx_range_threshold = regime_cfg.get("adx_range", 20)
        self.atr_high_vol_mult = regime_cfg.get("atr_high_vol", 2.0)
        self.atr_low_vol_mult = regime_cfg.get("atr_low_vol", 0.5)

    def detect(self, df: pd.DataFrame) -> Dict:
        """Detect current market regime from indicator DataFrame.

        Expects df to have: adx, ema_50, ema_200, atr, squeeze, squeeze_release,
                           close, volume, volume_ma_20

        Returns dict with:
            - regime: str (regime type)
            - confidence: float (0-1)
            - strategy_hint: str
            - stop_multiplier: float
            - size_multiplier: float
        """
        if len(df) < 50 or "adx" not in df.columns:
            return self._default_result()

        last = df.iloc[-1]
        close = last["close"]
        adx = last.get("adx", 0)
        ema_50 = last.get("ema_50", close)
        ema_200 = last.get("ema_200", close)
        atr = last.get("atr", 0)
        squeeze = last.get("squeeze", False)
        squeeze_release = last.get("squeeze_release", False)
        volume = last.get("volume", 0)
        vol_ma = last.get("volume_ma_20", volume)

        # Calculate average ATR over last 50 candles
        atr_series = df["atr"].dropna()
        avg_atr = atr_series.mean() if len(atr_series) > 0 else atr

        # Determine regime
        regime = self.RANGE
        confidence = 0.5
        strategy = "wait"
        stop_mult = 1.0
        size_mult = 1.0

        # Check volatility first (it modifies other regimes)
        is_high_vol = atr > avg_atr * self.atr_high_vol_mult if avg_atr > 0 else False
        is_low_vol = atr < avg_atr * self.atr_low_vol_mult if avg_atr > 0 else False

        # Breakout detection (squeeze release + volume spike)
        vol_spike = volume > vol_ma * 1.5 if vol_ma > 0 else False
        if squeeze_release and vol_spike:
            regime = self.BREAKOUT
            confidence = 0.8
            strategy = "enter_breakout_direction"
            stop_mult = 1.0
            size_mult = 0.8

        # Strong Trend Up
        elif adx and adx > self.adx_trend_threshold and close > ema_50 and ema_50 > ema_200:
            regime = self.STRONG_TREND_UP
            confidence = min(0.9, 0.5 + (adx - self.adx_trend_threshold) / 50)
            strategy = "trend_follow_long"
            stop_mult = 1.0
            size_mult = 1.0

        # Strong Trend Down
        elif adx and adx > self.adx_trend_threshold and close < ema_50 and ema_50 < ema_200:
            regime = self.STRONG_TREND_DOWN
            confidence = min(0.9, 0.5 + (adx - self.adx_trend_threshold) / 50)
            strategy = "trend_follow_short"
            stop_mult = 1.0
            size_mult = 1.0

        # Range
        elif adx and adx < self.adx_range_threshold:
            regime = self.RANGE
            confidence = min(0.8, 0.5 + (self.adx_range_threshold - adx) / 40)
            strategy = "mean_revert"
            stop_mult = 0.8
            size_mult = 0.7

        # Apply volatility overlay
        if is_high_vol and regime not in (self.BREAKOUT,):
            regime = self.HIGH_VOLATILITY
            confidence = 0.7
            strategy = "reduce_size_widen_stops"
            stop_mult = 1.3
            size_mult = 0.5

        elif is_low_vol and not squeeze_release:
            regime = self.LOW_VOLATILITY
            confidence = 0.6
            strategy = "prepare_for_breakout"
            stop_mult = 0.7
            size_mult = 0.6

        return {
            "regime": regime,
            "confidence": confidence,
            "strategy_hint": strategy,
            "stop_multiplier": stop_mult,
            "size_multiplier": size_mult,
            "adx": adx,
            "atr": atr,
            "avg_atr": avg_atr,
            "is_high_vol": is_high_vol,
            "is_low_vol": is_low_vol,
            "squeeze": bool(squeeze),
            "squeeze_release": bool(squeeze_release),
        }

    def _default_result(self):
        return {
            "regime": self.RANGE,
            "confidence": 0.3,
            "strategy_hint": "insufficient_data",
            "stop_multiplier": 1.0,
            "size_multiplier": 0.5,
            "adx": 0,
            "atr": 0,
            "avg_atr": 0,
            "is_high_vol": False,
            "is_low_vol": False,
            "squeeze": False,
            "squeeze_release": False,
        }
