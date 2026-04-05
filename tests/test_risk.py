"""Tests for risk management modules."""

import pytest
from risk.risk_manager import RiskManager
from risk.stop_loss import StopLossManager
from risk.portfolio import PortfolioManager


class TestRiskManager:
    def setup_method(self):
        self.config = {
            "risk": {
                "risk_per_trade": 0.015,
                "max_risk_per_trade": 0.02,
                "max_leverage": 5.0,
                "max_total_exposure": 0.15,
                "max_per_asset": 0.08,
                "max_concurrent_positions": 3,
                "correlation_threshold": 0.7,
            }
        }
        self.rm = RiskManager(self.config)

    def test_position_size_basic(self):
        # 1.5% risk on $10000, entry $100, stop $97 (3% distance)
        qty = self.rm.calculate_position_size(10000, 100, 97)
        # Risk-based: $150 / 0.03 = $5000 USD -> 50 units
        # But capped by per-asset limit: 8% * 5x * $10000 = $4000 -> 40 units
        assert qty == pytest.approx(40.0, rel=0.01)

    def test_position_size_zero_distance(self):
        qty = self.rm.calculate_position_size(10000, 100, 100)
        assert qty == 0.0

    def test_max_loss_check_pass(self):
        # $100 entry, $97 stop, 5 units = $15 risk, equity $10000, max 2% = $200
        assert self.rm.max_loss_check(10000, 100, 97, 5) is True

    def test_max_loss_check_fail(self):
        # $100 entry, $97 stop, 100 units = $300 risk > $200
        assert self.rm.max_loss_check(10000, 100, 97, 100) is False

    def test_exposure_limits_max_positions(self):
        positions = [
            {"asset": "BTC", "size": 0.1, "entry_price": 50000},
            {"asset": "ETH", "size": 1, "entry_price": 3000},
            {"asset": "SOL", "size": 10, "entry_price": 100},
        ]
        allowed, reason = self.rm.check_exposure_limits(10000, positions, "ARB", 500)
        assert allowed is False
        assert "concurrent" in reason.lower()

    def test_exposure_limits_ok(self):
        positions = []
        allowed, reason = self.rm.check_exposure_limits(10000, positions, "BTC", 500)
        assert allowed is True

    def test_scale_in_size(self):
        assert self.rm.scale_in_size(100, 0) == 100
        assert self.rm.scale_in_size(100, 1) == 50
        assert self.rm.scale_in_size(100, 2) == 25


class TestStopLossManager:
    def setup_method(self):
        self.config = {
            "stop_loss": {
                "atr_multiplier": 1.5,
                "min_distance": 0.003,
                "max_distance": 0.03,
                "breakeven_trigger": 1.0,
                "trail_stage1_trigger": 2.0,
                "trail_stage1_atr": 1.0,
                "trail_stage2_trigger": 3.0,
                "trail_stage2_atr": 0.75,
                "time_decay_candles": 20,
                "time_decay_atr": 0.75,
                "time_exit_candles": 40,
                "time_exit_atr": 1.0,
                "high_vol_multiplier": 1.3,
                "low_vol_multiplier": 0.7,
            },
            "take_profit": {
                "tp1_ratio": 1.5,
                "tp1_close": 0.40,
                "tp2_ratio": 2.5,
                "tp2_close": 0.30,
                "tp3_trail_atr": 1.0,
                "funding_exit": 0.0005,
            }
        }
        self.sl = StopLossManager(self.config)

    def test_initial_stop_long(self):
        stop = self.sl.calculate_initial_stop(100, 2, True)
        # 1.5 * 2 = 3, so stop at 97
        assert stop == pytest.approx(97, abs=0.5)

    def test_initial_stop_short(self):
        stop = self.sl.calculate_initial_stop(100, 2, False)
        assert stop == pytest.approx(103, abs=0.5)

    def test_initial_stop_min_distance(self):
        # Very small ATR -> should use min distance
        stop = self.sl.calculate_initial_stop(100, 0.001, True)
        assert stop >= 100 * (1 - 0.03)  # Not below max distance

    def test_take_profits(self):
        tp = self.sl.calculate_take_profits(100, 97, True)
        assert tp["tp1"]["price"] == pytest.approx(104.5, abs=0.1)  # 1.5x risk
        assert tp["tp2"]["price"] == pytest.approx(107.5, abs=0.1)  # 2.5x risk
        assert tp["tp1"]["close_pct"] == 0.40

    def test_trailing_stop_breakeven(self):
        # Long: entry 100, current 102 (1x ATR profit with ATR=2)
        new_stop, reason = self.sl.update_trailing_stop(
            97, 100, 102, 2, True, 5
        )
        assert new_stop >= 100  # Should be at breakeven
        assert reason == "breakeven"

    def test_trailing_stop_never_backwards(self):
        # Current stop is 99, but new calculation would give 97
        new_stop, reason = self.sl.update_trailing_stop(
            99, 100, 100.5, 2, True, 2
        )
        assert new_stop >= 99  # Never move backwards

    def test_stop_hit_long(self):
        assert self.sl.check_stop_hit(96, 97, True) is True
        assert self.sl.check_stop_hit(98, 97, True) is False

    def test_stop_hit_short(self):
        assert self.sl.check_stop_hit(104, 103, False) is True
        assert self.sl.check_stop_hit(102, 103, False) is False

    def test_time_exit(self):
        assert self.sl.check_time_exit(40, 0.5) is True
        assert self.sl.check_time_exit(40, 1.5) is False
        assert self.sl.check_time_exit(30, 0.5) is False

    def test_funding_exit(self):
        assert self.sl.check_funding_exit(0.001, True) is True
        assert self.sl.check_funding_exit(0.0001, True) is False
        assert self.sl.check_funding_exit(-0.001, False) is True


class TestPortfolioManager:
    def setup_method(self):
        self.config = {
            "circuit_breakers": {
                "daily_loss_halt": 0.03,
                "weekly_loss_halt": 0.05,
                "max_drawdown": 0.10,
                "drawdown_halt_hours": 24,
                "recovery_size_pct": 0.50,
                "recovery_hours": 48,
                "min_win_rate": 0.30,
                "min_win_rate_trades": 20,
                "consecutive_loss_pause": 5,
                "consecutive_loss_halt_hours": 4,
                "consecutive_loss_recovery_trades": 3,
            }
        }
        self.pm = PortfolioManager(self.config)
        self.pm.initialize(10000)

    def test_normal_update(self):
        result = self.pm.update(10100)
        assert result["trading_allowed"] is True
        assert result["size_multiplier"] == 1.0

    def test_daily_loss_halt(self):
        result = self.pm.update(9600)  # 4% loss > 3% limit
        assert result["trading_allowed"] is False
        assert "daily" in result["halt_reason"].lower()

    def test_drawdown_halt(self):
        self.pm.peak_equity = 10000
        result = self.pm.update(8900)  # 11% drawdown > 10% limit
        assert result["trading_allowed"] is False
        assert result.get("flatten") is True

    def test_consecutive_losses(self):
        stats = {"win_rate": 0.4, "consecutive_losses": 6, "total_trades": 30}
        mult, warning = self.pm.check_trade_stats(stats)
        assert mult == 0
        assert warning is not None

    def test_low_win_rate(self):
        stats = {"win_rate": 0.25, "consecutive_losses": 0, "total_trades": 25}
        mult, warning = self.pm.check_trade_stats(stats)
        assert mult == 0.5
        assert warning is not None
