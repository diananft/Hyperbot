"""Position tracking and management."""

import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from utils.logger import get_logger

logger = get_logger("positions")


@dataclass
class Position:
    """Represents an open trading position."""
    asset: str
    side: str  # "long" or "short"
    entry_price: float
    quantity: float
    stop_loss: float
    take_profits: Dict = field(default_factory=dict)
    entry_time: float = field(default_factory=time.time)
    signal_score: float = 0.0
    regime: str = ""
    candles_held: int = 0
    tp1_hit: bool = False
    tp2_hit: bool = False
    remaining_quantity: float = 0.0
    max_price: float = 0.0  # For trailing (long)
    min_price: float = float("inf")  # For trailing (short)
    pnl: float = 0.0
    add_count: int = 0

    def __post_init__(self):
        self.remaining_quantity = self.quantity
        self.max_price = self.entry_price
        self.min_price = self.entry_price

    @property
    def is_long(self) -> bool:
        return self.side == "long"

    @property
    def unrealized_pnl(self) -> float:
        return self.pnl

    def update_price(self, current_price: float):
        """Update with current price."""
        if self.is_long:
            self.pnl = (current_price - self.entry_price) * self.remaining_quantity
            self.max_price = max(self.max_price, current_price)
        else:
            self.pnl = (self.entry_price - current_price) * self.remaining_quantity
            self.min_price = min(self.min_price, current_price)

    def duration_minutes(self) -> float:
        return (time.time() - self.entry_time) / 60


class PositionManager:
    """Manages all open positions."""

    def __init__(self):
        self.positions: Dict[str, Position] = {}  # asset -> Position

    def open_position(self, asset: str, side: str, entry_price: float,
                      quantity: float, stop_loss: float,
                      take_profits: Dict = None,
                      signal_score: float = 0, regime: str = "") -> Position:
        """Open a new position."""
        pos = Position(
            asset=asset,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profits=take_profits or {},
            signal_score=signal_score,
            regime=regime,
        )
        self.positions[asset] = pos
        logger.info(f"Opened {side} {asset}: {quantity:.6f} @ ${entry_price:,.2f}, "
                   f"SL=${stop_loss:,.2f}")
        return pos

    def close_position(self, asset: str, exit_price: float,
                       close_pct: float = 1.0) -> Optional[Dict]:
        """Close (partially or fully) a position.

        Returns trade details dict or None.
        """
        pos = self.positions.get(asset)
        if not pos:
            return None

        close_qty = pos.remaining_quantity * close_pct
        if pos.is_long:
            pnl = (exit_price - pos.entry_price) * close_qty
        else:
            pnl = (pos.entry_price - exit_price) * close_qty

        pnl_pct = pnl / (pos.entry_price * close_qty) if close_qty > 0 else 0

        trade_detail = {
            "asset": asset,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "quantity": close_qty,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "duration_minutes": pos.duration_minutes(),
            "signal_score": pos.signal_score,
            "regime": pos.regime,
            "candles_held": pos.candles_held,
        }

        pos.remaining_quantity -= close_qty
        if pos.remaining_quantity <= 0 or close_pct >= 1.0:
            del self.positions[asset]
            logger.info(f"Closed {pos.side} {asset}: ${pnl:+,.2f} ({pnl_pct:+.2%})")
        else:
            logger.info(f"Partial close {asset}: {close_pct:.0%}, "
                       f"remaining: {pos.remaining_quantity:.6f}")

        return trade_detail

    def get_position(self, asset: str) -> Optional[Position]:
        return self.positions.get(asset)

    def has_position(self, asset: str) -> bool:
        return asset in self.positions

    def get_all_positions(self) -> List[Position]:
        return list(self.positions.values())

    def update_prices(self, prices: Dict[str, float]):
        """Update all positions with current prices."""
        for asset, pos in self.positions.items():
            clean = asset.replace("-USD", "").replace("-PERP", "")
            price = prices.get(clean) or prices.get(asset, 0)
            if price > 0:
                pos.update_price(price)

    def increment_candles(self):
        """Increment candle counter for all positions."""
        for pos in self.positions.values():
            pos.candles_held += 1

    def total_exposure(self) -> float:
        """Total position exposure in USD."""
        return sum(abs(p.remaining_quantity * p.entry_price) for p in self.positions.values())

    def flatten_all(self) -> List[str]:
        """Get list of all assets to flatten."""
        return list(self.positions.keys())
