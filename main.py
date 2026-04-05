#!/usr/bin/env python3
"""Hyperbot - Automated Crypto Trading Bot for Hyperliquid.

Usage:
    python main.py                    # Run in paper mode (default)
    python main.py --mode live        # Run in live mode
    python main.py --mode backtest    # Run backtest
    python main.py --config custom.yaml
"""

import os
import sys
import time
import signal
import argparse
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.exchange import HyperliquidExchange
from core.data_feed import DataFeed
from core.order_manager import OrderManager
from core.position_manager import PositionManager
from strategy.signal_engine import SignalEngine, SignalType
from strategy.regime_detector import RegimeDetector
from risk.risk_manager import RiskManager
from risk.stop_loss import StopLossManager
from risk.portfolio import PortfolioManager
from backtest.backtester import Backtester
from backtest.data_loader import BacktestDataLoader
from backtest.optimizer import WalkForwardOptimizer
from dashboard.web_dashboard import init_dashboard, start_dashboard
from utils.logger import setup_logging, get_logger, log_trade, log_signal
from utils.database import Database
from utils.notifier import Notifier


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


class Hyperbot:
    """Main trading bot controller."""

    def __init__(self, config: dict):
        self.config = config
        self.mode = config.get("mode", "paper")
        self.assets = config.get("assets", ["BTC", "ETH"])
        self.running = False

        # Logging
        log_cfg = config.get("logging", {})
        setup_logging(log_cfg.get("log_dir", "logs"), log_cfg.get("level", "INFO"))
        self.logger = get_logger("main")
        self.trade_logger = get_logger("trades")
        self.signal_logger = get_logger("signals")

        self.logger.info(f"Hyperbot initializing in {self.mode} mode")
        self.logger.info(f"Assets: {self.assets}")

        # Core modules
        self.exchange = HyperliquidExchange(config)
        self.data_feed = DataFeed(self.exchange, config)
        self.db = Database()
        self.notifier = Notifier(config.get("notifications", {}))

        # Strategy
        self.signal_engine = SignalEngine(config)

        # Risk
        self.risk_manager = RiskManager(config)
        self.stop_loss_mgr = StopLossManager(config)
        self.portfolio_mgr = PortfolioManager(config)

        # Execution
        self.order_manager = OrderManager(self.exchange, config, self.db)
        self.position_manager = PositionManager()

        # Timing
        self._last_heartbeat = time.time()
        self._last_equity_snapshot = time.time()
        self._last_daily_summary = 0
        self._last_candle_update = {}  # asset -> timestamp

    def run(self):
        """Main entry point - route to appropriate mode."""
        if self.mode == "backtest":
            self._run_backtest()
        else:
            self._run_trading()

    def _run_backtest(self):
        """Run backtesting mode."""
        self.logger.info("Starting backtest mode")

        # Load historical data
        loader = BacktestDataLoader(self.exchange, self.config)
        data = loader.load_all_assets(self.assets,
                                       self.config.get("timeframes", {}).get("execution", "15m"))

        if not any(len(df) > 0 for df in data.values()):
            self.logger.error("No data available for backtesting")
            return

        # Run backtest
        bt = Backtester(self.config)
        results = bt.run(data)

        # Optionally run walk-forward optimization
        if self.config.get("backtest", {}).get("walk_forward", {}).get("enabled", False):
            optimizer = WalkForwardOptimizer(self.config)
            opt_results = optimizer.optimize(data)
            self.logger.info(f"Optimization results: {opt_results}")

        self.logger.info("Backtest complete")

    def _run_trading(self):
        """Run live/paper trading mode."""
        self.logger.info(f"Starting {self.mode} trading")

        # Verify connection
        if not self.exchange.is_connected():
            self.logger.error("Cannot connect to Hyperliquid API")
            return

        # Get initial equity
        equity = self.exchange.get_equity()
        if equity <= 0 and self.mode == "live":
            self.logger.error("Zero equity detected. Aborting.")
            return

        self.logger.info(f"Connected. Equity: ${equity:,.2f}")
        self.portfolio_mgr.initialize(equity)

        # Initialize data feed
        self.data_feed.initialize()

        # Set leverage for all assets
        for asset in self.assets:
            max_lev = int(min(self.config.get("risk", {}).get("max_leverage", 5), 5))
            self.exchange.set_leverage(asset, max_lev)

        # Reconcile positions from exchange
        self._reconcile_positions()

        # Start WebSocket
        self.data_feed.start_websocket()

        # Start dashboard
        dash_cfg = self.config.get("dashboard", {})
        init_dashboard(self.db, self.position_manager, self.portfolio_mgr,
                      self.data_feed, self.exchange, self.mode,
                      kill_callback=self._kill_switch)
        start_dashboard(dash_cfg.get("host", "0.0.0.0"),
                       dash_cfg.get("port", 8080))

        # Notify start
        self.notifier.notify_system("Bot Started",
                                     f"Mode: {self.mode}\nEquity: ${equity:,.2f}\n"
                                     f"Assets: {', '.join(self.assets)}")

        # Main loop
        self.running = True
        self._register_signals()
        self.logger.info("Entering main trading loop")

        try:
            while self.running:
                self._trading_cycle()
                time.sleep(1)  # 1s between checks
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received")
        except Exception as e:
            self.logger.critical(f"Main loop crash: {e}", exc_info=True)
            self.notifier.notify_risk_alert("CRASH", str(e))
        finally:
            self._shutdown()

    def _trading_cycle(self):
        """Single iteration of the trading loop."""
        now = time.time()

        # Heartbeat check (every 5 minutes)
        if now - self._last_heartbeat > 300:
            self._heartbeat()
            self._last_heartbeat = now

        # Update prices
        self.data_feed.update_all_prices()
        self.position_manager.update_prices(self.data_feed.mid_prices)

        # Check connectivity
        if not self.data_feed.ws_connected:
            if not self.exchange.is_connected():
                self.logger.error("API disconnected")
                self.notifier.notify_risk_alert("DISCONNECT", "API not responding")
                time.sleep(5)
                return

        # Get equity and check portfolio limits
        equity = self.exchange.get_equity()
        if equity <= 0:
            return

        portfolio_check = self.portfolio_mgr.update(equity)
        if portfolio_check.get("flatten"):
            self.logger.critical("FLATTEN triggered by portfolio manager")
            self._flatten_all()
            return

        if not portfolio_check.get("trading_allowed", True):
            return  # Halted

        # Check trade stats for size adjustment
        trade_stats = self.db.get_trade_stats()
        stats_mult, stats_warning = self.portfolio_mgr.check_trade_stats(trade_stats)
        if stats_warning:
            self.notifier.notify_risk_alert("Trade Stats", stats_warning)
        if stats_mult == 0:
            return  # Paused due to consecutive losses

        # For each asset: update data, generate signals, manage positions
        for asset in self.assets:
            try:
                self._process_asset(asset, equity, portfolio_check, stats_mult)
            except Exception as e:
                self.logger.error(f"Error processing {asset}: {e}", exc_info=True)

        # Update orderbooks periodically
        for asset in self.assets:
            self.data_feed.update_orderbook(asset)

        # Cancel stale orders
        self.order_manager.cancel_stale_orders()

        # Equity snapshot (every hour)
        if now - self._last_equity_snapshot > 3600:
            self.db.log_equity(
                equity=equity,
                unrealized_pnl=sum(p.pnl for p in self.position_manager.get_all_positions()),
                drawdown_pct=portfolio_check.get("drawdown_pct", 0),
                open_positions=len(self.position_manager.get_all_positions()),
            )
            self._last_equity_snapshot = now

        # Daily summary at 00:00 UTC
        utc_now = datetime.now(timezone.utc)
        if utc_now.hour == 0 and utc_now.minute < 2 and now - self._last_daily_summary > 3600:
            self._send_daily_summary(equity, portfolio_check)
            self._last_daily_summary = now

    def _process_asset(self, asset: str, equity: float,
                       portfolio_check: dict, stats_mult: float):
        """Process a single asset: update candles, generate signals, trade."""
        exec_tf = self.config.get("timeframes", {}).get("execution", "15m")

        # Update candles
        df = self.data_feed.update_candles(asset, exec_tf)
        if len(df) < 50:
            return

        # Check if new candle closed
        last_ts = df["timestamp"].iloc[-1]
        prev_ts = self._last_candle_update.get(asset, 0)
        if last_ts <= prev_ts:
            # Only process on candle close (or first run)
            if prev_ts > 0:
                return
        self._last_candle_update[asset] = last_ts

        # Build multi-timeframe candles
        candles = {}
        for tf_name, tf_val in self.config.get("timeframes", {}).items():
            candles[tf_val] = self.data_feed.get_candle_df(asset, tf_val)

        # Get orderbook
        orderbook = self.data_feed.orderbooks.get(
            asset.replace("-USD", "").replace("-PERP", ""),
            self.data_feed.orderbooks.get(asset, {})
        )

        # Get funding rate
        funding_rates = self.exchange.get_funding_rates(asset)
        clean_asset = asset.replace("-USD", "").replace("-PERP", "")
        funding = funding_rates.get(clean_asset, {}).get("funding_rate", 0)

        # Generate signal
        signal = self.signal_engine.generate_signal(asset, candles, orderbook, funding)

        # Log signal (only log to console when actionable)
        if abs(signal.score) >= 0.3:
            log_signal(self.signal_logger, asset=asset, score=signal.score,
                       type=signal.signal_type.value, regime=signal.regime,
                       components=signal.components)
        self.db.log_signal(
            asset=asset, composite_score=signal.score,
            trend_score=signal.components.get("trend", 0),
            momentum_score=signal.components.get("momentum", 0),
            volume_score=signal.components.get("volume", 0),
            orderbook_score=signal.components.get("orderbook", 0),
            pattern_score=signal.components.get("patterns", 0),
            mtf_score=signal.components.get("mtf_confluence", 0),
            regime=signal.regime, action=signal.signal_type.value,
        )

        # Manage existing position
        if self.position_manager.has_position(asset):
            self._manage_position(asset, signal, equity, funding)
        else:
            # Check for new entry
            if self.signal_engine.should_enter(signal):
                self._enter_position(asset, signal, equity, portfolio_check,
                                     stats_mult, df, funding)

    def _enter_position(self, asset: str, signal, equity: float,
                        portfolio_check: dict, stats_mult: float,
                        df, funding: float):
        """Enter a new position."""
        is_long = signal.score > 0
        current_price = self.data_feed.get_price(asset)
        if current_price <= 0:
            return

        # Get ATR for stop loss
        last = df.iloc[-1] if len(df) > 0 else {}
        atr = last.get("atr", current_price * 0.02)

        # Regime analysis for multipliers
        regime_result = self.signal_engine.regime_detector.detect(df)
        regime_stop_mult = regime_result.get("stop_multiplier", 1.0)
        regime_size_mult = regime_result.get("size_multiplier", 1.0)

        # Calculate stop loss
        # Look for structural support/resistance
        pattern_result = self.signal_engine.pattern_recognizer.analyze(df)
        sr_levels = pattern_result.get("support_levels", []) if is_long else \
                    pattern_result.get("resistance_levels", [])
        structural = sr_levels[0] if sr_levels else None

        stop = self.stop_loss_mgr.calculate_initial_stop(
            current_price, atr, is_long, regime_stop_mult, structural
        )

        # Position sizing
        signal_size = self.signal_engine.get_position_size_pct(signal)
        portfolio_size_mult = portfolio_check.get("size_multiplier", 1.0)

        quantity = self.risk_manager.calculate_position_size(
            equity, current_price, stop,
            signal_size * stats_mult * portfolio_size_mult,
            regime_size_mult,
        )

        if quantity <= 0:
            return

        # Check exposure limits
        current_positions = [{
            "asset": p.asset, "size": p.remaining_quantity,
            "entry_price": p.entry_price
        } for p in self.position_manager.get_all_positions()]

        allowed, reason = self.risk_manager.check_exposure_limits(
            equity, current_positions, asset, quantity * current_price
        )
        if not allowed:
            self.logger.info(f"Position blocked for {asset}: {reason}")
            return

        # Max loss check (NON-NEGOTIABLE: 2% cap)
        if not self.risk_manager.max_loss_check(equity, current_price, stop, quantity):
            self.logger.warning(f"Position {asset} would exceed 2% risk cap")
            return

        # Reconcile with exchange before trading
        exchange_positions = self.order_manager.reconcile_positions()

        # Calculate take profits
        tp = self.stop_loss_mgr.calculate_take_profits(current_price, stop, is_long)

        # Execute entry
        result = self.order_manager.execute_entry(
            asset, is_long, quantity, current_price, signal.score,
            stop_loss=stop, take_profit=tp["tp1"]["price"]
        )

        if result.get("status") in ("filled", "ok") or result.get("paper"):
            filled_price = result.get("filled_price", current_price)
            filled_qty = result.get("filled_size", quantity)

            pos = self.position_manager.open_position(
                asset=asset,
                side="long" if is_long else "short",
                entry_price=filled_price,
                quantity=filled_qty,
                stop_loss=stop,
                take_profits=tp,
                signal_score=signal.score,
                regime=signal.regime,
            )

            # Log trade
            self.db.log_trade(
                asset=asset, side="long" if is_long else "short",
                action="open", entry_price=filled_price,
                quantity=filled_qty, signal_score=signal.score,
                regime=signal.regime, stop_loss=stop,
                take_profit=tp["tp1"]["price"],
            )
            log_trade(self.trade_logger, action="open", asset=asset,
                     side="long" if is_long else "short",
                     price=filled_price, quantity=filled_qty,
                     stop_loss=stop, score=signal.score)

            self.notifier.notify_trade("OPEN", asset,
                                        "LONG" if is_long else "SHORT",
                                        filled_price, filled_qty)

    def _manage_position(self, asset: str, signal, equity: float,
                         funding: float):
        """Manage an existing position: trailing stops, TP, exit signals."""
        pos = self.position_manager.get_position(asset)
        if not pos:
            return

        current_price = self.data_feed.get_price(asset)
        if current_price <= 0:
            return

        exec_tf = self.config.get("timeframes", {}).get("execution", "15m")
        df = self.data_feed.get_candle_df(asset, exec_tf)
        atr = df.iloc[-1].get("atr", current_price * 0.02) if len(df) > 0 else current_price * 0.02

        # Update trailing stop
        regime_result = self.signal_engine.regime_detector.detect(df)
        new_stop, stop_reason = self.stop_loss_mgr.update_trailing_stop(
            pos.stop_loss, pos.entry_price, current_price, atr,
            pos.is_long, pos.candles_held,
            regime_result.get("stop_multiplier", 1.0)
        )
        if new_stop != pos.stop_loss:
            pos.stop_loss = new_stop
            self.logger.info(f"Stop updated for {asset}: ${new_stop:,.2f} ({stop_reason})")

        # Check stop hit
        if self.stop_loss_mgr.check_stop_hit(current_price, pos.stop_loss, pos.is_long):
            self._close_position(asset, current_price, "stop_loss")
            return

        # Check TP1
        if not pos.tp1_hit and pos.take_profits:
            tp1 = pos.take_profits.get("tp1", {})
            if tp1 and self.stop_loss_mgr.check_tp_hit(current_price, tp1.get("price", 0), pos.is_long):
                close_pct = tp1.get("close_pct", 0.4)
                self._partial_close(asset, current_price, close_pct, "tp1")
                pos.tp1_hit = True
                pos.stop_loss = pos.entry_price * (1.001 if pos.is_long else 0.999)

        # Check TP2
        if pos.tp1_hit and not pos.tp2_hit and pos.take_profits:
            tp2 = pos.take_profits.get("tp2", {})
            if tp2 and self.stop_loss_mgr.check_tp_hit(current_price, tp2.get("price", 0), pos.is_long):
                close_pct = tp2.get("close_pct", 0.3)
                self._partial_close(asset, current_price, close_pct, "tp2")
                pos.tp2_hit = True

        # Check time exit
        profit_atr = abs(current_price - pos.entry_price) / max(atr, 1e-10)
        if self.stop_loss_mgr.check_time_exit(pos.candles_held, profit_atr):
            self._close_position(asset, current_price, "time_exit")
            return

        # Check funding exit
        if self.stop_loss_mgr.check_funding_exit(funding, pos.is_long):
            self._close_position(asset, current_price, "funding_exit")
            return

        # Check signal flip (exit)
        if self.signal_engine.should_exit(signal, pos.side):
            self._close_position(asset, current_price, "signal_flip")
            return

        # Increment candle counter
        pos.candles_held += 1

    def _close_position(self, asset: str, price: float, reason: str):
        """Close a position fully."""
        pos = self.position_manager.get_position(asset)
        if not pos:
            return

        # Execute exit
        self.order_manager.execute_exit(asset, pos.is_long,
                                         pos.remaining_quantity, price, reason)

        # Close in position manager
        trade = self.position_manager.close_position(asset, price)
        if trade:
            self.db.log_trade(
                asset=asset, side=trade["side"], action="close",
                entry_price=trade["entry_price"], exit_price=price,
                quantity=trade["quantity"], pnl=trade["pnl"],
                pnl_pct=trade["pnl_pct"],
                duration_minutes=trade["duration_minutes"],
                notes=reason,
            )
            log_trade(self.trade_logger, action="close", asset=asset,
                     side=trade["side"], price=price, pnl=trade["pnl"],
                     reason=reason)
            self.notifier.notify_trade("CLOSE", asset, trade["side"],
                                        price, trade["quantity"],
                                        trade["pnl"], trade["pnl_pct"])
            self.portfolio_mgr.on_trade_complete()

    def _partial_close(self, asset: str, price: float,
                       close_pct: float, reason: str):
        """Partially close a position."""
        pos = self.position_manager.get_position(asset)
        if not pos:
            return

        close_qty = pos.remaining_quantity * close_pct
        self.order_manager.execute_exit(asset, pos.is_long, close_qty, price, reason)
        trade = self.position_manager.close_position(asset, price, close_pct)
        if trade:
            self.db.log_trade(
                asset=asset, side=trade["side"], action="partial_close",
                entry_price=trade["entry_price"], exit_price=price,
                quantity=trade["quantity"], pnl=trade["pnl"],
                pnl_pct=trade["pnl_pct"], notes=reason,
            )
            self.notifier.notify_trade(f"PARTIAL CLOSE ({reason})", asset,
                                        trade["side"], price, trade["quantity"],
                                        trade["pnl"], trade["pnl_pct"])

    def _flatten_all(self):
        """Emergency: close all positions and cancel all orders."""
        self.logger.critical("FLATTENING ALL POSITIONS")
        self.order_manager.cancel_all()

        for asset in self.position_manager.flatten_all():
            price = self.data_feed.get_price(asset)
            if price > 0:
                self._close_position(asset, price, "flatten")

        self.notifier.notify_risk_alert("FLATTEN", "All positions closed")

    def _kill_switch(self):
        """Kill switch callback from dashboard."""
        self._flatten_all()
        self.running = False

    def _reconcile_positions(self):
        """Sync local position state with exchange."""
        try:
            positions = self.exchange.get_positions()
            for p in positions:
                asset = p["asset"]
                if not self.position_manager.has_position(asset):
                    self.logger.warning(f"Exchange has position in {asset} not tracked locally")
                    # Could auto-track, but safer to log and let user decide
        except Exception as e:
            self.logger.error(f"Reconciliation failed: {e}")

    def _heartbeat(self):
        """Periodic health check."""
        positions = self.position_manager.get_all_positions()
        self.logger.info(f"Heartbeat: {len(positions)} positions, "
                        f"WS={'connected' if self.data_feed.ws_connected else 'disconnected'}")

    def _send_daily_summary(self, equity: float, portfolio_check: dict):
        """Send daily summary notification."""
        daily_pnl = self.db.get_daily_pnl(1)
        pnl_today = daily_pnl[0] if daily_pnl else {"total_pnl": 0, "num_trades": 0, "wins": 0}
        trades_today = pnl_today.get("num_trades", 0)
        wins = pnl_today.get("wins", 0)
        win_rate = wins / trades_today if trades_today > 0 else 0

        self.notifier.notify_daily_summary(
            equity=equity,
            daily_pnl=pnl_today.get("total_pnl", 0),
            daily_pnl_pct=portfolio_check.get("daily_pnl_pct", 0),
            trades=trades_today,
            win_rate=win_rate,
            drawdown=portfolio_check.get("drawdown_pct", 0),
        )

    def _register_signals(self):
        """Register OS signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signal."""
        self.logger.info(f"Signal {signum} received, shutting down...")
        self.running = False

    def _shutdown(self):
        """Graceful shutdown procedure."""
        self.logger.info("Shutting down Hyperbot...")

        # Cancel all orders
        self.order_manager.cancel_all()

        # Stop WebSocket
        self.data_feed.stop_websocket()

        # Save data cache
        self.data_feed.save_cache()

        # Close database
        self.db.close()

        # Notify
        self.notifier.notify_system("Bot Stopped", "Graceful shutdown complete")

        self.logger.info("Shutdown complete")


def main():
    parser = argparse.ArgumentParser(description="Hyperbot Trading Bot")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--mode", choices=["paper", "live", "backtest"],
                       help="Override trading mode")
    args = parser.parse_args()

    # Load environment
    load_dotenv()

    # Load config
    config = load_config(args.config)

    # Override mode if specified
    if args.mode:
        config["mode"] = args.mode

    # Safety check: never default to live
    if config.get("mode") == "live":
        print("\n*** WARNING: LIVE TRADING MODE ***")
        print("This will trade with REAL money on your Hyperliquid account.")
        confirm = input("Type 'CONFIRM LIVE' to proceed: ")
        if confirm != "CONFIRM LIVE":
            print("Aborted.")
            return

    # Create and run bot
    bot = Hyperbot(config)
    bot.run()


if __name__ == "__main__":
    main()
