"""Signal engine - combines all analysis into trading signals."""

import numpy as np
import pandas as pd
from typing import Dict, Optional
from enum import Enum

from strategy.indicators import TechnicalIndicators
from strategy.orderbook_analysis import OrderbookAnalyzer
from strategy.pattern_recognition import PatternRecognizer
from strategy.volume_profile import VolumeProfileAnalyzer
from strategy.regime_detector import RegimeDetector
from strategy.multi_timeframe import MultiTimeframeAnalyzer
from utils.logger import get_logger

logger = get_logger("signals")


class SignalType(Enum):
    STRONG_LONG = "strong_long"
    LONG = "long"
    NEUTRAL = "neutral"
    SHORT = "short"
    STRONG_SHORT = "strong_short"


class Signal:
    """Represents a trading signal."""
    def __init__(self, asset: str, signal_type: SignalType, score: float,
                 components: Dict, regime: str, confidence: float):
        self.asset = asset
        self.signal_type = signal_type
        self.score = score
        self.components = components
        self.regime = regime
        self.confidence = confidence

    def __repr__(self):
        return f"Signal({self.asset}, {self.signal_type.value}, score={self.score:.3f})"


class SignalEngine:
    """Combines all analysis modules into weighted trading signals."""

    def __init__(self, config: dict):
        self.config = config
        weights = config.get("signal_weights", {})
        self.weights = {
            "trend": weights.get("trend", 0.25),
            "momentum": weights.get("momentum", 0.15),
            "volume": weights.get("volume", 0.15),
            "orderbook": weights.get("orderbook", 0.10),
            "patterns": weights.get("patterns", 0.10),
            "mtf_confluence": weights.get("mtf_confluence", 0.15),
            "regime": weights.get("regime", 0.10),
        }

        thresholds = config.get("signal_thresholds", {})
        self.strong_threshold = thresholds.get("strong_entry", 0.8)
        self.normal_threshold = thresholds.get("normal_entry", 0.5)
        self.vol_ma_period = thresholds.get("volume_ma_period", 20)

        # Initialize sub-modules
        self.indicators = TechnicalIndicators(config)
        self.orderbook_analyzer = OrderbookAnalyzer(config)
        self.pattern_recognizer = PatternRecognizer(config)
        self.volume_profiler = VolumeProfileAnalyzer(config)
        self.regime_detector = RegimeDetector(config)
        self.mtf_analyzer = MultiTimeframeAnalyzer(config)

    def generate_signal(self, asset: str, candles: Dict[str, pd.DataFrame],
                        orderbook: dict = None,
                        funding_rate: float = None) -> Signal:
        """Generate a trading signal for an asset.

        Args:
            asset: Asset symbol
            candles: {timeframe: DataFrame} with OHLCV data
            orderbook: L2 orderbook data
            funding_rate: Current funding rate

        Returns: Signal object
        """
        # Get execution timeframe data
        exec_tf = self.config.get("timeframes", {}).get("execution", "15m")
        df = candles.get(exec_tf, pd.DataFrame())

        if len(df) < 50:
            return Signal(asset, SignalType.NEUTRAL, 0, {}, "unknown", 0)

        # Calculate indicators on execution timeframe
        df_ind = self.indicators.calculate_all(df)
        last = df_ind.iloc[-1]

        # ===== Component Scores =====
        components = {}

        # 1. Trend Score
        components["trend"] = self._trend_score(last)

        # 2. Momentum Score
        components["momentum"] = self._momentum_score(last)

        # 3. Volume Score
        components["volume"] = self._volume_score(last, df_ind)

        # 4. Orderbook Score
        if orderbook and orderbook.get("bids"):
            ob_analysis = self.orderbook_analyzer.analyze(orderbook)
            components["orderbook"] = ob_analysis["signal"]
        else:
            components["orderbook"] = 0

        # 5. Pattern Score
        pattern_analysis = self.pattern_recognizer.analyze(df_ind)
        components["patterns"] = pattern_analysis["signal"]

        # 6. MTF Confluence
        mtf_analysis = self.mtf_analyzer.analyze(candles, self.indicators)
        components["mtf_confluence"] = mtf_analysis["confluence"] * np.sign(
            mtf_analysis["higher_bias"] + mtf_analysis["execution_signal"]
        ) if mtf_analysis["aligned"] else 0

        # 7. Regime
        regime_analysis = self.regime_detector.detect(df_ind)
        regime = regime_analysis["regime"]
        components["regime"] = self._regime_score(regime_analysis)

        # ===== Weighted Composite Score =====
        composite = sum(components[k] * self.weights[k] for k in self.weights)
        composite = np.clip(composite, -1, 1)

        # ===== Filters =====
        # Volume filter
        vol = last.get("volume", 0)
        vol_ma = last.get("volume_ma_20", vol)
        if vol_ma > 0 and vol < vol_ma * 0.5:
            composite *= 0.5  # Low volume = reduce confidence

        # Funding rate filter
        if funding_rate is not None:
            if composite > 0 and funding_rate > 0.0003:
                composite *= 0.7  # Long while funding is high = reduce
            elif composite < 0 and funding_rate < -0.0003:
                composite *= 0.7  # Short while funding is very negative = reduce

        # Conflicting signals filter
        pos_components = sum(1 for v in components.values() if v > 0.1)
        neg_components = sum(1 for v in components.values() if v < -0.1)
        if pos_components >= 3 and neg_components >= 3:
            composite *= 0.3  # Heavy conflict = stay flat

        # Determine signal type
        if composite >= self.strong_threshold:
            signal_type = SignalType.STRONG_LONG
        elif composite >= self.normal_threshold:
            signal_type = SignalType.LONG
        elif composite <= -self.strong_threshold:
            signal_type = SignalType.STRONG_SHORT
        elif composite <= -self.normal_threshold:
            signal_type = SignalType.SHORT
        else:
            signal_type = SignalType.NEUTRAL

        confidence = min(1.0, abs(composite))

        return Signal(asset, signal_type, composite, components, regime, confidence)

    def _trend_score(self, last: pd.Series) -> float:
        """Calculate trend component score."""
        score = 0.0
        close = last.get("close", 0)
        if close == 0:
            return 0

        # EMA alignment
        ema_9 = last.get("ema_9", close)
        ema_21 = last.get("ema_21", close)
        ema_50 = last.get("ema_50", close)
        ema_200 = last.get("ema_200", close)

        if close > ema_9 > ema_21 > ema_50 > ema_200:
            score += 0.5  # Perfect bullish alignment
        elif close > ema_21 > ema_50:
            score += 0.3
        elif close < ema_9 < ema_21 < ema_50 < ema_200:
            score -= 0.5
        elif close < ema_21 < ema_50:
            score -= 0.3

        # MACD
        macd_hist = last.get("macd_histogram", 0)
        if macd_hist and not np.isnan(macd_hist):
            if macd_hist > 0:
                score += 0.15
            else:
                score -= 0.15

        # ADX strength
        adx = last.get("adx", 0)
        di_plus = last.get("di_plus", 0)
        di_minus = last.get("di_minus", 0)
        if adx and adx > 25:
            if di_plus and di_minus:
                if di_plus > di_minus:
                    score += 0.2
                else:
                    score -= 0.2

        # Supertrend
        st_dir = last.get("supertrend_dir", 0)
        if st_dir == 1:
            score += 0.1
        elif st_dir == -1:
            score -= 0.1

        # Linear regression
        slope = last.get("linreg_slope", 0)
        r2 = last.get("linreg_r2", 0)
        if slope and r2 and not np.isnan(slope) and not np.isnan(r2) and r2 > 0.5:
            score += np.clip(slope / (close * 0.01 + 1e-10), -0.15, 0.15)

        return np.clip(score, -1, 1)

    def _momentum_score(self, last: pd.Series) -> float:
        """Calculate momentum component score."""
        score = 0.0

        # RSI
        rsi = last.get("rsi", 50)
        if rsi and not np.isnan(rsi):
            if rsi > 70:
                score -= 0.3  # Overbought
            elif rsi > 60:
                score += 0.1  # Bullish momentum
            elif rsi < 30:
                score += 0.3  # Oversold (potential reversal)
            elif rsi < 40:
                score -= 0.1

        # RSI divergence
        if last.get("rsi_bullish_div", False):
            score += 0.25
        if last.get("rsi_bearish_div", False):
            score -= 0.25

        # StochRSI
        stoch_k = last.get("stochrsi_k", 50)
        stoch_d = last.get("stochrsi_d", 50)
        if stoch_k and stoch_d and not np.isnan(stoch_k):
            if stoch_k < 20 and stoch_k > stoch_d:
                score += 0.2  # Oversold crossover
            elif stoch_k > 80 and stoch_k < stoch_d:
                score -= 0.2  # Overbought crossover

        # CCI
        cci = last.get("cci", 0)
        if cci and not np.isnan(cci):
            score += np.clip(cci / 200, -0.15, 0.15)

        # Williams %R
        wr = last.get("williams_r", -50)
        if wr and not np.isnan(wr):
            if wr > -20:
                score -= 0.1  # Overbought
            elif wr < -80:
                score += 0.1  # Oversold

        # MFI
        mfi = last.get("mfi", 50)
        if mfi and not np.isnan(mfi):
            if mfi > 80:
                score -= 0.1
            elif mfi < 20:
                score += 0.1

        return np.clip(score, -1, 1)

    def _volume_score(self, last: pd.Series, df: pd.DataFrame) -> float:
        """Calculate volume component score."""
        score = 0.0

        # Volume ratio
        vol_ratio = last.get("volume_ratio", 1)
        if vol_ratio and not np.isnan(vol_ratio):
            if vol_ratio > 2:
                score += 0.2  # High volume
            elif vol_ratio < 0.5:
                score -= 0.1  # Low volume

        # OBV trend
        obv = last.get("obv", 0)
        obv_ma = last.get("obv_ma", 0)
        if obv and obv_ma and not np.isnan(obv) and not np.isnan(obv_ma):
            if obv > obv_ma:
                score += 0.2
            else:
                score -= 0.2

        # CMF
        cmf = last.get("cmf", 0)
        if cmf and not np.isnan(cmf):
            score += np.clip(cmf * 2, -0.3, 0.3)

        # VWAP position
        close = last.get("close", 0)
        vwap = last.get("vwap", close)
        if close and vwap and not np.isnan(vwap) and vwap > 0:
            vwap_dist = (close - vwap) / vwap
            score += np.clip(vwap_dist * 5, -0.2, 0.2)

        # Volume profile (from CVD trend)
        if "cvd" in df.columns and len(df) >= 10:
            cvd_recent = df["cvd"].iloc[-10:]
            if not cvd_recent.isna().all():
                cvd_slope = (cvd_recent.iloc[-1] - cvd_recent.iloc[0])
                if cvd_slope > 0:
                    score += 0.1
                else:
                    score -= 0.1

        return np.clip(score, -1, 1)

    def _regime_score(self, regime_analysis: Dict) -> float:
        """Convert regime to directional score."""
        regime = regime_analysis["regime"]
        confidence = regime_analysis["confidence"]

        regime_scores = {
            RegimeDetector.STRONG_TREND_UP: 0.5,
            RegimeDetector.STRONG_TREND_DOWN: -0.5,
            RegimeDetector.RANGE: 0.0,
            RegimeDetector.BREAKOUT: 0.0,  # Direction comes from other signals
            RegimeDetector.HIGH_VOLATILITY: 0.0,
            RegimeDetector.LOW_VOLATILITY: 0.0,
        }
        return regime_scores.get(regime, 0) * confidence

    def should_enter(self, signal: Signal) -> bool:
        """Check if signal warrants a new position."""
        return signal.signal_type in (SignalType.STRONG_LONG, SignalType.LONG,
                                       SignalType.STRONG_SHORT, SignalType.SHORT)

    def get_position_size_pct(self, signal: Signal) -> float:
        """Get position size percentage based on signal strength."""
        if abs(signal.score) >= self.strong_threshold:
            return 1.0  # Full size
        elif abs(signal.score) >= self.normal_threshold:
            return 0.6  # 60% size
        return 0.0

    def should_exit(self, signal: Signal, current_side: str) -> bool:
        """Check if signal warrants closing a position."""
        if current_side == "long":
            return signal.signal_type in (SignalType.SHORT, SignalType.STRONG_SHORT)
        elif current_side == "short":
            return signal.signal_type in (SignalType.LONG, SignalType.STRONG_LONG)
        return False
