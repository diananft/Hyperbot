"""Hyperliquid exchange connection with REST + WebSocket."""

import os
import time
import json
import threading
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone

import requests
from eth_account import Account

from utils.logger import get_logger

logger = get_logger("exchange")

# Hyperliquid API endpoints
MAINNET_API = "https://api.hyperliquid.xyz"
TESTNET_API = "https://api.hyperliquid-testnet.xyz"
MAINNET_WS = "wss://api.hyperliquid.xyz/ws"
TESTNET_WS = "wss://api.hyperliquid-testnet.xyz/ws"


class HyperliquidExchange:
    """Wrapper around Hyperliquid REST API with rate limiting and retries."""

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
        self._min_request_interval = 0.1  # 100ms between requests
        self._lock = threading.Lock()

        # Account for signing
        self._account = None
        if self.api_key:
            try:
                self._account = Account.from_key(self.api_key)
                logger.info(f"Exchange initialized: {'mainnet' if self.is_mainnet else 'testnet'}, "
                           f"mode={self.mode}, wallet={self.wallet_address[:10]}...")
            except Exception as e:
                logger.error(f"Failed to initialize account from key: {e}")

        # Asset metadata cache
        self._meta = None
        self._asset_map = {}

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

    def _exchange_request(self, action: dict) -> dict:
        """Make a signed exchange API request."""
        if not self._account:
            raise ValueError("No API key configured")

        # Build the action with nonce
        nonce = int(time.time() * 1000)
        action["nonce"] = nonce

        # For Hyperliquid, we need to sign the action
        # The exact signing mechanism depends on the SDK version
        payload = {
            "action": action,
            "nonce": nonce,
            "signature": self._sign_action(action, nonce),
            "vaultAddress": None,
        }
        return self._request("/exchange", payload)

    def _sign_action(self, action: dict, nonce: int) -> dict:
        """Sign an action for the exchange API.

        Note: This is a simplified signing flow. The hyperliquid-python-sdk
        handles the full EIP-712 signing. For production, use the SDK's
        signing utilities.
        """
        from eth_account.messages import encode_defunct
        # Hyperliquid uses a specific signing scheme
        # This creates a basic signature - the SDK handles the full flow
        msg = json.dumps(action, sort_keys=True)
        message = encode_defunct(text=msg)
        signed = self._account.sign_message(message)
        return {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v}

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
        # Try with and without -USD suffix
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
        """Get OHLCV candle data.

        interval: 1m, 5m, 15m, 1h, 4h, 1d
        """
        coin = asset.replace("-USD", "").replace("-PERP", "")
        # Convert interval to milliseconds for the API
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
            # metaAndAssetCtxs returns meta + per-asset context including funding
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
            logger.info(f"Account state response keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")
            return result or {}
        except Exception as e:
            logger.error(f"Failed to get account state: {e}")
            return {}

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
        """Get account equity."""
        state = self.get_account_state()
        if not state:
            logger.warning("Empty account state returned")
            return 0.0

        # Try marginSummary first (standard location)
        if "marginSummary" in state:
            val = float(state["marginSummary"].get("accountValue", 0))
            if val > 0:
                return val

        # Try crossMarginSummary (newer API format)
        if "crossMarginSummary" in state:
            val = float(state["crossMarginSummary"].get("accountValue", 0))
            if val > 0:
                return val

        # Try withdrawable as last resort
        if "withdrawable" in state:
            val = float(state["withdrawable"])
            if val > 0:
                return val

        logger.warning(f"Could not find equity in state. Available keys: {list(state.keys())}")
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

    # ===== Trading Methods =====

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

        coin = asset.replace("-USD", "").replace("-PERP", "")
        asset_idx = self.get_asset_index(asset)

        order = {
            "a": asset_idx,
            "b": is_buy,
            "p": str(price) if price else "0",
            "s": str(size),
            "r": reduce_only,
            "t": {"limit": {"tif": "Gtc"}} if order_type == "limit" else {"trigger": {"triggerPx": str(price), "isMarket": True, "tpsl": "sl"}},
        }

        if order_type == "market":
            # For market orders, use aggressive limit
            mid = self.get_all_mids().get(coin, 0)
            if mid:
                slippage = 0.005  # 0.5%
                order["p"] = str(mid * (1 + slippage) if is_buy else mid * (1 - slippage))
                order["t"] = {"limit": {"tif": "Ioc"}}

        action = {
            "type": "order",
            "orders": [order],
            "grouping": "na",
        }

        try:
            result = self._exchange_request(action)
            logger.info(f"Order placed: {'BUY' if is_buy else 'SELL'} {size} {coin} "
                       f"@ {price or 'MARKET'}, result={result}")
            return result
        except Exception as e:
            logger.error(f"Order failed: {e}")
            return {"status": "error", "error": str(e)}

    def cancel_order(self, asset: str, order_id: int) -> dict:
        """Cancel an open order."""
        if self.mode == "paper":
            return {"status": "cancelled", "paper": True}

        coin = asset.replace("-USD", "").replace("-PERP", "")
        asset_idx = self.get_asset_index(asset)
        action = {
            "type": "cancel",
            "cancels": [{"a": asset_idx, "o": order_id}],
        }
        try:
            result = self._exchange_request(action)
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

        asset_idx = self.get_asset_index(asset)
        action = {
            "type": "updateLeverage",
            "asset": asset_idx,
            "isCross": is_cross,
            "leverage": leverage,
        }
        try:
            return self._exchange_request(action)
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
