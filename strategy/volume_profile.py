"""Volume profile analysis - POC, Value Area, HVN/LVN."""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from utils.logger import get_logger

logger = get_logger("volume_profile")


class VolumeProfileAnalyzer:
    """Build and analyze volume-at-price profiles."""

    def __init__(self, config: dict = None):
        self.num_bins = 100  # Number of price levels
        self.value_area_pct = 0.70  # 70% of volume for Value Area
        self.hvn_threshold = 1.5  # x average for High Volume Node
        self.lvn_threshold = 0.5  # x average for Low Volume Node

    def analyze(self, df: pd.DataFrame) -> Dict:
        """Build volume profile and identify key levels.

        Returns dict with: poc, vah, val, hvn_levels, lvn_levels,
                          current_position, signal
        """
        if len(df) < 20:
            return self._empty_result()

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values
        current_price = close[-1]

        # Build volume profile
        price_min = low.min()
        price_max = high.max()
        if price_max <= price_min:
            return self._empty_result()

        bin_edges = np.linspace(price_min, price_max, self.num_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        vol_profile = np.zeros(self.num_bins)

        # Distribute each candle's volume across price bins it touches
        for i in range(len(df)):
            candle_low = low[i]
            candle_high = high[i]
            candle_vol = volume[i]
            candle_close = close[i]
            candle_open = df["open"].values[i]

            # Determine buy/sell volume
            if candle_close >= candle_open:
                buy_pct = 0.6
            else:
                buy_pct = 0.4

            for j in range(self.num_bins):
                if bin_edges[j+1] >= candle_low and bin_edges[j] <= candle_high:
                    # Portion of candle range in this bin
                    overlap_low = max(bin_edges[j], candle_low)
                    overlap_high = min(bin_edges[j+1], candle_high)
                    candle_range = candle_high - candle_low
                    if candle_range > 0:
                        pct = (overlap_high - overlap_low) / candle_range
                    else:
                        pct = 1.0
                    vol_profile[j] += candle_vol * pct

        # POC (Point of Control) - highest volume price
        poc_idx = np.argmax(vol_profile)
        poc = bin_centers[poc_idx]

        # Value Area (70% of total volume around POC)
        total_vol = vol_profile.sum()
        if total_vol == 0:
            return self._empty_result()

        vah, val = self._find_value_area(vol_profile, bin_centers, poc_idx, total_vol)

        # HVN and LVN
        avg_vol = vol_profile.mean()
        hvn_levels = [bin_centers[i] for i in range(self.num_bins)
                      if vol_profile[i] > avg_vol * self.hvn_threshold]
        lvn_levels = [bin_centers[i] for i in range(self.num_bins)
                      if 0 < vol_profile[i] < avg_vol * self.lvn_threshold]

        # Delta volume profile (buy vs sell per level) - approximation
        delta_profile = self._build_delta_profile(df, bin_edges, bin_centers)

        # Current price position relative to profile
        if current_price > vah:
            position = "above_value"
        elif current_price < val:
            position = "below_value"
        else:
            position = "inside_value"

        # Signal
        signal = self._compute_signal(current_price, poc, vah, val, position, delta_profile)

        return {
            "poc": poc,
            "vah": vah,
            "val": val,
            "hvn_levels": hvn_levels[:5],
            "lvn_levels": lvn_levels[:5],
            "current_position": position,
            "vol_profile": vol_profile.tolist(),
            "price_levels": bin_centers.tolist(),
            "delta_profile": delta_profile,
            "signal": signal,
        }

    def _find_value_area(self, vol_profile: np.ndarray, bin_centers: np.ndarray,
                         poc_idx: int, total_vol: float) -> Tuple[float, float]:
        """Find Value Area High and Low (70% of volume around POC)."""
        target_vol = total_vol * self.value_area_pct
        current_vol = vol_profile[poc_idx]
        upper_idx = poc_idx
        lower_idx = poc_idx

        while current_vol < target_vol:
            can_go_up = upper_idx + 1 < len(vol_profile)
            can_go_down = lower_idx - 1 >= 0

            if not can_go_up and not can_go_down:
                break

            up_vol = vol_profile[upper_idx + 1] if can_go_up else 0
            down_vol = vol_profile[lower_idx - 1] if can_go_down else 0

            if up_vol >= down_vol and can_go_up:
                upper_idx += 1
                current_vol += up_vol
            elif can_go_down:
                lower_idx -= 1
                current_vol += down_vol
            elif can_go_up:
                upper_idx += 1
                current_vol += up_vol
            else:
                break

        vah = bin_centers[upper_idx]
        val = bin_centers[lower_idx]
        return vah, val

    def _build_delta_profile(self, df: pd.DataFrame, bin_edges: np.ndarray,
                             bin_centers: np.ndarray) -> Dict:
        """Build delta volume profile (buy vs sell per level)."""
        buy_vol = np.zeros(len(bin_centers))
        sell_vol = np.zeros(len(bin_centers))

        for i in range(len(df)):
            candle_close = df["close"].values[i]
            candle_open = df["open"].values[i]
            candle_low = df["low"].values[i]
            candle_high = df["high"].values[i]
            candle_vol = df["volume"].values[i]

            # Approximate buy/sell split
            if candle_close >= candle_open:
                buy_pct = (candle_close - candle_low) / max(candle_high - candle_low, 1e-10)
            else:
                buy_pct = (candle_high - candle_close) / max(candle_high - candle_low, 1e-10) * 0.4

            for j in range(len(bin_centers)):
                if bin_edges[j+1] >= candle_low and bin_edges[j] <= candle_high:
                    candle_range = candle_high - candle_low
                    if candle_range > 0:
                        overlap_low = max(bin_edges[j], candle_low)
                        overlap_high = min(bin_edges[j+1], candle_high)
                        pct = (overlap_high - overlap_low) / candle_range
                    else:
                        pct = 1.0
                    buy_vol[j] += candle_vol * pct * buy_pct
                    sell_vol[j] += candle_vol * pct * (1 - buy_pct)

        # Net delta at key levels
        total_buy = buy_vol.sum()
        total_sell = sell_vol.sum()
        return {
            "total_buy": total_buy,
            "total_sell": total_sell,
            "net_delta": total_buy - total_sell,
            "delta_ratio": total_buy / max(total_sell, 1e-10),
        }

    def _compute_signal(self, price: float, poc: float, vah: float,
                        val: float, position: str, delta: Dict) -> float:
        """Compute volume profile signal (-1 to 1)."""
        signal = 0.0

        # Position relative to value area
        if position == "above_value":
            signal += 0.2  # Breakout above = bullish, but watch for rejection
        elif position == "below_value":
            signal -= 0.2  # Below value = bearish
        else:
            # Inside value - mean reversion tendency
            if price > poc:
                signal -= 0.1  # Slight sell bias above POC in range
            else:
                signal += 0.1  # Slight buy bias below POC in range

        # Delta volume bias
        if delta["delta_ratio"] > 1.2:
            signal += 0.3  # More buying
        elif delta["delta_ratio"] < 0.8:
            signal -= 0.3  # More selling

        return np.clip(signal, -1, 1)

    def _empty_result(self):
        return {
            "poc": 0, "vah": 0, "val": 0,
            "hvn_levels": [], "lvn_levels": [],
            "current_position": "unknown",
            "vol_profile": [], "price_levels": [],
            "delta_profile": {"total_buy": 0, "total_sell": 0,
                             "net_delta": 0, "delta_ratio": 1},
            "signal": 0,
        }
