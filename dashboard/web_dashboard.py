"""Flask web dashboard for monitoring the trading bot."""

import json
import threading
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request

from utils.logger import get_logger

logger = get_logger("dashboard")

app = Flask(__name__, template_folder="templates")

# Global references (set by main.py)
_bot_state = {
    "db": None,
    "position_manager": None,
    "portfolio_manager": None,
    "data_feed": None,
    "exchange": None,
    "signal_engine": None,
    "running": True,
    "mode": "paper",
    "start_time": None,
    "kill_callback": None,
    "config": {},
}


def init_dashboard(db, position_manager, portfolio_manager,
                   data_feed, exchange, mode, kill_callback=None,
                   signal_engine=None, config=None):
    """Initialize dashboard with bot references."""
    _bot_state["db"] = db
    _bot_state["position_manager"] = position_manager
    _bot_state["portfolio_manager"] = portfolio_manager
    _bot_state["data_feed"] = data_feed
    _bot_state["exchange"] = exchange
    _bot_state["mode"] = mode
    _bot_state["start_time"] = datetime.now(timezone.utc).isoformat()
    _bot_state["kill_callback"] = kill_callback
    _bot_state["signal_engine"] = signal_engine
    _bot_state["config"] = config or {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """Get bot status."""
    equity = 0
    try:
        if _bot_state["exchange"]:
            equity = _bot_state["exchange"].get_equity()
    except Exception:
        pass

    positions = []
    total_pnl = 0
    if _bot_state["position_manager"]:
        for pos in _bot_state["position_manager"].get_all_positions():
            positions.append({
                "asset": pos.asset,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "quantity": pos.remaining_quantity,
                "pnl": pos.pnl,
                "pnl_pct": (pos.pnl / (pos.entry_price * pos.remaining_quantity) * 100) if pos.entry_price and pos.remaining_quantity else 0,
                "stop_loss": pos.stop_loss,
                "candles_held": pos.candles_held,
                "duration_min": pos.duration_minutes(),
                "signal_score": pos.signal_score,
                "tp1_hit": pos.tp1_hit,
                "tp2_hit": pos.tp2_hit,
            })
            total_pnl += pos.pnl

    portfolio = {}
    if _bot_state["portfolio_manager"]:
        portfolio = _bot_state["portfolio_manager"].update(equity)

    # Prices
    prices = {}
    if _bot_state["data_feed"]:
        prices = {k: v for k, v in _bot_state["data_feed"].mid_prices.items()
                  if k in (_bot_state["config"].get("assets", []))}

    return jsonify({
        "status": "running" if _bot_state["running"] else "stopped",
        "mode": _bot_state["mode"],
        "equity": equity,
        "positions": positions,
        "total_unrealized_pnl": total_pnl,
        "portfolio": portfolio,
        "start_time": _bot_state["start_time"],
        "ws_connected": _bot_state["data_feed"].ws_connected if _bot_state["data_feed"] else False,
        "prices": prices,
        "assets": _bot_state["config"].get("assets", []),
    })


@app.route("/api/trades")
def api_trades():
    """Get recent trades."""
    if _bot_state["db"]:
        limit = request.args.get("limit", 50, type=int)
        trades = _bot_state["db"].get_recent_trades(limit)
        return jsonify(trades)
    return jsonify([])


@app.route("/api/equity")
def api_equity():
    """Get equity history."""
    if _bot_state["db"]:
        days = request.args.get("days", 30, type=int)
        history = _bot_state["db"].get_equity_history(days)
        return jsonify(history)
    return jsonify([])


@app.route("/api/daily_pnl")
def api_daily_pnl():
    """Get daily PnL."""
    if _bot_state["db"]:
        days = request.args.get("days", 30, type=int)
        pnl = _bot_state["db"].get_daily_pnl(days)
        return jsonify(pnl)
    return jsonify([])


@app.route("/api/stats")
def api_stats():
    """Get trade statistics."""
    if _bot_state["db"]:
        stats = _bot_state["db"].get_trade_stats(50)
        return jsonify(stats)
    return jsonify({})


@app.route("/api/config")
def api_config():
    """Get current config summary."""
    cfg = _bot_state["config"]
    risk = cfg.get("risk", {})
    sl = cfg.get("stop_loss", {})
    tp = cfg.get("take_profit", {})
    return jsonify({
        "mode": cfg.get("mode", "paper"),
        "assets": cfg.get("assets", []),
        "risk_per_trade": risk.get("risk_per_trade", 0.015),
        "max_leverage": risk.get("max_leverage", 5),
        "max_positions": risk.get("max_concurrent_positions", 3),
        "stop_atr_mult": sl.get("atr_multiplier", 1.5),
        "tp1_ratio": tp.get("tp1_ratio", 1.5),
        "tp2_ratio": tp.get("tp2_ratio", 2.5),
        "daily_loss_halt": cfg.get("circuit_breakers", {}).get("daily_loss_halt", 0.03),
        "max_drawdown": cfg.get("circuit_breakers", {}).get("max_drawdown", 0.10),
    })


@app.route("/kill", methods=["POST"])
def kill_switch():
    """Emergency kill switch - flatten everything immediately."""
    logger.critical("KILL SWITCH ACTIVATED via dashboard")
    _bot_state["running"] = False
    if _bot_state["kill_callback"]:
        _bot_state["kill_callback"]()
    return jsonify({"status": "kill_switch_activated"})


def start_dashboard(host: str = "0.0.0.0", port: int = 8080):
    """Start dashboard in a background thread."""
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True
    )
    thread.start()
    logger.info(f"Dashboard started at http://{host}:{port}")
    return thread
