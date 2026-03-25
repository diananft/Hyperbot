"""Notification system for Telegram and Discord."""

import os
import json
import asyncio
import requests
from datetime import datetime, timezone
from typing import Optional
from utils.logger import get_logger

logger = get_logger("notifier")


class Notifier:
    def __init__(self, config: dict):
        self.enabled = config.get("enabled", False)
        channels = config.get("channels", {})
        self.telegram_enabled = channels.get("telegram", False)
        self.discord_enabled = channels.get("discord", False)
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")

    def send(self, message: str, level: str = "info"):
        """Send notification to all configured channels."""
        if not self.enabled:
            return
        prefix = {"info": "ℹ️", "trade": "📊", "alert": "⚠️",
                  "critical": "🚨", "success": "✅"}.get(level, "")
        full_msg = f"{prefix} Hyperbot | {message}"
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            self._send_telegram(full_msg)
        if self.discord_enabled and self.discord_webhook:
            self._send_discord(full_msg)

    def _send_telegram(self, message: str):
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Telegram send failed: {resp.text}")
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    def _send_discord(self, message: str):
        try:
            resp = requests.post(self.discord_webhook, json={
                "content": message
            }, timeout=10)
            if resp.status_code not in (200, 204):
                logger.error(f"Discord send failed: {resp.text}")
        except Exception as e:
            logger.error(f"Discord error: {e}")

    def notify_trade(self, action: str, asset: str, side: str, price: float,
                     quantity: float, pnl: float = None, pnl_pct: float = None):
        msg = f"<b>{action.upper()}</b> {side.upper()} {asset}\n"
        msg += f"Price: ${price:,.2f} | Qty: {quantity:.4f}\n"
        if pnl is not None:
            msg += f"PnL: ${pnl:,.2f} ({pnl_pct:+.2%})"
        self.send(msg, level="trade")

    def notify_daily_summary(self, equity: float, daily_pnl: float,
                             daily_pnl_pct: float, trades: int,
                             win_rate: float, drawdown: float):
        msg = (f"<b>Daily Summary</b>\n"
               f"Equity: ${equity:,.2f}\n"
               f"Day PnL: ${daily_pnl:,.2f} ({daily_pnl_pct:+.2%})\n"
               f"Trades: {trades} | Win Rate: {win_rate:.0%}\n"
               f"Drawdown: {drawdown:.2%}")
        self.send(msg, level="info")

    def notify_risk_alert(self, alert_type: str, details: str):
        self.send(f"<b>RISK ALERT: {alert_type}</b>\n{details}", level="critical")

    def notify_system(self, event: str, details: str = ""):
        self.send(f"<b>System: {event}</b>\n{details}" if details else
                  f"<b>System: {event}</b>", level="info")
