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
    "running": True,
    "mode": "paper",
    "start_time": None,
    "kill_callback": None,
}


def init_dashboard(db, position_manager, portfolio_manager,
                   data_feed, exchange, mode, kill_callback=None):
    """Initialize dashboard with bot references."""
    _bot_state["db"] = db
    _bot_state["position_manager"] = position_manager
    _bot_state["portfolio_manager"] = portfolio_manager
    _bot_state["data_feed"] = data_feed
    _bot_state["exchange"] = exchange
    _bot_state["mode"] = mode
    _bot_state["start_time"] = datetime.now(timezone.utc).isoformat()
    _bot_state["kill_callback"] = kill_callback


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
    if _bot_state["position_manager"]:
        for pos in _bot_state["position_manager"].get_all_positions():
            positions.append({
                "asset": pos.asset,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "quantity": pos.remaining_quantity,
                "pnl": pos.pnl,
                "stop_loss": pos.stop_loss,
                "candles_held": pos.candles_held,
                "duration_min": pos.duration_minutes(),
            })

    portfolio = {}
    if _bot_state["portfolio_manager"]:
        portfolio = _bot_state["portfolio_manager"].update(equity)

    return jsonify({
        "status": "running" if _bot_state["running"] else "stopped",
        "mode": _bot_state["mode"],
        "equity": equity,
        "positions": positions,
        "portfolio": portfolio,
        "start_time": _bot_state["start_time"],
        "ws_connected": _bot_state["data_feed"].ws_connected if _bot_state["data_feed"] else False,
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


@app.route("/api/signals")
def api_signals():
    """Get current signal scores."""
    if _bot_state["data_feed"]:
        prices = _bot_state["data_feed"].mid_prices
        return jsonify(prices)
    return jsonify({})


@app.route("/kill", methods=["POST"])
def kill_switch():
    """Emergency kill switch - flatten everything immediately."""
    logger.critical("KILL SWITCH ACTIVATED via dashboard")
    _bot_state["running"] = False
    if _bot_state["kill_callback"]:
        _bot_state["kill_callback"]()
    return jsonify({"status": "kill_switch_activated", "message": "All positions being closed"})


def start_dashboard(host: str = "0.0.0.0", port: int = 8080):
    """Start dashboard in a background thread."""
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True
    )
    thread.start()
    logger.info(f"Dashboard started at http://{host}:{port}")
    return thread
