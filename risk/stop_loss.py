"""Stop loss and take profit management."""

import time
import numpy as np
from typing import Dict, Optional, Tuple
from utils.logger import get_logger

logger = get_logger("stop_loss")


class StopLossManager:
    """Manages initial stops, trailing stops, and take profit levels."""

    def __init__(self, config: dict):
        sl_cfg = config.get("stop_loss", {})
        self.atr_multiplier = sl_cfg.get("atr_multiplier", 1.5)
        self.min_distance = sl_cfg.get("min_distance", 0.003)
        self.max_distance = sl_cfg.get("max_distance", 0.03)
        self.breakeven_trigger = sl_cfg.get("breakeven_trigger", 1.0)
        self.trail_stage1_trigger = sl_cfg.get("trail_stage1_trigger", 2.0)
        self.trail_stage1_atr = sl_cfg.get("trail_stage1_atr", 1.0)
        self.trail_stage2_trigger = sl_cfg.get("trail_stage2_trigger", 3.0)
        self.trail_stage2_atr = sl_cfg.get("trail_stage2_atr", 0.75)
        self.time_decay_candles = sl_cfg.get("time_decay_candles", 20)
        self.time_decay_atr = sl_cfg.get("time_decay_atr", 0.75)
        self.time_exit_candles = sl_cfg.get("time_exit_candles", 40)
        self.time_exit_atr = sl_cfg.get("time_exit_atr", 1.0)
        self.high_vol_mult = sl_cfg.get("high_vol_multiplier", 1.3)
        self.low_vol_mult = sl_cfg.get("low_vol_multiplier", 0.7)

        tp_cfg = config.get("take_profit", {})
        self.tp1_ratio = tp_cfg.get("tp1_ratio", 1.5)
        self.tp1_close = tp_cfg.get("tp1_close", 0.40)
        self.tp2_ratio = tp_cfg.get("tp2_ratio", 2.5)
        self.tp2_close = tp_cfg.get("tp2_close", 0.30)
        self.tp3_trail_atr = tp_cfg.get("tp3_trail_atr", 1.0)
        self.funding_exit = tp_cfg.get("funding_exit", 0.0005)

    def calculate_initial_stop(self, entry_price: float, atr: float,
                                is_long: bool, regime_mult: float = 1.0,
                                structural_level: float = None) -> float:
        """Calculate initial stop loss price.

        1.5x ATR from entry, clamped between min/max distance.
        Uses structural level if within range.
        """
        distance = self.atr_multiplier * atr * regime_mult

        # Clamp distance
        min_dist = entry_price * self.min_distance
        max_dist = entry_price * self.max_distance
        distance = max(min_dist, min(distance, max_dist))

        if is_long:
            stop = entry_price - distance
            if structural_level and structural_level < entry_price:
                # Use structural level if it's closer and reasonable
                struct_dist = entry_price - structural_level
                if min_dist <= struct_dist <= max_dist:
                    stop = structural_level - (atr * 0.1)  # Slightly below structure
        else:
            stop = entry_price + distance
            if structural_level and structural_level > entry_price:
                struct_dist = structural_level - entry_price
                if min_dist <= struct_dist <= max_dist:
                    stop = structural_level + (atr * 0.1)

        return stop

    def calculate_take_profits(self, entry_price: float, stop_loss: float,
                                is_long: bool) -> Dict:
        """Calculate TP1, TP2, and TP3 levels."""
        risk = abs(entry_price - stop_loss)

        if is_long:
            tp1 = entry_price + risk * self.tp1_ratio
            tp2 = entry_price + risk * self.tp2_ratio
        else:
            tp1 = entry_price - risk * self.tp1_ratio
            tp2 = entry_price - risk * self.tp2_ratio

        return {
            "tp1": {"price": tp1, "close_pct": self.tp1_close},
            "tp2": {"price": tp2, "close_pct": self.tp2_close},
            "tp3": {"trail_atr": self.tp3_trail_atr, "close_pct": 1.0 - self.tp1_close - self.tp2_close},
        }

    def update_trailing_stop(self, current_stop: float, entry_price: float,
                             current_price: float, atr: float,
                             is_long: bool, candles_held: int,
                             regime_mult: float = 1.0) -> Tuple[float, str]:
        """Update trailing stop based on profit stages and time.

        Returns (new_stop, reason).
        Never moves stop backwards (against the position).
        """
        profit_atr = self._profit_in_atr(entry_price, current_price, atr, is_long)
        new_stop = current_stop
        reason = "no_change"

        if is_long:
            # Stage 1: Breakeven + 0.1%
            if profit_atr >= self.breakeven_trigger:
                be_stop = entry_price * 1.001
                if be_stop > new_stop:
                    new_stop = be_stop
                    reason = "breakeven"

            # Stage 2: Trail at 1x ATR
            if profit_atr >= self.trail_stage1_trigger:
                trail_stop = current_price - atr * self.trail_stage1_atr * regime_mult
                if trail_stop > new_stop:
                    new_stop = trail_stop
                    reason = "trail_stage1"

            # Stage 3: Tighter trail
            if profit_atr >= self.trail_stage2_trigger:
                trail_stop = current_price - atr * self.trail_stage2_atr * regime_mult
                if trail_stop > new_stop:
                    new_stop = trail_stop
                    reason = "trail_stage2"

            # Time decay
            if candles_held >= self.time_decay_candles and profit_atr < 0.5:
                decay_stop = current_price - atr * self.time_decay_atr * regime_mult
                if decay_stop > new_stop:
                    new_stop = decay_stop
                    reason = "time_decay"

            # Never move stop backwards
            new_stop = max(new_stop, current_stop)

        else:  # Short
            if profit_atr >= self.breakeven_trigger:
                be_stop = entry_price * 0.999
                if be_stop < new_stop:
                    new_stop = be_stop
                    reason = "breakeven"

            if profit_atr >= self.trail_stage1_trigger:
                trail_stop = current_price + atr * self.trail_stage1_atr * regime_mult
                if trail_stop < new_stop:
                    new_stop = trail_stop
                    reason = "trail_stage1"

            if profit_atr >= self.trail_stage2_trigger:
                trail_stop = current_price + atr * self.trail_stage2_atr * regime_mult
                if trail_stop < new_stop:
                    new_stop = trail_stop
                    reason = "trail_stage2"

            if candles_held >= self.time_decay_candles and profit_atr < 0.5:
                decay_stop = current_price + atr * self.time_decay_atr * regime_mult
                if decay_stop < new_stop:
                    new_stop = decay_stop
                    reason = "time_decay"

            new_stop = min(new_stop, current_stop)

        return new_stop, reason

    def check_time_exit(self, candles_held: int, profit_atr: float) -> bool:
        """Check if position should be closed due to time decay."""
        return (candles_held >= self.time_exit_candles and
                profit_atr < self.time_exit_atr)

    def check_funding_exit(self, funding_rate: float, is_long: bool) -> bool:
        """Check if funding rate warrants exit."""
        if is_long and funding_rate > self.funding_exit:
            return True
        if not is_long and funding_rate < -self.funding_exit:
            return True
        return False

    def check_stop_hit(self, current_price: float, stop_price: float,
                       is_long: bool) -> bool:
        """Check if stop loss has been hit."""
        if is_long:
            return current_price <= stop_price
        else:
            return current_price >= stop_price

    def check_tp_hit(self, current_price: float, tp_price: float,
                     is_long: bool) -> bool:
        """Check if take profit has been hit."""
        if is_long:
            return current_price >= tp_price
        else:
            return current_price <= tp_price

    def _profit_in_atr(self, entry: float, current: float,
                       atr: float, is_long: bool) -> float:
        """Calculate profit in ATR multiples."""
        if atr <= 0:
            return 0
        if is_long:
            return (current - entry) / atr
        else:
            return (entry - current) / atr
