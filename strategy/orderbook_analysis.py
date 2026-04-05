"""L2 orderbook analysis for trading signals."""

import time
import numpy as np
from typing import Dict, List, Tuple
from collections import deque
from utils.logger import get_logger

logger = get_logger("orderbook")


class OrderbookAnalyzer:
    """Analyze L2 orderbook data for trading signals."""

    def __init__(self, config: dict):
        ob_config = config.get("orderbook", {})
        self.depth_levels = ob_config.get("depth_levels", 20)
        self.wall_threshold = ob_config.get("wall_threshold", 3.0)
        # History for tracking changes
        self._history: Dict[str, deque] = {}
        self._max_history = 60  # Keep 60 snapshots

    def analyze(self, orderbook: dict) -> dict:
        """Analyze orderbook and return signals.

        Returns dict with:
            - imbalance: float (-1 to 1), positive = bid-heavy (bullish)
            - spread: float (absolute)
            - spread_bps: float (basis points)
            - micro_price: float (volume-weighted mid)
            - bid_walls: list of (price, size) for large levels
            - ask_walls: list of (price, size) for large levels
            - depth_bid: total bid depth
            - depth_ask: total ask depth
            - signal: float (-1 to 1) composite orderbook signal
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            return self._empty_result()

        best_bid = bids[0]["price"]
        best_ask = asks[0]["price"]
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_bps = (spread / mid) * 10000 if mid > 0 else 0

        # Depth
        bid_sizes = [b["size"] for b in bids]
        ask_sizes = [a["size"] for a in asks]
        total_bid = sum(bid_sizes)
        total_ask = sum(ask_sizes)

        # Imbalance ratio: (bid - ask) / (bid + ask)
        total = total_bid + total_ask
        imbalance = (total_bid - total_ask) / total if total > 0 else 0

        # Micro price (volume-weighted mid)
        micro_price = (best_bid * asks[0]["size"] + best_ask * bids[0]["size"]) / \
                      (bids[0]["size"] + asks[0]["size"]) if (bids[0]["size"] + asks[0]["size"]) > 0 else mid

        # Detect walls
        avg_bid_size = np.mean(bid_sizes) if bid_sizes else 0
        avg_ask_size = np.mean(ask_sizes) if ask_sizes else 0
        bid_walls = [(b["price"], b["size"]) for b in bids
                     if avg_bid_size > 0 and b["size"] > avg_bid_size * self.wall_threshold]
        ask_walls = [(a["price"], a["size"]) for a in asks
                     if avg_ask_size > 0 and a["size"] > avg_ask_size * self.wall_threshold]

        # Top-of-book imbalance (more weight)
        top_n = min(5, len(bids), len(asks))
        top_bid = sum(b["size"] for b in bids[:top_n])
        top_ask = sum(a["size"] for a in asks[:top_n])
        top_imbalance = (top_bid - top_ask) / (top_bid + top_ask) if (top_bid + top_ask) > 0 else 0

        # Absorption detection (need history)
        absorption = self._detect_absorption(orderbook)

        # Spoofing detection
        spoofing = self._detect_spoofing(orderbook)

        # Composite signal
        signal = self._compute_signal(imbalance, top_imbalance, bid_walls,
                                       ask_walls, absorption, micro_price, mid)

        return {
            "imbalance": imbalance,
            "top_imbalance": top_imbalance,
            "spread": spread,
            "spread_bps": spread_bps,
            "micro_price": micro_price,
            "mid_price": mid,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "depth_bid": total_bid,
            "depth_ask": total_ask,
            "absorption": absorption,
            "spoofing_detected": spoofing,
            "signal": signal,
        }

    def _detect_absorption(self, orderbook: dict) -> float:
        """Detect order absorption (large orders being eaten)."""
        asset_key = f"{orderbook.get('bids', [{}])[0].get('price', 0):.0f}" if orderbook.get("bids") else "unknown"
        if asset_key not in self._history:
            self._history[asset_key] = deque(maxlen=self._max_history)

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        snapshot = {
            "time": time.time(),
            "bid_depth": sum(b["size"] for b in bids),
            "ask_depth": sum(a["size"] for a in asks),
            "best_bid": bids[0]["price"] if bids else 0,
            "best_ask": asks[0]["price"] if asks else 0,
        }
        self._history[asset_key].append(snapshot)

        if len(self._history[asset_key]) < 5:
            return 0.0

        # Compare bid/ask depth changes
        recent = list(self._history[asset_key])[-5:]
        bid_changes = [recent[i+1]["bid_depth"] - recent[i]["bid_depth"]
                       for i in range(len(recent)-1)]
        ask_changes = [recent[i+1]["ask_depth"] - recent[i]["ask_depth"]
                       for i in range(len(recent)-1)]

        # If bids are being absorbed (decreasing) while price holds = bullish absorption
        bid_absorbed = sum(1 for x in bid_changes if x < 0)
        ask_absorbed = sum(1 for x in ask_changes if x < 0)

        price_change = recent[-1]["best_bid"] - recent[0]["best_bid"]

        if bid_absorbed > 2 and price_change >= 0:
            return 0.5  # Bullish absorption
        elif ask_absorbed > 2 and price_change <= 0:
            return -0.5  # Bearish absorption
        return 0.0

    def _detect_spoofing(self, orderbook: dict) -> bool:
        """Simple spoofing detection: large orders far from mid that appear/disappear."""
        # This is a basic implementation
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        if not bids or not asks:
            return False

        mid = (bids[0]["price"] + asks[0]["price"]) / 2
        avg_bid = np.mean([b["size"] for b in bids]) if bids else 0

        # Check for suspiciously large orders far from mid
        for bid in bids[5:]:  # Skip top 5 levels
            distance = (mid - bid["price"]) / mid
            if distance > 0.01 and bid["size"] > avg_bid * 5:
                return True
        for ask in asks[5:]:
            distance = (ask["price"] - mid) / mid
            if distance > 0.01 and ask["size"] > avg_bid * 5:
                return True
        return False

    def _compute_signal(self, imbalance: float, top_imbalance: float,
                        bid_walls: list, ask_walls: list,
                        absorption: float, micro_price: float,
                        mid: float) -> float:
        """Compute composite orderbook signal (-1 to 1)."""
        score = 0.0

        # Imbalance contribution (40%)
        score += top_imbalance * 0.4

        # Full depth imbalance (20%)
        score += imbalance * 0.2

        # Wall analysis (15%)
        wall_score = 0
        if bid_walls and not ask_walls:
            wall_score = 0.5
        elif ask_walls and not bid_walls:
            wall_score = -0.5
        elif bid_walls and ask_walls:
            bid_wall_vol = sum(w[1] for w in bid_walls)
            ask_wall_vol = sum(w[1] for w in ask_walls)
            total_wall = bid_wall_vol + ask_wall_vol
            if total_wall > 0:
                wall_score = (bid_wall_vol - ask_wall_vol) / total_wall
        score += wall_score * 0.15

        # Absorption (15%)
        score += absorption * 0.15

        # Micro price vs mid (10%)
        if mid > 0:
            micro_bias = (micro_price - mid) / mid * 100  # In percent
            score += np.clip(micro_bias, -1, 1) * 0.10

        return np.clip(score, -1, 1)

    def _empty_result(self):
        return {
            "imbalance": 0, "top_imbalance": 0, "spread": 0,
            "spread_bps": 0, "micro_price": 0, "mid_price": 0,
            "best_bid": 0, "best_ask": 0, "bid_walls": [],
            "ask_walls": [], "depth_bid": 0, "depth_ask": 0,
            "absorption": 0, "spoofing_detected": False, "signal": 0,
        }
