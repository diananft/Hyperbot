"""Hyperliquid exchange connection using the official SDK for signing."""

import os
import time
import json
import threading
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone

import requests

from utils.logger import get_logger

logger = get_logger("exchange")

# Hyperliquid API endpoints
MAINNET_API = "https://api.hyperliquid.xyz"
TESTNET_API = "https://api.hyperliquid-testnet.xyz"
MAINNET_WS = "wss://api.hyperliquid.xyz/ws"
TESTNET_WS = "wss://api.hyperliquid-testnet.xyz/ws"


class HyperliquidExchange:
    """Wrapper around Hyperliquid API using the official SDK for exchange operations."""

    def __init__(self, config: dict):
        self.api_key = os.getenv("HYPERLIQUID_API_KEY", "")
        self.api_secret = os.getenv("HYPERLIQUID_API_SECRET", "")
        self.wallet_address = os.getenv("HYPERLIQUID_WALLET_ADDRESS", "")
        self.is_mainnet = os.getenv("HYPERLIQUID_MAINNET", "true").lower() == "true"
        self.mode = config.get("mode", "paper")

        self.base_url = MAINNET_API if self.is_mainnet else TESTNET_API
        self.ws_url = MAINNET_WS if self.is_mainnet else TESTNET_WS

        # Rate limiting
        self._last_request_time = 0
        self._min_request_interval = 0.1
        self._lock = threading.Lock()

        # SDK exchange client (handles EIP-712 signing)
        self._sdk_exchange = None
        self._sdk_info = None
        self._init_sdk()

        # Asset metadata cache
        self._meta = None
        self._asset_map = {}

    def _init_sdk(self):
        """Initialize the hyperliquid-python-sdk clients."""
        try:
            from hyperliquid.info import Info
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants

            base_url = constants.MAINNET_API_URL if self.is_mainnet else constants.TESTNET_API_URL

            self._sdk_info = Info(base_url, skip_ws=True)

            if self.api_key:
                from eth_account import Account
                account = Account.from_key(self.api_key)
                self._sdk_exchange = Exchange(
                    account,
                    base_url,
                    account_address=self.wallet_address or None,
                )
                logger.info(f"SDK initialized: {'mainnet' if self.is_mainnet else 'testnet'}, "
                           f"mode={self.mode}, wallet={self.wallet_address[:10]}...")
            else:
                logger.warning("No API key - SDK exchange client not initialized (read-only mode)")

        except ImportError:
            logger.warning("hyperliquid-python-sdk not installed. "
                          "Install with: pip install hyperliquid-python-sdk. "
                          "Falling back to direct REST API (read-only, no trading).")
        except Exception as e:
            logger.error(f"SDK initialization failed: {e}")

    def _rate_limit(self):
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)
            self._last_request_time = time.time()

    def _request(self, endpoint: str, payload: dict, retries: int = 3) -> dict:
        """Make a POST request with retries and exponential backoff."""
        self._rate_limit()
        url = f"{self.base_url}{endpoint}"
        for attempt in range(retries):
            try:
                resp = requests.post(url, json=payload, timeout=10)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                wait = 2 ** attempt
                logger.warning(f"Request failed (attempt {attempt+1}/{retries}): {e}, "
                             f"retrying in {wait}s")
                if attempt < retries - 1:
                    time.sleep(wait)
                else:
                    logger.error(f"Request failed after {retries} attempts: {e}")
                    raise

    def _info_request(self, req_type: str, **kwargs) -> Any:
        """Make an info API request."""
        payload = {"type": req_type}
        payload.update(kwargs)
        return self._request("/info", payload)

    # ===== Market Data Methods =====

    def get_meta(self) -> dict:
        """Get exchange metadata (asset info, etc.)."""
        if not self._meta:
            self._meta = self._info_request("meta")
            if self._meta and "universe" in self._meta:
                self._asset_map = {
                    asset["name"]: i
                    for i, asset in enumerate(self._meta["universe"])
                }
        return self._meta

    def get_asset_index(self, asset: str) -> int:
        """Get the numeric index for an asset."""
        if not self._asset_map:
            self.get_meta()
        clean = asset.replace("-USD", "").replace("-PERP", "")
        if clean in self._asset_map:
            return self._asset_map[clean]
        raise ValueError(f"Unknown asset: {asset}")

    def get_orderbook(self, asset: str, depth: int = 20) -> dict:
        """Get L2 orderbook."""
        coin = asset.replace("-USD", "").replace("-PERP", "")
        result = self._info_request("l2Book", coin=coin)
        if result and "levels" in result:
            bids = result["levels"][0][:depth] if len(result["levels"]) > 0 else []
            asks = result["levels"][1][:depth] if len(result["levels"]) > 1 else []
            return {
                "bids": [{"price": float(b["px"]), "size": float(b["sz"])} for b in bids],
                "asks": [{"price": float(a["px"]), "size": float(a["sz"])} for a in asks],
                "timestamp": time.time()
            }
        return {"bids": [], "asks": [], "timestamp": time.time()}

    def get_candles(self, asset: str, interval: str = "15m",
                    limit: int = 500) -> List[dict]:
        """Get OHLCV candle data."""
        coin = asset.replace("-USD", "").replace("-PERP", "")
        interval_ms = {
            "1m": 60_000, "5m": 300_000, "15m": 900_000,
            "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000
        }.get(interval, 900_000)

        end_time = int(time.time() * 1000)
        start_time = end_time - (limit * interval_ms)

        result = self._request("/info", {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
            }
        })
        if result:
            candles = []
            for c in result:
                candles.append({
                    "timestamp": c["t"],
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": float(c["v"]),
                })
            return candles
        return []

    def get_all_mids(self) -> Dict[str, float]:
        """Get mid prices for all assets."""
        result = self._info_request("allMids")
        if result:
            return {k: float(v) for k, v in result.items()}
        return {}

    def get_funding_rates(self, asset: str = None) -> Any:
        """Get current funding rates."""
        try:
            result = self._info_request("metaAndAssetCtxs")
            if not result or not isinstance(result, list) or len(result) < 2:
                return {}
            meta = result[0]
            asset_ctxs = result[1]
            universe = meta.get("universe", [])
            rates = {}
            for i, info in enumerate(universe):
                name = info["name"]
                if asset and name != asset.replace("-USD", "").replace("-PERP", ""):
                    continue
                ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
                rates[name] = {
                    "funding_rate": float(ctx.get("funding", 0)),
                    "premium": float(ctx.get("premium", 0)),
                }
            return rates
        except Exception as e:
            logger.warning(f"Failed to get funding rates: {e}")
            return {}

    # ===== Account Methods =====

    def get_account_state(self) -> dict:
        """Get full account state including positions."""
        if not self.wallet_address:
            logger.warning("No wallet address configured")
            return {}
        try:
            result = self._info_request("clearinghouseState", user=self.wallet_address)
            return result or {}
        except Exception as e:
            logger.error(f"Failed to get account state: {e}")
            return {}

    def get_spot_balance(self) -> float:
        """Get spot USDC balance."""
        try:
            result = self._info_request("spotClearinghouseState", user=self.wallet_address)
            if result and "balances" in result:
                for bal in result["balances"]:
                    if bal.get("coin") == "USDC":
                        return float(bal.get("total", 0))
        except Exception as e:
            logger.warning(f"Failed to get spot balance: {e}")
        return 0.0

    def get_positions(self) -> List[dict]:
        """Get current open positions."""
        state = self.get_account_state()
        if not state:
            return []
        positions = []
        for pos in state.get("assetPositions", []):
            p = pos.get("position", {})
            if float(p.get("szi", 0)) != 0:
                positions.append({
                    "asset": p.get("coin", ""),
                    "size": float(p.get("szi", 0)),
                    "entry_price": float(p.get("entryPx", 0)),
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
                    "margin_used": float(p.get("marginUsed", 0)),
                    "leverage": float(p.get("leverage", {}).get("value", 1)),
                    "liquidation_price": float(p.get("liquidationPx", 0) or 0),
                })
        return positions

    def get_equity(self) -> float:
        """Get account equity (perps, then spot fallback)."""
        state = self.get_account_state()
        if not state:
            logger.warning("Empty account state returned")
            return 0.0

        # Try perps account value
        for key in ["crossMarginSummary", "marginSummary"]:
            if key in state and isinstance(state[key], dict):
                try:
                    val = float(state[key].get("accountValue", "0"))
                    if val > 0:
                        return val
                except (ValueError, TypeError):
                    continue

        # Try withdrawable
        try:
            val = float(state.get("withdrawable", "0"))
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass

        # Fallback: check spot balance
        spot = self.get_spot_balance()
        if spot > 0:
            return spot

        logger.warning("No equity found in perps or spot")
        return 0.0

    def get_open_orders(self) -> List[dict]:
        """Get all open orders."""
        if not self.wallet_address:
            return []
        result = self._info_request("openOrders", user=self.wallet_address)
        if result:
            return [{
                "order_id": o.get("oid", ""),
                "asset": o.get("coin", ""),
                "side": "buy" if o.get("side", "").lower() == "b" else "sell",
                "price": float(o.get("limitPx", 0)),
                "size": float(o.get("sz", 0)),
                "order_type": o.get("orderType", ""),
                "timestamp": o.get("timestamp", 0),
            } for o in result]
        return []

    # ===== Trading Methods (using SDK for proper EIP-712 signing) =====

    def place_order(self, asset: str, is_buy: bool, size: float,
                    price: float = None, order_type: str = "limit",
                    reduce_only: bool = False,
                    tp_price: float = None, sl_price: float = None) -> dict:
        """Place an order on Hyperliquid."""
        if self.mode == "paper":
            logger.info(f"PAPER ORDER: {'BUY' if is_buy else 'SELL'} {size} {asset} "
                       f"@ {price or 'MARKET'}")
            return {
                "status": "filled",
                "order_id": f"paper_{int(time.time()*1000)}",
                "filled_price": price or 0,
                "filled_size": size,
                "paper": True,
            }

        if not self._sdk_exchange:
            return {"status": "error", "error": "SDK exchange not initialized"}

        coin = asset.replace("-USD", "").replace("-PERP", "")

        try:
            if order_type == "market":
                # For market orders, use aggressive IOC limit
                mid = self.get_all_mids().get(coin, 0)
                if mid:
                    slippage = 0.005  # 0.5%
                    price = mid * (1 + slippage) if is_buy else mid * (1 - slippage)
                    # Round price to appropriate precision
                    price = self._round_price(price, coin)

                result = self._sdk_exchange.order(
                    coin, is_buy, size, price,
                    {"limit": {"tif": "Ioc"}},
                    reduce_only=reduce_only,
                )
            else:
                # Limit order (GTC)
                price = self._round_price(price, coin)
                result = self._sdk_exchange.order(
                    coin, is_buy, size, price,
                    {"limit": {"tif": "Gtc"}},
                    reduce_only=reduce_only,
                )

            logger.info(f"Order placed: {'BUY' if is_buy else 'SELL'} {size} {coin} "
                       f"@ {price}, result={result}")

            # Parse SDK response
            status = result.get("status", "")
            if status == "ok":
                response = result.get("response", {})
                if response.get("type") == "order":
                    statuses = response.get("data", {}).get("statuses", [])
                    if statuses:
                        s = statuses[0]
                        if "filled" in s:
                            return {
                                "status": "filled",
                                "order_id": s["filled"].get("oid", ""),
                                "filled_price": float(s["filled"].get("avgPx", price)),
                                "filled_size": float(s["filled"].get("totalSz", size)),
                            }
                        elif "resting" in s:
                            return {
                                "status": "resting",
                                "order_id": s["resting"].get("oid", ""),
                                "filled_price": 0,
                                "filled_size": 0,
                            }
                        elif "error" in s:
                            return {"status": "error", "error": s["error"]}
            return {"status": status or "unknown", "raw": result}

        except Exception as e:
            logger.error(f"Order failed: {e}")
            return {"status": "error", "error": str(e)}

    def cancel_order(self, asset: str, order_id: int) -> dict:
        """Cancel an open order."""
        if self.mode == "paper":
            return {"status": "cancelled", "paper": True}

        if not self._sdk_exchange:
            return {"status": "error", "error": "SDK exchange not initialized"}

        coin = asset.replace("-USD", "").replace("-PERP", "")
        try:
            result = self._sdk_exchange.cancel(coin, order_id)
            logger.info(f"Order cancelled: {coin} oid={order_id}")
            return result
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return {"status": "error", "error": str(e)}

    def cancel_all_orders(self) -> List[dict]:
        """Cancel all open orders."""
        orders = self.get_open_orders()
        results = []
        for order in orders:
            r = self.cancel_order(order["asset"], order["order_id"])
            results.append(r)
        return results

    def set_leverage(self, asset: str, leverage: int, is_cross: bool = True) -> dict:
        """Set leverage for an asset."""
        if self.mode == "paper":
            return {"status": "ok", "paper": True}

        if not self._sdk_exchange:
            logger.warning(f"SDK not available, skipping leverage set for {asset}")
            return {"status": "skipped", "error": "SDK not initialized"}

        try:
            result = self._sdk_exchange.update_leverage(
                leverage, asset.replace("-USD", "").replace("-PERP", ""),
                is_cross=is_cross
            )
            logger.info(f"Leverage set: {asset} -> {leverage}x {'cross' if is_cross else 'isolated'}")
            return result
        except Exception as e:
            logger.error(f"Set leverage failed: {e}")
            return {"status": "error", "error": str(e)}

    def is_connected(self) -> bool:
        """Check if we can reach the API."""
        try:
            result = self._info_request("meta")
            return result is not None and "universe" in result
        except Exception:
            return False

    def get_user_fills(self, limit: int = 100) -> List[dict]:
        """Get recent fills/trades for the account."""
        if not self.wallet_address:
            return []
        result = self._info_request("userFills", user=self.wallet_address)
        if result:
            return result[:limit]
        return []

    def _round_price(self, price: float, coin: str) -> float:
        """Round price to appropriate precision for the asset."""
        if price >= 10000:
            return round(price, 1)
        elif price >= 100:
            return round(price, 2)
        elif price >= 1:
            return round(price, 3)
        elif price >= 0.01:
            return round(price, 5)
        else:
            return round(price, 6)
