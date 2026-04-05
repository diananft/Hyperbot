"""SQLite database for trade history, signals, and equity snapshots."""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from utils.logger import get_logger

logger = get_logger("database")


class Database:
    def __init__(self, db_path: str = "hyperbot.db"):
        self.db_path = db_path
        self.conn = None
        self._connect()
        self._create_tables()

    def _connect(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                action TEXT NOT NULL,
                entry_price REAL,
                exit_price REAL,
                quantity REAL,
                pnl REAL,
                pnl_pct REAL,
                fees REAL DEFAULT 0,
                signal_score REAL,
                regime TEXT,
                stop_loss REAL,
                take_profit REAL,
                duration_minutes REAL,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                asset TEXT NOT NULL,
                composite_score REAL,
                trend_score REAL,
                momentum_score REAL,
                volume_score REAL,
                orderbook_score REAL,
                pattern_score REAL,
                mtf_score REAL,
                regime TEXT,
                action TEXT
            );

            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                equity REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl_day REAL DEFAULT 0,
                drawdown_pct REAL DEFAULT 0,
                open_positions INTEGER DEFAULT 0,
                margin_used REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                order_id TEXT,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                quantity REAL,
                status TEXT,
                filled_price REAL,
                filled_quantity REAL,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_asset ON trades(asset);
            CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
            CREATE INDEX IF NOT EXISTS idx_equity_timestamp ON equity_snapshots(timestamp);
            CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(timestamp);
        """)
        self.conn.commit()

    def log_trade(self, asset: str, side: str, action: str,
                  entry_price: float = None, exit_price: float = None,
                  quantity: float = None, pnl: float = None,
                  pnl_pct: float = None, fees: float = 0,
                  signal_score: float = None, regime: str = None,
                  stop_loss: float = None, take_profit: float = None,
                  duration_minutes: float = None, notes: str = None):
        self.conn.execute(
            """INSERT INTO trades (timestamp, asset, side, action, entry_price,
               exit_price, quantity, pnl, pnl_pct, fees, signal_score, regime,
               stop_loss, take_profit, duration_minutes, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), asset, side, action,
             entry_price, exit_price, quantity, pnl, pnl_pct, fees,
             signal_score, regime, stop_loss, take_profit, duration_minutes, notes)
        )
        self.conn.commit()

    def log_signal(self, asset: str, composite_score: float,
                   trend_score: float = 0, momentum_score: float = 0,
                   volume_score: float = 0, orderbook_score: float = 0,
                   pattern_score: float = 0, mtf_score: float = 0,
                   regime: str = None, action: str = None):
        self.conn.execute(
            """INSERT INTO signals (timestamp, asset, composite_score,
               trend_score, momentum_score, volume_score, orderbook_score,
               pattern_score, mtf_score, regime, action)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), asset, composite_score,
             trend_score, momentum_score, volume_score, orderbook_score,
             pattern_score, mtf_score, regime, action)
        )
        self.conn.commit()

    def log_equity(self, equity: float, unrealized_pnl: float = 0,
                   realized_pnl_day: float = 0, drawdown_pct: float = 0,
                   open_positions: int = 0, margin_used: float = 0):
        self.conn.execute(
            """INSERT INTO equity_snapshots (timestamp, equity, unrealized_pnl,
               realized_pnl_day, drawdown_pct, open_positions, margin_used)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), equity, unrealized_pnl,
             realized_pnl_day, drawdown_pct, open_positions, margin_used)
        )
        self.conn.commit()

    def log_order(self, order_id: str, asset: str, side: str,
                  order_type: str, price: float = None, quantity: float = None,
                  status: str = None, filled_price: float = None,
                  filled_quantity: float = None, error: str = None):
        self.conn.execute(
            """INSERT INTO orders (timestamp, order_id, asset, side, order_type,
               price, quantity, status, filled_price, filled_quantity, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), order_id, asset, side,
             order_type, price, quantity, status, filled_price,
             filled_quantity, error)
        )
        self.conn.commit()

    def get_recent_trades(self, limit: int = 50, asset: str = None):
        query = "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?"
        params = [limit]
        if asset:
            query = "SELECT * FROM trades WHERE asset=? ORDER BY timestamp DESC LIMIT ?"
            params = [asset, limit]
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def get_equity_history(self, days: int = 30):
        return [dict(r) for r in self.conn.execute(
            """SELECT * FROM equity_snapshots
               WHERE timestamp > datetime('now', ?)
               ORDER BY timestamp""",
            (f"-{days} days",)
        ).fetchall()]

    def get_daily_pnl(self, days: int = 30):
        return [dict(r) for r in self.conn.execute(
            """SELECT DATE(timestamp) as date,
                      SUM(pnl) as total_pnl,
                      COUNT(*) as num_trades,
                      SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                      SUM(fees) as total_fees
               FROM trades
               WHERE timestamp > datetime('now', ?)
               GROUP BY DATE(timestamp)
               ORDER BY date""",
            (f"-{days} days",)
        ).fetchall()]

    def get_trade_stats(self, lookback_trades: int = 20):
        rows = self.conn.execute(
            """SELECT pnl, pnl_pct FROM trades
               WHERE action='close' AND pnl IS NOT NULL
               ORDER BY timestamp DESC LIMIT ?""",
            (lookback_trades,)
        ).fetchall()
        if not rows:
            return {"win_rate": 0.5, "avg_win": 0, "avg_loss": 0,
                    "consecutive_losses": 0, "total_trades": 0}
        wins = [r["pnl"] for r in rows if r["pnl"] > 0]
        losses = [r["pnl"] for r in rows if r["pnl"] <= 0]
        # Count consecutive losses from most recent
        consec = 0
        for r in rows:
            if r["pnl"] <= 0:
                consec += 1
            else:
                break
        return {
            "win_rate": len(wins) / len(rows) if rows else 0.5,
            "avg_win": sum(wins) / len(wins) if wins else 0,
            "avg_loss": sum(losses) / len(losses) if losses else 0,
            "consecutive_losses": consec,
            "total_trades": len(rows),
        }

    def close(self):
        if self.conn:
            self.conn.close()
