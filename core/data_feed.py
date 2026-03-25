"""Real-time and historical data pipeline."""

import time
import json
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable
from pathlib import Path

import pandas as pd
import numpy as np
import websockets
import asyncio

from core.exchange import HyperliquidExchange
from utils.logger import get_logger

logger = get_logger("data_feed")

CACHE_DIR = Path("data/cache")


class DataFeed:
    """Manages real-time and historical market data."""

    def __init__(self, exchange: HyperliquidExchange, config: dict):
        self.exchange = exchange
        self.config = config
        self.assets = config.get("assets", ["BTC", "ETH"])
        self.timeframes = config.get("timeframes", {
            "higher": "4h", "execution": "15m", "trigger": "1m"
        })

        # In-memory candle DataFrames: {asset: {timeframe: DataFrame}}
        self.candles: Dict[str, Dict[str, pd.DataFrame]] = {}
        # Orderbook snapshots: {asset: dict}
        self.orderbooks: Dict[str, dict] = {}
        # Latest mid prices
        self.mid_prices: Dict[str, float] = {}

        # WebSocket state
        self._ws = None
        self._ws_thread = None
        self._ws_connected = False
        self._ws_running = False
        self._callbacks: List[Callable] = []

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def initialize(self):
        """Load historical data for all assets and timeframes."""
        logger.info("Initializing data feed...")
        for asset in self.assets:
            self.candles[asset] = {}
            for tf_name, tf_val in self.timeframes.items():
                df = self._load_candles(asset, tf_val)
                self.candles[asset][tf_val] = df
                logger.info(f"Loaded {len(df)} candles for {asset} {tf_val}")
            # Initial orderbook
            try:
                self.orderbooks[asset] = self.exchange.get_orderbook(asset)
            except Exception as e:
                logger.warning(f"Failed to get orderbook for {asset}: {e}")
                self.orderbooks[asset] = {"bids": [], "asks": [], "timestamp": 0}

        # Load mid prices
        try:
            self.mid_prices = self.exchange.get_all_mids()
        except Exception as e:
            logger.warning(f"Failed to get mid prices: {e}")

        logger.info("Data feed initialized")

    def _load_candles(self, asset: str, interval: str,
                      limit: int = 500) -> pd.DataFrame:
        """Load candles from cache or exchange."""
        cache_file = CACHE_DIR / f"{asset}_{interval}.parquet"

        # Try cache first
        if cache_file.exists():
            try:
                cached = pd.read_parquet(cache_file)
                # Check if cache is recent enough
                if len(cached) > 0:
                    last_ts = cached["timestamp"].iloc[-1]
                    interval_ms = self._interval_to_ms(interval)
                    if time.time() * 1000 - last_ts < interval_ms * 2:
                        logger.info(f"Using cached data for {asset} {interval}")
                        return cached
            except Exception as e:
                logger.warning(f"Cache read failed for {asset} {interval}: {e}")

        # Fetch from exchange
        try:
            raw = self.exchange.get_candles(asset, interval, limit)
            if not raw:
                logger.warning(f"No candles returned for {asset} {interval}")
                return pd.DataFrame(columns=["timestamp", "open", "high",
                                             "low", "close", "volume"])
            df = pd.DataFrame(raw)
            df = df.sort_values("timestamp").reset_index(drop=True)
            # Cache to parquet
            try:
                df.to_parquet(cache_file, index=False)
            except Exception as e:
                logger.warning(f"Cache write failed: {e}")
            return df
        except Exception as e:
            logger.error(f"Failed to fetch candles for {asset} {interval}: {e}")
            return pd.DataFrame(columns=["timestamp", "open", "high",
                                         "low", "close", "volume"])

    def _interval_to_ms(self, interval: str) -> int:
        multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
        unit = interval[-1]
        value = int(interval[:-1])
        return value * multipliers.get(unit, 60_000)

    def update_candles(self, asset: str, interval: str,
                       new_candle: dict = None) -> pd.DataFrame:
        """Update candle data - fetch latest or append new candle."""
        if asset not in self.candles:
            self.candles[asset] = {}

        if new_candle:
            df = self.candles[asset].get(interval, pd.DataFrame())
            new_row = pd.DataFrame([new_candle])
            if len(df) > 0 and df["timestamp"].iloc[-1] == new_candle["timestamp"]:
                # Update existing candle
                df.iloc[-1] = new_row.iloc[0]
            else:
                df = pd.concat([df, new_row], ignore_index=True)
            # Keep rolling window
            max_candles = 1000
            if len(df) > max_candles:
                df = df.iloc[-max_candles:].reset_index(drop=True)
            self.candles[asset][interval] = df
            return df
        else:
            # Fetch latest
            try:
                raw = self.exchange.get_candles(asset, interval, limit=5)
                if raw:
                    df = self.candles[asset].get(interval, pd.DataFrame())
                    for candle in raw:
                        if len(df) > 0 and df["timestamp"].iloc[-1] == candle["timestamp"]:
                            df.iloc[-1] = pd.Series(candle)
                        elif len(df) == 0 or candle["timestamp"] > df["timestamp"].iloc[-1]:
                            df = pd.concat([df, pd.DataFrame([candle])],
                                         ignore_index=True)
                    max_candles = 1000
                    if len(df) > max_candles:
                        df = df.iloc[-max_candles:].reset_index(drop=True)
                    self.candles[asset][interval] = df
                    return df
            except Exception as e:
                logger.error(f"Failed to update candles: {e}")
            return self.candles[asset].get(interval, pd.DataFrame())

    def update_orderbook(self, asset: str) -> dict:
        """Refresh orderbook for an asset."""
        try:
            ob = self.exchange.get_orderbook(asset)
            self.orderbooks[asset] = ob
            return ob
        except Exception as e:
            logger.error(f"Failed to update orderbook for {asset}: {e}")
            return self.orderbooks.get(asset, {"bids": [], "asks": [], "timestamp": 0})

    def update_all_prices(self):
        """Update mid prices for all assets."""
        try:
            self.mid_prices = self.exchange.get_all_mids()
        except Exception as e:
            logger.warning(f"Failed to update mid prices: {e}")

    def get_candle_df(self, asset: str, interval: str) -> pd.DataFrame:
        """Get candle DataFrame for an asset/timeframe."""
        return self.candles.get(asset, {}).get(interval, pd.DataFrame())

    def get_price(self, asset: str) -> float:
        """Get latest price for an asset."""
        clean = asset.replace("-USD", "").replace("-PERP", "")
        if clean in self.mid_prices:
            return self.mid_prices[clean]
        # Fallback to last candle close
        for tf in self.timeframes.values():
            df = self.get_candle_df(asset, tf)
            if len(df) > 0:
                return df["close"].iloc[-1]
        return 0.0

    # ===== WebSocket =====

    def start_websocket(self):
        """Start WebSocket connection in background thread."""
        if self._ws_running:
            return
        self._ws_running = True
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()
        logger.info("WebSocket thread started")

    def stop_websocket(self):
        """Stop WebSocket connection."""
        self._ws_running = False
        self._ws_connected = False

    def _ws_loop(self):
        """WebSocket event loop with auto-reconnect."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self._ws_running:
            try:
                loop.run_until_complete(self._ws_connect())
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            if self._ws_running:
                logger.info("WebSocket reconnecting in 5s...")
                time.sleep(5)

    async def _ws_connect(self):
        """Connect to WebSocket and subscribe."""
        try:
            async with websockets.connect(self.exchange.ws_url) as ws:
                self._ws = ws
                self._ws_connected = True
                logger.info("WebSocket connected")

                # Subscribe to trades and L2 for each asset
                for asset in self.assets:
                    coin = asset.replace("-USD", "").replace("-PERP", "")
                    # Subscribe to L2 orderbook
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "l2Book", "coin": coin}
                    }))
                    # Subscribe to trades
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "trades", "coin": coin}
                    }))

                async for message in ws:
                    if not self._ws_running:
                        break
                    try:
                        data = json.loads(message)
                        self._process_ws_message(data)
                    except json.JSONDecodeError:
                        continue

        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self._ws_connected = False

    def _process_ws_message(self, data: dict):
        """Process incoming WebSocket message."""
        channel = data.get("channel", "")
        msg_data = data.get("data", {})

        if channel == "l2Book":
            coin = msg_data.get("coin", "")
            if coin and "levels" in msg_data:
                levels = msg_data["levels"]
                self.orderbooks[coin] = {
                    "bids": [{"price": float(b["px"]), "size": float(b["sz"])}
                            for b in (levels[0] if levels else [])[:20]],
                    "asks": [{"price": float(a["px"]), "size": float(a["sz"])}
                            for a in (levels[1] if len(levels) > 1 else [])[:20]],
                    "timestamp": time.time()
                }
        elif channel == "trades":
            if isinstance(msg_data, list):
                for trade in msg_data:
                    coin = trade.get("coin", "")
                    if coin:
                        self.mid_prices[coin] = float(trade.get("px", 0))

        # Fire callbacks
        for cb in self._callbacks:
            try:
                cb(channel, msg_data)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def on_update(self, callback: Callable):
        """Register a callback for WebSocket updates."""
        self._callbacks.append(callback)

    @property
    def ws_connected(self) -> bool:
        return self._ws_connected

    def save_cache(self):
        """Save all candle data to parquet cache."""
        for asset in self.candles:
            for tf, df in self.candles[asset].items():
                if len(df) > 0:
                    cache_file = CACHE_DIR / f"{asset}_{tf}.parquet"
                    try:
                        df.to_parquet(cache_file, index=False)
                    except Exception as e:
                        logger.warning(f"Cache save failed for {asset} {tf}: {e}")
