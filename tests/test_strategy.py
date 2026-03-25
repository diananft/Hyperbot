"""Tests for strategy modules."""

import pytest
import numpy as np
import pandas as pd

from strategy.indicators import TechnicalIndicators
from strategy.orderbook_analysis import OrderbookAnalyzer
from strategy.pattern_recognition import PatternRecognizer
from strategy.volume_profile import VolumeProfileAnalyzer
from strategy.regime_detector import RegimeDetector
from strategy.signal_engine import SignalEngine, SignalType


def make_ohlcv(n=200, start_price=100, trend=0.001, volatility=0.02):
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    prices = [start_price]
    for i in range(1, n):
        ret = trend + np.random.normal(0, volatility)
        prices.append(prices[-1] * (1 + ret))

    data = []
    for i, p in enumerate(prices):
        h = p * (1 + abs(np.random.normal(0, 0.005)))
        l = p * (1 - abs(np.random.normal(0, 0.005)))
        o = p * (1 + np.random.normal(0, 0.003))
        v = abs(np.random.normal(1000, 300))
        data.append({
            "timestamp": 1700000000000 + i * 900000,
            "open": o, "high": max(h, o, p), "low": min(l, o, p),
            "close": p, "volume": v
        })
    return pd.DataFrame(data)


class TestIndicators:
    def setup_method(self):
        self.config = {"indicators": {}}
        self.ind = TechnicalIndicators(self.config)
        self.df = make_ohlcv(200)

    def test_calculate_all_columns(self):
        result = self.ind.calculate_all(self.df)
        # Check key columns exist
        expected_cols = [
            "ema_9", "ema_21", "ema_50", "sma_20", "macd", "macd_signal",
            "adx", "rsi", "bb_upper", "bb_lower", "atr", "obv", "vwap",
            "supertrend", "stochrsi_k", "cci", "williams_r", "mfi",
            "squeeze", "cvd", "cmf",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_rsi_range(self):
        result = self.ind.calculate_all(self.df)
        rsi = result["rsi"].dropna()
        assert rsi.min() >= 0
        assert rsi.max() <= 100

    def test_atr_positive(self):
        result = self.ind.calculate_all(self.df)
        atr = result["atr"].dropna()
        assert (atr >= 0).all()

    def test_insufficient_data(self):
        small_df = self.df.head(20)
        result = self.ind.calculate_all(small_df)
        assert len(result) == len(small_df)


class TestOrderbookAnalysis:
    def setup_method(self):
        self.analyzer = OrderbookAnalyzer({"orderbook": {"wall_threshold": 3.0}})

    def test_balanced_orderbook(self):
        ob = {
            "bids": [{"price": 99 - i * 0.1, "size": 10} for i in range(10)],
            "asks": [{"price": 100 + i * 0.1, "size": 10} for i in range(10)],
        }
        result = self.analyzer.analyze(ob)
        assert abs(result["imbalance"]) < 0.1
        assert result["spread"] == pytest.approx(1.0, abs=0.2)

    def test_bid_heavy_orderbook(self):
        ob = {
            "bids": [{"price": 99 - i * 0.1, "size": 50} for i in range(10)],
            "asks": [{"price": 100 + i * 0.1, "size": 5} for i in range(10)],
        }
        result = self.analyzer.analyze(ob)
        assert result["imbalance"] > 0.5
        assert result["signal"] > 0

    def test_empty_orderbook(self):
        result = self.analyzer.analyze({"bids": [], "asks": []})
        assert result["signal"] == 0

    def test_wall_detection(self):
        bids = [{"price": 99 - i * 0.1, "size": 10} for i in range(10)]
        bids[2]["size"] = 100  # Large wall
        ob = {
            "bids": bids,
            "asks": [{"price": 100 + i * 0.1, "size": 10} for i in range(10)],
        }
        result = self.analyzer.analyze(ob)
        assert len(result["bid_walls"]) > 0


class TestPatternRecognition:
    def setup_method(self):
        self.pr = PatternRecognizer()

    def test_analyze_returns_structure(self):
        df = make_ohlcv(200)
        result = self.pr.analyze(df)
        assert "patterns" in result
        assert "support_levels" in result
        assert "resistance_levels" in result
        assert "structure" in result
        assert "signal" in result

    def test_insufficient_data(self):
        df = make_ohlcv(20)
        result = self.pr.analyze(df)
        assert result["structure"] == "unknown"


class TestVolumeProfile:
    def setup_method(self):
        self.vp = VolumeProfileAnalyzer()

    def test_analyze_basic(self):
        df = make_ohlcv(100)
        result = self.vp.analyze(df)
        assert result["poc"] > 0
        assert result["vah"] >= result["val"]
        assert result["current_position"] in ("above_value", "below_value", "inside_value")

    def test_insufficient_data(self):
        df = make_ohlcv(5)
        result = self.vp.analyze(df)
        assert result["poc"] == 0


class TestRegimeDetector:
    def setup_method(self):
        self.config = {"regime": {"adx_trend": 25, "adx_range": 20,
                                   "atr_high_vol": 2.0, "atr_low_vol": 0.5}}
        self.rd = RegimeDetector(self.config)
        self.ind = TechnicalIndicators({"indicators": {}})

    def test_detect_returns_valid_regime(self):
        df = make_ohlcv(200, trend=0.003)
        df = self.ind.calculate_all(df)
        result = self.rd.detect(df)
        assert result["regime"] in (
            "strong_trend_up", "strong_trend_down", "range",
            "breakout", "high_volatility", "low_volatility"
        )
        assert 0 <= result["confidence"] <= 1
        assert result["stop_multiplier"] > 0

    def test_insufficient_data(self):
        df = pd.DataFrame({"close": [100]})
        result = self.rd.detect(df)
        assert result["regime"] == "range"
        assert result["confidence"] == 0.3


class TestSignalEngine:
    def setup_method(self):
        self.config = {
            "signal_weights": {
                "trend": 0.25, "momentum": 0.15, "volume": 0.15,
                "orderbook": 0.10, "patterns": 0.10,
                "mtf_confluence": 0.15, "regime": 0.10,
            },
            "signal_thresholds": {
                "strong_entry": 0.8, "normal_entry": 0.5,
                "confluence_min": 0.7, "volume_ma_period": 20,
            },
            "indicators": {},
            "orderbook": {},
            "regime": {"adx_trend": 25, "adx_range": 20,
                       "atr_high_vol": 2.0, "atr_low_vol": 0.5},
            "timeframes": {"higher": "4h", "execution": "15m", "trigger": "1m"},
        }
        self.se = SignalEngine(self.config)

    def test_neutral_signal(self):
        df = make_ohlcv(200, trend=0)
        candles = {"15m": df}
        signal = self.se.generate_signal("BTC", candles)
        assert signal.signal_type in SignalType
        assert -1 <= signal.score <= 1

    def test_signal_components(self):
        df = make_ohlcv(200, trend=0.002)
        candles = {"15m": df}
        signal = self.se.generate_signal("BTC", candles)
        assert "trend" in signal.components
        assert "momentum" in signal.components
        assert "volume" in signal.components

    def test_should_enter(self):
        df = make_ohlcv(200, trend=0.005)  # Strong uptrend
        candles = {"15m": df}
        signal = self.se.generate_signal("BTC", candles)
        # Signal may or may not trigger entry depending on data
        assert isinstance(self.se.should_enter(signal), bool)
