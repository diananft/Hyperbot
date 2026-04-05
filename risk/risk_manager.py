"""Position sizing and risk management."""

import numpy as np
from typing import Dict, List, Optional, Tuple
from utils.logger import get_logger

logger = get_logger("risk")


class RiskManager:
    """Manages position sizing, exposure limits, and correlation guards."""

    def __init__(self, config: dict):
        risk_cfg = config.get("risk", {})
        self.risk_per_trade = risk_cfg.get("risk_per_trade", 0.015)
        self.max_risk_per_trade = risk_cfg.get("max_risk_per_trade", 0.02)
        self.max_leverage = risk_cfg.get("max_leverage", 5.0)
        self.max_total_exposure = risk_cfg.get("max_total_exposure", 0.15)
        self.max_per_asset = risk_cfg.get("max_per_asset", 0.08)
        self.max_concurrent = risk_cfg.get("max_concurrent_positions", 3)
        self.correlation_threshold = risk_cfg.get("correlation_threshold", 0.7)

    def calculate_position_size(self, equity: float, entry_price: float,
                                stop_loss_price: float,
                                signal_size_pct: float = 1.0,
                                regime_size_mult: float = 1.0) -> float:
        """Calculate position size based on risk.

        position_size = (equity * risk_pct) / |entry - stop_loss|

        Returns size in base currency units.
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            return 0.0

        risk_distance = abs(entry_price - stop_loss_price)
        if risk_distance == 0:
            return 0.0

        risk_pct = min(self.risk_per_trade, self.max_risk_per_trade)
        risk_amount = equity * risk_pct

        # Position size in USD
        position_usd = risk_amount / (risk_distance / entry_price)

        # Apply signal-based sizing
        position_usd *= signal_size_pct

        # Apply regime multiplier
        position_usd *= regime_size_mult

        # Leverage cap
        max_position = equity * self.max_leverage
        position_usd = min(position_usd, max_position)

        # Per-asset cap
        max_asset_position = equity * self.max_per_asset * self.max_leverage
        position_usd = min(position_usd, max_asset_position)

        # Convert to quantity
        quantity = position_usd / entry_price

        return quantity

    def check_exposure_limits(self, equity: float,
                              current_positions: List[dict],
                              new_asset: str,
                              new_position_usd: float) -> Tuple[bool, str]:
        """Check if a new position would exceed exposure limits.

        Returns (allowed, reason).
        """
        # Max concurrent positions
        if len(current_positions) >= self.max_concurrent:
            return False, f"Max concurrent positions ({self.max_concurrent}) reached"

        # Total exposure
        total_exposure = sum(
            abs(p.get("size", 0) * p.get("entry_price", 0))
            for p in current_positions
        )
        total_exposure += new_position_usd
        if total_exposure > equity * self.max_total_exposure * self.max_leverage:
            return False, f"Total exposure would exceed {self.max_total_exposure:.0%} limit"

        # Per-asset exposure
        asset_exposure = sum(
            abs(p.get("size", 0) * p.get("entry_price", 0))
            for p in current_positions
            if p.get("asset", "") == new_asset
        )
        asset_exposure += new_position_usd
        if asset_exposure > equity * self.max_per_asset * self.max_leverage:
            return False, f"Asset exposure would exceed {self.max_per_asset:.0%} limit"

        return True, "OK"

    def check_correlation(self, new_asset: str, current_positions: List[dict],
                          price_data: Dict[str, list]) -> bool:
        """Check if new asset is too correlated with existing positions.

        If correlation > threshold, treat as same position for sizing.
        """
        if not current_positions or not price_data:
            return True

        new_prices = price_data.get(new_asset, [])
        if len(new_prices) < 20:
            return True

        for pos in current_positions:
            existing_asset = pos.get("asset", "")
            existing_prices = price_data.get(existing_asset, [])
            if len(existing_prices) < 20:
                continue

            # Calculate correlation on returns
            min_len = min(len(new_prices), len(existing_prices))
            new_returns = np.diff(np.log(new_prices[-min_len:])) if min_len > 1 else []
            existing_returns = np.diff(np.log(existing_prices[-min_len:])) if min_len > 1 else []

            if len(new_returns) > 0 and len(existing_returns) > 0:
                corr = np.corrcoef(new_returns, existing_returns)[0, 1]
                if abs(corr) > self.correlation_threshold:
                    logger.warning(f"{new_asset} highly correlated ({corr:.2f}) with "
                                 f"{existing_asset} - treating as same position")
                    return False

        return True

    def scale_in_size(self, base_size: float, add_number: int) -> float:
        """Calculate size for scaling into a position.

        Each add reduces size by 50%.
        """
        return base_size * (0.5 ** add_number)

    def max_loss_check(self, equity: float, entry_price: float,
                       stop_loss_price: float, quantity: float) -> bool:
        """Verify position won't exceed 2% equity risk."""
        risk = abs(entry_price - stop_loss_price) * quantity
        max_risk = equity * self.max_risk_per_trade
        return risk <= max_risk
