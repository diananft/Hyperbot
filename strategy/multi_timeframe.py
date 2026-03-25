"""Multi-timeframe analysis for confluence scoring."""

import numpy as np
import pandas as pd
from typing import Dict, Optional
from utils.logger import get_logger

logger = get_logger("mtf")


class MultiTimeframeAnalyzer:
    """Analyze multiple timeframes for trade confluence."""

    def __init__(self, config: dict):
        self.timeframes = config.get("timeframes", {
            "higher": "4h", "execution": "15m", "trigger": "1m"
        })
        self.min_confluence = config.get("signal_thresholds", {}).get("confluence_min", 0.7)

    def analyze(self, candles: Dict[str, pd.DataFrame],
                indicators_calc) -> Dict:
        """Analyze all timeframes and compute confluence score.

        Args:
            candles: {timeframe: DataFrame} with OHLCV data
            indicators_calc: TechnicalIndicators instance

        Returns dict with:
            - higher_bias: float (-1 to 1) trend direction
            - execution_signal: float (-1 to 1) entry signal
            - trigger_timing: float (-1 to 1) precise entry
            - confluence: float (0-1) overall agreement
            - aligned: bool (confluence >= threshold)
            - details: dict per-timeframe analysis
        """
        results = {}
        tf_signals = {}

        for tf_name, tf_interval in self.timeframes.items():
            df = candles.get(tf_interval, pd.DataFrame())
            if len(df) < 50:
                tf_signals[tf_name] = 0.0
                results[tf_name] = {"signal": 0, "trend": "neutral", "data": "insufficient"}
                continue

            # Calculate indicators for this timeframe
            df_ind = indicators_calc.calculate_all(df)
            analysis = self._analyze_timeframe(df_ind, tf_name)
            tf_signals[tf_name] = analysis["signal"]
            results[tf_name] = analysis

        higher_bias = tf_signals.get("higher", 0)
        exec_signal = tf_signals.get("execution", 0)
        trigger = tf_signals.get("trigger", 0)

        # Confluence: how well do timeframes agree?
        signals = [higher_bias, exec_signal, trigger]
        non_zero = [s for s in signals if abs(s) > 0.1]

        if len(non_zero) == 0:
            confluence = 0.0
        elif len(non_zero) == 1:
            confluence = 0.3
        else:
            # Check if all signals agree on direction
            all_positive = all(s > 0 for s in non_zero)
            all_negative = all(s < 0 for s in non_zero)
            if all_positive or all_negative:
                # Strength is based on average magnitude
                avg_mag = np.mean([abs(s) for s in non_zero])
                confluence = min(1.0, 0.5 + avg_mag * 0.5)
            else:
                # Mixed signals - low confluence
                confluence = max(0.0, 0.3 - len(non_zero) * 0.1)

        aligned = confluence >= self.min_confluence

        return {
            "higher_bias": higher_bias,
            "execution_signal": exec_signal,
            "trigger_timing": trigger,
            "confluence": confluence,
            "aligned": aligned,
            "details": results,
        }

    def _analyze_timeframe(self, df: pd.DataFrame, tf_name: str) -> Dict:
        """Analyze a single timeframe."""
        last = df.iloc[-1]
        signal = 0.0
        trend = "neutral"

        # Trend from EMAs
        close = last.get("close", 0)
        ema_9 = last.get("ema_9", close)
        ema_21 = last.get("ema_21", close)
        ema_50 = last.get("ema_50", close)
        ema_200 = last.get("ema_200", close)

        # EMA alignment
        if close > ema_9 > ema_21 > ema_50:
            signal += 0.4
            trend = "strong_up"
        elif close > ema_21 > ema_50:
            signal += 0.25
            trend = "up"
        elif close < ema_9 < ema_21 < ema_50:
            signal -= 0.4
            trend = "strong_down"
        elif close < ema_21 < ema_50:
            signal -= 0.25
            trend = "down"

        # MACD
        macd_hist = last.get("macd_histogram", 0)
        if macd_hist and not np.isnan(macd_hist):
            signal += np.clip(macd_hist / (abs(close) * 0.001 + 1e-10), -0.2, 0.2)

        # RSI
        rsi = last.get("rsi", 50)
        if rsi and not np.isnan(rsi):
            if rsi > 70:
                signal -= 0.15  # Overbought
            elif rsi < 30:
                signal += 0.15  # Oversold
            elif rsi > 50:
                signal += 0.05
            else:
                signal -= 0.05

        # Supertrend
        st_dir = last.get("supertrend_dir", 0)
        if st_dir == 1:
            signal += 0.15
        elif st_dir == -1:
            signal -= 0.15

        signal = np.clip(signal, -1, 1)

        return {
            "signal": signal,
            "trend": trend,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "ema_alignment": trend,
        }
