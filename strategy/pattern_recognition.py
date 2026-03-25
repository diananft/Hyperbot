"""Chart pattern recognition using swing point analysis."""

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from typing import List, Dict, Tuple, Optional
from utils.logger import get_logger

logger = get_logger("patterns")


class PatternRecognizer:
    """Detect chart patterns and support/resistance levels."""

    def __init__(self, config: dict = None):
        self.swing_order = 5  # Number of bars on each side for swing detection
        self.sr_cluster_pct = 0.005  # 0.5% for clustering S/R levels
        self.min_pattern_bars = 10
        self.max_pattern_bars = 100

    def analyze(self, df: pd.DataFrame) -> Dict:
        """Run all pattern recognition on a DataFrame.

        Returns:
            dict with keys: patterns, support_levels, resistance_levels,
                           structure, breakouts
        """
        if len(df) < 50:
            return {"patterns": [], "support_levels": [], "resistance_levels": [],
                    "structure": "unknown", "breakouts": [], "signal": 0}

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values

        # Find swing highs and lows
        swing_highs, swing_lows = self._find_swings(high, low)

        # Detect patterns
        patterns = []
        patterns.extend(self._detect_double_top(high, swing_highs))
        patterns.extend(self._detect_double_bottom(low, swing_lows))
        patterns.extend(self._detect_head_shoulders(high, swing_highs, close))
        patterns.extend(self._detect_triangles(high, low, swing_highs, swing_lows))
        patterns.extend(self._detect_flags(close, high, low, volume))

        # Support/Resistance levels
        support, resistance = self._find_sr_levels(high, low, close, volume)

        # Market structure (HH/HL/LH/LL)
        structure = self._analyze_structure(swing_highs, swing_lows, high, low)

        # Breakout detection
        breakouts = self._detect_breakouts(close, volume, support, resistance, df)

        # Composite signal
        signal = self._compute_signal(patterns, structure, breakouts)

        return {
            "patterns": patterns,
            "support_levels": support,
            "resistance_levels": resistance,
            "structure": structure,
            "breakouts": breakouts,
            "signal": signal,
        }

    def _find_swings(self, high: np.ndarray, low: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Find swing high and low indices."""
        swing_high_idx = argrelextrema(high, np.greater, order=self.swing_order)[0]
        swing_low_idx = argrelextrema(low, np.less, order=self.swing_order)[0]
        return swing_high_idx, swing_low_idx

    def _detect_double_top(self, high: np.ndarray,
                           swing_highs: np.ndarray) -> List[Dict]:
        """Detect double top pattern."""
        patterns = []
        if len(swing_highs) < 2:
            return patterns

        for i in range(len(swing_highs) - 1):
            idx1, idx2 = swing_highs[i], swing_highs[i + 1]
            h1, h2 = high[idx1], high[idx2]

            # Peaks should be within 1% of each other
            if abs(h1 - h2) / h1 < 0.01:
                # Check for valley between peaks
                valley = min(high[idx1:idx2 + 1])
                neckline = valley
                distance = idx2 - idx1
                if self.min_pattern_bars < distance < self.max_pattern_bars:
                    target = neckline - (h1 - neckline)
                    confidence = min(0.8, 0.5 + (distance / self.max_pattern_bars) * 0.3)
                    patterns.append({
                        "type": "double_top",
                        "confidence": confidence,
                        "direction": "bearish",
                        "target_price": target,
                        "neckline": neckline,
                        "peak_price": max(h1, h2),
                        "index": idx2,
                    })
        return patterns

    def _detect_double_bottom(self, low: np.ndarray,
                              swing_lows: np.ndarray) -> List[Dict]:
        """Detect double bottom pattern."""
        patterns = []
        if len(swing_lows) < 2:
            return patterns

        for i in range(len(swing_lows) - 1):
            idx1, idx2 = swing_lows[i], swing_lows[i + 1]
            l1, l2 = low[idx1], low[idx2]

            if abs(l1 - l2) / l1 < 0.01:
                peak = max(low[idx1:idx2 + 1])
                neckline = peak
                distance = idx2 - idx1
                if self.min_pattern_bars < distance < self.max_pattern_bars:
                    target = neckline + (neckline - l1)
                    confidence = min(0.8, 0.5 + (distance / self.max_pattern_bars) * 0.3)
                    patterns.append({
                        "type": "double_bottom",
                        "confidence": confidence,
                        "direction": "bullish",
                        "target_price": target,
                        "neckline": neckline,
                        "trough_price": min(l1, l2),
                        "index": idx2,
                    })
        return patterns

    def _detect_head_shoulders(self, high: np.ndarray,
                                swing_highs: np.ndarray,
                                close: np.ndarray) -> List[Dict]:
        """Detect head and shoulders and inverse patterns."""
        patterns = []
        if len(swing_highs) < 3:
            return patterns

        for i in range(len(swing_highs) - 2):
            idx1, idx2, idx3 = swing_highs[i], swing_highs[i+1], swing_highs[i+2]
            h1, h2, h3 = high[idx1], high[idx2], high[idx3]

            # H&S: middle peak is highest, shoulders roughly equal
            if h2 > h1 and h2 > h3 and abs(h1 - h3) / h1 < 0.03:
                neckline = min(close[idx1:idx3+1])
                target = neckline - (h2 - neckline)
                patterns.append({
                    "type": "head_and_shoulders",
                    "confidence": 0.7,
                    "direction": "bearish",
                    "target_price": target,
                    "neckline": neckline,
                    "index": idx3,
                })

            # Inverse H&S: middle trough is lowest (using highs as approximation)
            if h2 < h1 and h2 < h3 and abs(h1 - h3) / h1 < 0.03:
                neckline = max(close[idx1:idx3+1])
                target = neckline + (neckline - h2)
                patterns.append({
                    "type": "inverse_head_and_shoulders",
                    "confidence": 0.7,
                    "direction": "bullish",
                    "target_price": target,
                    "neckline": neckline,
                    "index": idx3,
                })
        return patterns

    def _detect_triangles(self, high: np.ndarray, low: np.ndarray,
                          swing_highs: np.ndarray,
                          swing_lows: np.ndarray) -> List[Dict]:
        """Detect ascending, descending, and symmetrical triangles."""
        patterns = []
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return patterns

        recent_highs = swing_highs[-4:] if len(swing_highs) >= 4 else swing_highs
        recent_lows = swing_lows[-4:] if len(swing_lows) >= 4 else swing_lows

        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            high_vals = high[recent_highs]
            low_vals = low[recent_lows]

            highs_flat = abs(high_vals[-1] - high_vals[0]) / high_vals[0] < 0.01
            lows_rising = low_vals[-1] > low_vals[0] * 1.005
            highs_falling = high_vals[-1] < high_vals[0] * 0.995
            lows_flat = abs(low_vals[-1] - low_vals[0]) / low_vals[0] < 0.01

            if highs_flat and lows_rising:
                patterns.append({
                    "type": "ascending_triangle",
                    "confidence": 0.65,
                    "direction": "bullish",
                    "target_price": high_vals[-1] + (high_vals[-1] - low_vals[0]),
                    "index": max(recent_highs[-1], recent_lows[-1]),
                })
            elif lows_flat and highs_falling:
                patterns.append({
                    "type": "descending_triangle",
                    "confidence": 0.65,
                    "direction": "bearish",
                    "target_price": low_vals[-1] - (high_vals[0] - low_vals[-1]),
                    "index": max(recent_highs[-1], recent_lows[-1]),
                })
            elif highs_falling and lows_rising:
                patterns.append({
                    "type": "symmetrical_triangle",
                    "confidence": 0.5,
                    "direction": "neutral",
                    "target_price": None,
                    "index": max(recent_highs[-1], recent_lows[-1]),
                })

        return patterns

    def _detect_flags(self, close: np.ndarray, high: np.ndarray,
                      low: np.ndarray, volume: np.ndarray) -> List[Dict]:
        """Detect flag and pennant patterns (consolidation after strong move)."""
        patterns = []
        if len(close) < 30:
            return patterns

        # Look at last 30 bars
        recent = close[-30:]
        recent_vol = volume[-30:]

        # Check for strong move in first 10 bars
        pole_move = (recent[10] - recent[0]) / recent[0]
        # Check for consolidation in last 15 bars
        consol_range = (max(recent[-15:]) - min(recent[-15:])) / recent[-15]

        if abs(pole_move) > 0.03 and consol_range < abs(pole_move) * 0.5:
            direction = "bullish" if pole_move > 0 else "bearish"
            target = recent[-1] + (recent[10] - recent[0]) if pole_move > 0 else \
                     recent[-1] - (recent[0] - recent[10])
            patterns.append({
                "type": "flag" if consol_range > 0.01 else "pennant",
                "confidence": 0.6,
                "direction": direction,
                "target_price": target,
                "index": len(close) - 1,
            })

        return patterns

    def _find_sr_levels(self, high: np.ndarray, low: np.ndarray,
                        close: np.ndarray, volume: np.ndarray) -> Tuple[List[float], List[float]]:
        """Find support and resistance levels using pivot points and volume clusters."""
        current_price = close[-1]

        # Pivot points from swing highs/lows
        swing_highs, swing_lows = self._find_swings(high, low)
        pivot_prices = []
        for idx in swing_highs:
            pivot_prices.append(high[idx])
        for idx in swing_lows:
            pivot_prices.append(low[idx])

        if not pivot_prices:
            return [], []

        # Cluster nearby levels
        clustered = self._cluster_levels(pivot_prices)

        # Separate into support and resistance
        support = sorted([l for l in clustered if l < current_price], reverse=True)[:5]
        resistance = sorted([l for l in clustered if l > current_price])[:5]

        return support, resistance

    def _cluster_levels(self, prices: List[float]) -> List[float]:
        """Cluster nearby price levels."""
        if not prices:
            return []
        prices = sorted(prices)
        clusters = []
        current_cluster = [prices[0]]

        for p in prices[1:]:
            if abs(p - current_cluster[-1]) / current_cluster[-1] < self.sr_cluster_pct:
                current_cluster.append(p)
            else:
                clusters.append(np.mean(current_cluster))
                current_cluster = [p]
        clusters.append(np.mean(current_cluster))
        return clusters

    def _analyze_structure(self, swing_highs: np.ndarray,
                           swing_lows: np.ndarray,
                           high: np.ndarray, low: np.ndarray) -> str:
        """Determine market structure: uptrend, downtrend, or range."""
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "unknown"

        recent_sh = swing_highs[-3:] if len(swing_highs) >= 3 else swing_highs
        recent_sl = swing_lows[-3:] if len(swing_lows) >= 3 else swing_lows

        sh_vals = high[recent_sh]
        sl_vals = low[recent_sl]

        # Higher highs and higher lows = uptrend
        hh = all(sh_vals[i] > sh_vals[i-1] for i in range(1, len(sh_vals)))
        hl = all(sl_vals[i] > sl_vals[i-1] for i in range(1, len(sl_vals)))

        # Lower highs and lower lows = downtrend
        lh = all(sh_vals[i] < sh_vals[i-1] for i in range(1, len(sh_vals)))
        ll = all(sl_vals[i] < sl_vals[i-1] for i in range(1, len(sl_vals)))

        if hh and hl:
            return "uptrend"
        elif lh and ll:
            return "downtrend"
        elif hh and ll:
            return "expanding"
        elif lh and hl:
            return "contracting"
        else:
            return "range"

    def _detect_breakouts(self, close: np.ndarray, volume: np.ndarray,
                          support: List[float], resistance: List[float],
                          df: pd.DataFrame) -> List[Dict]:
        """Detect breakouts with volume confirmation."""
        breakouts = []
        if len(close) < 3:
            return breakouts

        current = close[-1]
        prev = close[-2]
        vol_ma = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
        current_vol = volume[-1]
        vol_confirm = current_vol > vol_ma * 1.5

        # Resistance breakout
        for level in resistance[:3]:
            if prev < level and current > level and vol_confirm:
                breakouts.append({
                    "type": "resistance_breakout",
                    "level": level,
                    "direction": "bullish",
                    "volume_confirmed": True,
                    "confidence": 0.7 if vol_confirm else 0.4,
                })

        # Support breakdown
        for level in support[:3]:
            if prev > level and current < level and vol_confirm:
                breakouts.append({
                    "type": "support_breakdown",
                    "level": level,
                    "direction": "bearish",
                    "volume_confirmed": True,
                    "confidence": 0.7 if vol_confirm else 0.4,
                })

        return breakouts

    def _compute_signal(self, patterns: List[Dict], structure: str,
                        breakouts: List[Dict]) -> float:
        """Compute composite pattern signal (-1 to 1)."""
        signal = 0.0

        # Structure contribution
        structure_scores = {
            "uptrend": 0.3, "downtrend": -0.3,
            "expanding": 0, "contracting": 0,
            "range": 0, "unknown": 0,
        }
        signal += structure_scores.get(structure, 0)

        # Pattern contributions
        for p in patterns:
            direction_mult = 1 if p["direction"] == "bullish" else -1 if p["direction"] == "bearish" else 0
            signal += direction_mult * p["confidence"] * 0.3

        # Breakout contributions
        for b in breakouts:
            direction_mult = 1 if b["direction"] == "bullish" else -1
            signal += direction_mult * b["confidence"] * 0.4

        return np.clip(signal, -1, 1)
