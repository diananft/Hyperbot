"""Tests for core modules."""

import pytest
import time
from unittest.mock import MagicMock, patch
from core.position_manager import PositionManager, Position


class TestPositionManager:
    def setup_method(self):
        self.pm = PositionManager()

    def test_open_position(self):
        pos = self.pm.open_position("BTC", "long", 50000, 0.1, 48000)
        assert self.pm.has_position("BTC")
        assert pos.entry_price == 50000
        assert pos.quantity == 0.1
        assert pos.remaining_quantity == 0.1
        assert pos.is_long is True

    def test_close_position_full(self):
        self.pm.open_position("BTC", "long", 50000, 0.1, 48000)
        trade = self.pm.close_position("BTC", 52000)
        assert trade is not None
        assert trade["pnl"] == pytest.approx(200, abs=1)  # (52000-50000)*0.1
        assert not self.pm.has_position("BTC")

    def test_close_position_partial(self):
        self.pm.open_position("BTC", "long", 50000, 0.1, 48000)
        trade = self.pm.close_position("BTC", 52000, close_pct=0.5)
        assert trade["quantity"] == pytest.approx(0.05, abs=0.001)
        assert self.pm.has_position("BTC")  # Still has remaining
        pos = self.pm.get_position("BTC")
        assert pos.remaining_quantity == pytest.approx(0.05, abs=0.001)

    def test_short_position_pnl(self):
        self.pm.open_position("ETH", "short", 3000, 1.0, 3200)
        trade = self.pm.close_position("ETH", 2800)
        assert trade["pnl"] == pytest.approx(200, abs=1)  # (3000-2800)*1.0

    def test_short_position_loss(self):
        self.pm.open_position("ETH", "short", 3000, 1.0, 3200)
        trade = self.pm.close_position("ETH", 3100)
        assert trade["pnl"] == pytest.approx(-100, abs=1)

    def test_update_prices(self):
        self.pm.open_position("BTC", "long", 50000, 0.1, 48000)
        self.pm.update_prices({"BTC": 51000})
        pos = self.pm.get_position("BTC")
        assert pos.pnl == pytest.approx(100, abs=1)

    def test_total_exposure(self):
        self.pm.open_position("BTC", "long", 50000, 0.1, 48000)
        self.pm.open_position("ETH", "short", 3000, 1.0, 3200)
        assert self.pm.total_exposure() == pytest.approx(8000, abs=100)

    def test_increment_candles(self):
        self.pm.open_position("BTC", "long", 50000, 0.1, 48000)
        self.pm.increment_candles()
        pos = self.pm.get_position("BTC")
        assert pos.candles_held == 1

    def test_flatten_all(self):
        self.pm.open_position("BTC", "long", 50000, 0.1, 48000)
        self.pm.open_position("ETH", "short", 3000, 1.0, 3200)
        assets = self.pm.flatten_all()
        assert "BTC" in assets
        assert "ETH" in assets

    def test_max_price_tracking(self):
        self.pm.open_position("BTC", "long", 50000, 0.1, 48000)
        self.pm.update_prices({"BTC": 52000})
        self.pm.update_prices({"BTC": 51000})
        pos = self.pm.get_position("BTC")
        assert pos.max_price == 52000


class TestDatabase:
    def test_database_operations(self, tmp_path):
        from utils.database import Database
        db = Database(str(tmp_path / "test.db"))

        # Log a trade
        db.log_trade("BTC", "long", "open", entry_price=50000,
                     quantity=0.1, signal_score=0.85)

        # Log another trade (close)
        db.log_trade("BTC", "long", "close", entry_price=50000,
                     exit_price=52000, quantity=0.1, pnl=200,
                     pnl_pct=0.04)

        # Get trades
        trades = db.get_recent_trades(10)
        assert len(trades) == 2

        # Log equity
        db.log_equity(10200, unrealized_pnl=0, realized_pnl_day=200)

        # Get stats
        stats = db.get_trade_stats()
        assert stats["total_trades"] >= 0

        db.close()
