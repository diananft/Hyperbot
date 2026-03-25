"""Portfolio-level risk controls and circuit breakers."""

import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from utils.logger import get_logger

logger = get_logger("portfolio")


class PortfolioManager:
    """Manages portfolio-level risk controls and circuit breakers."""

    def __init__(self, config: dict):
        cb = config.get("circuit_breakers", {})
        self.daily_loss_halt = cb.get("daily_loss_halt", 0.03)
        self.weekly_loss_halt = cb.get("weekly_loss_halt", 0.05)
        self.max_drawdown = cb.get("max_drawdown", 0.10)
        self.drawdown_halt_hours = cb.get("drawdown_halt_hours", 24)
        self.recovery_size_pct = cb.get("recovery_size_pct", 0.50)
        self.recovery_hours = cb.get("recovery_hours", 48)
        self.min_win_rate = cb.get("min_win_rate", 0.30)
        self.min_win_rate_trades = cb.get("min_win_rate_trades", 20)
        self.consecutive_loss_pause = cb.get("consecutive_loss_pause", 5)
        self.consecutive_loss_halt_hours = cb.get("consecutive_loss_halt_hours", 4)
        self.consecutive_loss_recovery_trades = cb.get("consecutive_loss_recovery_trades", 3)

        # State
        self.peak_equity = 0
        self.daily_start_equity = 0
        self.weekly_start_equity = 0
        self._halt_until = 0  # Unix timestamp
        self._recovery_until = 0  # Unix timestamp
        self._recovery_trades_remaining = 0
        self._daily_reset_hour = 0  # UTC hour for daily reset
        self._last_daily_reset = 0
        self._last_weekly_reset = 0

    def initialize(self, equity: float):
        """Initialize with current equity."""
        self.peak_equity = equity
        self.daily_start_equity = equity
        self.weekly_start_equity = equity
        self._last_daily_reset = time.time()
        self._last_weekly_reset = time.time()

    def update(self, equity: float) -> Dict:
        """Update portfolio state and check circuit breakers.

        Returns dict with:
            - trading_allowed: bool
            - size_multiplier: float (1.0 or reduced)
            - halt_reason: str or None
            - drawdown_pct: float
            - daily_pnl_pct: float
        """
        now = time.time()

        # Reset daily/weekly counters
        self._check_resets(now, equity)

        # Update peak
        if equity > self.peak_equity:
            self.peak_equity = equity

        # Calculate metrics
        drawdown_pct = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0
        daily_pnl = equity - self.daily_start_equity
        daily_pnl_pct = daily_pnl / self.daily_start_equity if self.daily_start_equity > 0 else 0
        weekly_pnl = equity - self.weekly_start_equity
        weekly_pnl_pct = weekly_pnl / self.weekly_start_equity if self.weekly_start_equity > 0 else 0

        # Check if still in halt
        if now < self._halt_until:
            remaining = (self._halt_until - now) / 3600
            return {
                "trading_allowed": False,
                "size_multiplier": 0,
                "halt_reason": f"Circuit breaker active ({remaining:.1f}h remaining)",
                "drawdown_pct": drawdown_pct,
                "daily_pnl_pct": daily_pnl_pct,
            }

        # Check drawdown circuit breaker
        if drawdown_pct >= self.max_drawdown:
            self._halt_until = now + self.drawdown_halt_hours * 3600
            self._recovery_until = self._halt_until + self.recovery_hours * 3600
            logger.critical(f"MAX DRAWDOWN {drawdown_pct:.1%} - halting for {self.drawdown_halt_hours}h")
            return {
                "trading_allowed": False,
                "size_multiplier": 0,
                "halt_reason": f"Max drawdown {drawdown_pct:.1%} exceeded",
                "drawdown_pct": drawdown_pct,
                "daily_pnl_pct": daily_pnl_pct,
                "flatten": True,  # Signal to flatten all positions
            }

        # Check daily loss
        if daily_pnl_pct <= -self.daily_loss_halt:
            # Halt until end of day (next 00:00 UTC)
            now_dt = datetime.now(timezone.utc)
            next_day = now_dt.replace(hour=0, minute=0, second=0) + timedelta(days=1)
            self._halt_until = next_day.timestamp()
            logger.warning(f"Daily loss {daily_pnl_pct:.1%} - halting until {next_day}")
            return {
                "trading_allowed": False,
                "size_multiplier": 0,
                "halt_reason": f"Daily loss limit {daily_pnl_pct:.1%}",
                "drawdown_pct": drawdown_pct,
                "daily_pnl_pct": daily_pnl_pct,
            }

        # Check weekly loss
        if weekly_pnl_pct <= -self.weekly_loss_halt:
            self._halt_until = now + 24 * 3600
            logger.warning(f"Weekly loss {weekly_pnl_pct:.1%} - halting 24h")
            return {
                "trading_allowed": False,
                "size_multiplier": 0,
                "halt_reason": f"Weekly loss limit {weekly_pnl_pct:.1%}",
                "drawdown_pct": drawdown_pct,
                "daily_pnl_pct": daily_pnl_pct,
            }

        # Check recovery mode
        size_mult = 1.0
        if now < self._recovery_until:
            size_mult = self.recovery_size_pct

        if self._recovery_trades_remaining > 0:
            size_mult = min(size_mult, self.recovery_size_pct)

        return {
            "trading_allowed": True,
            "size_multiplier": size_mult,
            "halt_reason": None,
            "drawdown_pct": drawdown_pct,
            "daily_pnl_pct": daily_pnl_pct,
            "recovery_mode": now < self._recovery_until or self._recovery_trades_remaining > 0,
        }

    def check_trade_stats(self, trade_stats: Dict) -> Tuple[float, Optional[str]]:
        """Check trade statistics and return size adjustment.

        Returns (size_multiplier, warning_message).
        """
        win_rate = trade_stats.get("win_rate", 0.5)
        consec_losses = trade_stats.get("consecutive_losses", 0)
        total_trades = trade_stats.get("total_trades", 0)

        size_mult = 1.0
        warning = None

        # Win rate check
        if total_trades >= self.min_win_rate_trades and win_rate < self.min_win_rate:
            size_mult = 0.5
            warning = f"Win rate {win_rate:.0%} below {self.min_win_rate:.0%} threshold"
            logger.warning(warning)

        # Consecutive losses
        if consec_losses >= self.consecutive_loss_pause:
            self._halt_until = time.time() + self.consecutive_loss_halt_hours * 3600
            self._recovery_trades_remaining = self.consecutive_loss_recovery_trades
            warning = f"{consec_losses} consecutive losses - pausing {self.consecutive_loss_halt_hours}h"
            logger.warning(warning)
            return 0, warning

        return size_mult, warning

    def on_trade_complete(self):
        """Called after each trade completes."""
        if self._recovery_trades_remaining > 0:
            self._recovery_trades_remaining -= 1

    def _check_resets(self, now: float, equity: float):
        """Check if daily/weekly counters should reset."""
        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)

        # Daily reset at 00:00 UTC
        if now - self._last_daily_reset > 86400:
            self.daily_start_equity = equity
            self._last_daily_reset = now
            logger.info(f"Daily reset. Start equity: ${equity:,.2f}")

        # Weekly reset
        if now - self._last_weekly_reset > 604800:
            self.weekly_start_equity = equity
            self._last_weekly_reset = now
            logger.info(f"Weekly reset. Start equity: ${equity:,.2f}")
