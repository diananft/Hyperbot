"""Backtesting engine - replays historical data through signal/risk logic."""

import time
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from pathlib import Path

from strategy.indicators import TechnicalIndicators
from strategy.signal_engine import SignalEngine, SignalType
from strategy.regime_detector import RegimeDetector
from risk.risk_manager import RiskManager
from risk.stop_loss import StopLossManager
from backtest.performance import PerformanceAnalyzer
from utils.logger import get_logger

logger = get_logger("backtester")


class Backtester:
    """Replay historical data through the full trading system."""

    def __init__(self, config: dict):
        self.config = config
        bt_cfg = config.get("backtest", {})
        self.initial_capital = bt_cfg.get("initial_capital", 10000)
        self.slippage_market = bt_cfg.get("slippage_market", 0.0005)
        self.slippage_limit = bt_cfg.get("slippage_limit", 0.0)
        self.fee_maker = bt_cfg.get("fee_maker", 0.0001)
        self.fee_taker = bt_cfg.get("fee_taker", 0.00035)

        self.signal_engine = SignalEngine(config)
        self.risk_manager = RiskManager(config)
        self.stop_loss_mgr = StopLossManager(config)
        self.regime_detector = RegimeDetector(config)
        self.performance = PerformanceAnalyzer(self.initial_capital)

    def run(self, data: Dict[str, pd.DataFrame]) -> Dict:
        """Run backtest on multiple assets.

        Args:
            data: {asset: DataFrame with OHLCV}

        Returns: dict with metrics, trades, equity_curve
        """
        logger.info(f"Starting backtest with ${self.initial_capital:,.2f} capital")

        equity = self.initial_capital
        peak_equity = equity
        equity_curve = [equity]
        trades = []
        positions = {}  # asset -> position dict

        # Process each asset
        for asset, df in data.items():
            if len(df) < 100:
                logger.warning(f"Insufficient data for {asset}: {len(df)} candles")
                continue

            # Calculate all indicators
            df_ind = self.signal_engine.indicators.calculate_all(df)

            logger.info(f"Backtesting {asset}: {len(df_ind)} candles")

            # Walk through each candle
            for i in range(50, len(df_ind)):
                candle = df_ind.iloc[i]
                current_price = candle["close"]
                high = candle["high"]
                low = candle["low"]
                atr = candle.get("atr", 0)

                # Update existing position
                if asset in positions:
                    pos = positions[asset]

                    # Check stop loss hit (using low/high of candle)
                    if pos["is_long"]:
                        if low <= pos["stop_loss"]:
                            # Stop hit
                            exit_price = pos["stop_loss"]
                            pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
                            fees = abs(pnl) * self.fee_taker
                            pnl -= fees
                            equity += pnl
                            trades.append({
                                "asset": asset, "side": "long", "action": "close",
                                "entry_price": pos["entry_price"],
                                "exit_price": exit_price,
                                "quantity": pos["quantity"],
                                "pnl": pnl, "pnl_pct": pnl / (pos["entry_price"] * pos["quantity"]),
                                "fees": fees, "reason": "stop_loss",
                                "candles_held": pos["candles_held"],
                                "timestamp": candle["timestamp"],
                            })
                            del positions[asset]
                            continue
                    else:
                        if high >= pos["stop_loss"]:
                            exit_price = pos["stop_loss"]
                            pnl = (pos["entry_price"] - exit_price) * pos["quantity"]
                            fees = abs(pnl) * self.fee_taker
                            pnl -= fees
                            equity += pnl
                            trades.append({
                                "asset": asset, "side": "short", "action": "close",
                                "entry_price": pos["entry_price"],
                                "exit_price": exit_price,
                                "quantity": pos["quantity"],
                                "pnl": pnl, "pnl_pct": pnl / (pos["entry_price"] * pos["quantity"]),
                                "fees": fees, "reason": "stop_loss",
                                "candles_held": pos["candles_held"],
                                "timestamp": candle["timestamp"],
                            })
                            del positions[asset]
                            continue

                    # Check take profit
                    tp1 = pos.get("tp1")
                    if tp1 and not pos.get("tp1_hit"):
                        hit = (high >= tp1 if pos["is_long"] else low <= tp1)
                        if hit:
                            close_qty = pos["quantity"] * 0.4
                            pnl = (tp1 - pos["entry_price"]) * close_qty if pos["is_long"] else \
                                  (pos["entry_price"] - tp1) * close_qty
                            fees = abs(pnl) * self.fee_maker
                            pnl -= fees
                            equity += pnl
                            pos["tp1_hit"] = True
                            pos["quantity"] -= close_qty
                            pos["stop_loss"] = pos["entry_price"]  # Move to breakeven
                            trades.append({
                                "asset": asset, "side": pos["side"], "action": "partial_close",
                                "entry_price": pos["entry_price"],
                                "exit_price": tp1,
                                "quantity": close_qty,
                                "pnl": pnl, "pnl_pct": pnl / (pos["entry_price"] * close_qty),
                                "fees": fees, "reason": "tp1",
                                "timestamp": candle["timestamp"],
                            })

                    tp2 = pos.get("tp2")
                    if tp2 and pos.get("tp1_hit") and not pos.get("tp2_hit"):
                        hit = (high >= tp2 if pos["is_long"] else low <= tp2)
                        if hit:
                            close_qty = pos["quantity"] * 0.5  # 30% of original
                            pnl = (tp2 - pos["entry_price"]) * close_qty if pos["is_long"] else \
                                  (pos["entry_price"] - tp2) * close_qty
                            fees = abs(pnl) * self.fee_maker
                            pnl -= fees
                            equity += pnl
                            pos["tp2_hit"] = True
                            pos["quantity"] -= close_qty
                            trades.append({
                                "asset": asset, "side": pos["side"], "action": "partial_close",
                                "entry_price": pos["entry_price"],
                                "exit_price": tp2,
                                "quantity": close_qty,
                                "pnl": pnl, "pnl_pct": pnl / (pos["entry_price"] * close_qty),
                                "fees": fees, "reason": "tp2",
                                "timestamp": candle["timestamp"],
                            })

                    # Update trailing stop
                    if atr > 0:
                        regime = self.regime_detector.detect(df_ind.iloc[:i+1])
                        new_stop, reason = self.stop_loss_mgr.update_trailing_stop(
                            pos["stop_loss"], pos["entry_price"],
                            current_price, atr, pos["is_long"],
                            pos["candles_held"],
                            regime.get("stop_multiplier", 1.0)
                        )
                        pos["stop_loss"] = new_stop

                    # Time exit check
                    if pos["candles_held"] >= 40:
                        profit_atr = abs(current_price - pos["entry_price"]) / max(atr, 1e-10)
                        if profit_atr < 1.0:
                            pnl = (current_price - pos["entry_price"]) * pos["quantity"] if pos["is_long"] else \
                                  (pos["entry_price"] - current_price) * pos["quantity"]
                            fees = abs(pnl) * self.fee_taker
                            pnl -= fees
                            equity += pnl
                            trades.append({
                                "asset": asset, "side": pos["side"], "action": "close",
                                "entry_price": pos["entry_price"],
                                "exit_price": current_price,
                                "quantity": pos["quantity"],
                                "pnl": pnl, "pnl_pct": pnl / (pos["entry_price"] * pos["quantity"]),
                                "fees": fees, "reason": "time_exit",
                                "candles_held": pos["candles_held"],
                                "timestamp": candle["timestamp"],
                            })
                            del positions[asset]
                            continue

                    pos["candles_held"] += 1

                # Generate signal (if no position)
                if asset not in positions and equity > 0:
                    # Use lookback window for signal
                    window = df_ind.iloc[max(0, i-200):i+1]
                    candles_dict = {self.config.get("timeframes", {}).get("execution", "15m"): window}

                    signal = self.signal_engine.generate_signal(
                        asset, candles_dict
                    )

                    if self.signal_engine.should_enter(signal):
                        is_long = signal.score > 0
                        regime = self.regime_detector.detect(window)

                        # Calculate stop loss
                        if atr > 0:
                            stop = self.stop_loss_mgr.calculate_initial_stop(
                                current_price, atr, is_long,
                                regime.get("stop_multiplier", 1.0)
                            )
                        else:
                            stop = current_price * (0.97 if is_long else 1.03)

                        # Position sizing
                        size_pct = self.signal_engine.get_position_size_pct(signal)
                        size_pct *= regime.get("size_multiplier", 1.0)

                        quantity = self.risk_manager.calculate_position_size(
                            equity, current_price, stop, size_pct,
                            regime.get("size_multiplier", 1.0)
                        )

                        if quantity > 0 and self.risk_manager.max_loss_check(
                            equity, current_price, stop, quantity
                        ):
                            # Apply slippage
                            entry = current_price * (1 + self.slippage_market) if is_long else \
                                    current_price * (1 - self.slippage_market)
                            entry_fees = entry * quantity * self.fee_taker

                            # Take profits
                            tp = self.stop_loss_mgr.calculate_take_profits(entry, stop, is_long)

                            positions[asset] = {
                                "side": "long" if is_long else "short",
                                "is_long": is_long,
                                "entry_price": entry,
                                "quantity": quantity,
                                "stop_loss": stop,
                                "tp1": tp["tp1"]["price"],
                                "tp2": tp["tp2"]["price"],
                                "tp1_hit": False,
                                "tp2_hit": False,
                                "candles_held": 0,
                                "signal_score": signal.score,
                            }
                            equity -= entry_fees

                            trades.append({
                                "asset": asset, "side": "long" if is_long else "short",
                                "action": "open",
                                "entry_price": entry, "quantity": quantity,
                                "pnl": -entry_fees, "fees": entry_fees,
                                "signal_score": signal.score,
                                "regime": regime.get("regime", ""),
                                "timestamp": candle["timestamp"],
                            })

                equity_curve.append(equity)
                peak_equity = max(peak_equity, equity)

        # Close any remaining positions at last price
        for asset, pos in list(positions.items()):
            last_price = data[asset]["close"].iloc[-1]
            pnl = (last_price - pos["entry_price"]) * pos["quantity"] if pos["is_long"] else \
                  (pos["entry_price"] - last_price) * pos["quantity"]
            fees = abs(pnl) * self.fee_taker
            pnl -= fees
            equity += pnl
            trades.append({
                "asset": asset, "side": pos["side"], "action": "close",
                "entry_price": pos["entry_price"],
                "exit_price": last_price,
                "quantity": pos["quantity"],
                "pnl": pnl, "fees": fees, "reason": "backtest_end",
            })
            equity_curve.append(equity)

        # Calculate metrics
        metrics = self.performance.calculate_metrics(trades, equity_curve)
        self.performance.print_report(metrics)
        self.performance.save_charts(equity_curve, trades)

        return {
            "metrics": metrics,
            "trades": trades,
            "equity_curve": equity_curve,
        }
