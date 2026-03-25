"""Order execution management with limit/market logic and retries."""

import time
import threading
from typing import Dict, List, Optional, Tuple
from utils.logger import get_logger
from utils.database import Database

logger = get_logger("orders")


class OrderManager:
    """Manages order placement, fills, cancellation, and execution logic."""

    def __init__(self, exchange, config: dict, db: Database):
        self.exchange = exchange
        self.db = db
        exec_cfg = config.get("execution", {})
        self.ticks_inside = exec_cfg.get("ticks_inside_spread", 2)
        self.limit_timeout_replace = exec_cfg.get("limit_timeout_replace", 30)
        self.limit_timeout_market = exec_cfg.get("limit_timeout_market", 60)
        self.exit_limit_timeout = exec_cfg.get("exit_limit_timeout", 45)
        self.split_threshold = exec_cfg.get("split_threshold", 500)
        self.split_count = exec_cfg.get("split_count", 3)
        self.split_delay = exec_cfg.get("split_delay", 7)
        self.max_retries = exec_cfg.get("max_retries", 3)

        self._active_orders: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def execute_entry(self, asset: str, is_long: bool, quantity: float,
                      price: float, score: float,
                      stop_loss: float = None,
                      take_profit: float = None) -> Dict:
        """Execute an entry order with limit-then-market logic.

        For positions > split_threshold USD, splits into sub-orders.
        """
        position_usd = quantity * price

        if position_usd > self.split_threshold:
            return self._scaled_entry(asset, is_long, quantity, price, score,
                                       stop_loss, take_profit)
        else:
            return self._single_entry(asset, is_long, quantity, price, score,
                                       stop_loss, take_profit)

    def _single_entry(self, asset: str, is_long: bool, quantity: float,
                      price: float, score: float,
                      stop_loss: float, take_profit: float) -> Dict:
        """Place a single entry order."""
        # Get orderbook for pricing
        try:
            ob = self.exchange.get_orderbook(asset)
            if ob["bids"] and ob["asks"]:
                if is_long:
                    # Place 1-2 ticks inside spread from ask
                    spread = ob["asks"][0]["price"] - ob["bids"][0]["price"]
                    tick = spread / 10 if spread > 0 else price * 0.0001
                    limit_price = ob["asks"][0]["price"] - tick * self.ticks_inside
                else:
                    spread = ob["asks"][0]["price"] - ob["bids"][0]["price"]
                    tick = spread / 10 if spread > 0 else price * 0.0001
                    limit_price = ob["bids"][0]["price"] + tick * self.ticks_inside
            else:
                limit_price = price
        except Exception:
            limit_price = price

        # Place limit order
        result = self._place_with_retry(asset, is_long, quantity, limit_price, "limit")

        if result.get("status") != "filled":
            # Wait for fill
            filled = self._wait_for_fill(asset, result.get("order_id"), self.limit_timeout_replace)

            if not filled:
                # Replace at best bid/ask
                self._cancel_order_safe(asset, result.get("order_id"))
                try:
                    ob = self.exchange.get_orderbook(asset)
                    new_price = ob["asks"][0]["price"] if is_long else ob["bids"][0]["price"]
                except Exception:
                    new_price = price

                result = self._place_with_retry(asset, is_long, quantity, new_price, "limit")
                filled = self._wait_for_fill(asset, result.get("order_id"),
                                              self.limit_timeout_market - self.limit_timeout_replace)

                if not filled and abs(score) > 0.8:
                    # Only go market for strong signals
                    self._cancel_order_safe(asset, result.get("order_id"))
                    result = self._place_with_retry(asset, is_long, quantity, None, "market")

        # Log to database
        self.db.log_order(
            order_id=str(result.get("order_id", "")),
            asset=asset,
            side="buy" if is_long else "sell",
            order_type="entry",
            price=limit_price,
            quantity=quantity,
            status=result.get("status", "unknown"),
            filled_price=result.get("filled_price"),
            filled_quantity=result.get("filled_size"),
            error=result.get("error")
        )

        return result

    def _scaled_entry(self, asset: str, is_long: bool, total_quantity: float,
                      price: float, score: float,
                      stop_loss: float, take_profit: float) -> Dict:
        """Split entry into sub-orders."""
        sub_qty = total_quantity / self.split_count
        results = []

        for i in range(self.split_count):
            # Stagger prices: entry, entry-0.1%, entry-0.2% for longs
            offset = i * 0.001
            if is_long:
                sub_price = price * (1 - offset)
            else:
                sub_price = price * (1 + offset)

            result = self._single_entry(asset, is_long, sub_qty, sub_price,
                                        score, stop_loss, take_profit)
            results.append(result)

            if i < self.split_count - 1:
                time.sleep(self.split_delay)

        # Aggregate results
        total_filled = sum(r.get("filled_size", 0) for r in results)
        avg_price = (sum(r.get("filled_price", 0) * r.get("filled_size", 0)
                        for r in results) / total_filled) if total_filled > 0 else 0

        return {
            "status": "filled" if total_filled > 0 else "failed",
            "filled_size": total_filled,
            "filled_price": avg_price,
            "sub_orders": results,
        }

    def execute_exit(self, asset: str, is_long: bool, quantity: float,
                     price: float = None, reason: str = "signal") -> Dict:
        """Execute an exit order. Limit with timeout, then market."""
        if price is None:
            try:
                ob = self.exchange.get_orderbook(asset)
                if is_long:
                    price = ob["bids"][0]["price"] if ob["bids"] else 0
                else:
                    price = ob["asks"][0]["price"] if ob["asks"] else 0
            except Exception:
                price = 0

        # Try limit exit first
        is_buy = not is_long  # Exit a long = sell, exit a short = buy
        result = self._place_with_retry(asset, is_buy, quantity, price, "limit",
                                        reduce_only=True)

        if result.get("status") != "filled":
            filled = self._wait_for_fill(asset, result.get("order_id"),
                                          self.exit_limit_timeout)
            if not filled:
                # Go market
                self._cancel_order_safe(asset, result.get("order_id"))
                result = self._place_with_retry(asset, is_buy, quantity, None,
                                                "market", reduce_only=True)

        self.db.log_order(
            order_id=str(result.get("order_id", "")),
            asset=asset,
            side="buy" if is_buy else "sell",
            order_type=f"exit_{reason}",
            price=price,
            quantity=quantity,
            status=result.get("status", "unknown"),
            filled_price=result.get("filled_price"),
            filled_quantity=result.get("filled_size"),
        )

        return result

    def _place_with_retry(self, asset: str, is_buy: bool, quantity: float,
                          price: float, order_type: str,
                          reduce_only: bool = False) -> Dict:
        """Place order with retries on network errors."""
        for attempt in range(self.max_retries):
            try:
                result = self.exchange.place_order(
                    asset=asset,
                    is_buy=is_buy,
                    size=quantity,
                    price=price,
                    order_type=order_type,
                    reduce_only=reduce_only,
                )
                return result
            except Exception as e:
                logger.error(f"Order attempt {attempt+1}/{self.max_retries} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
        return {"status": "failed", "error": "Max retries exceeded"}

    def _wait_for_fill(self, asset: str, order_id, timeout: int) -> bool:
        """Wait for an order to fill within timeout."""
        if not order_id:
            return False
        start = time.time()
        while time.time() - start < timeout:
            try:
                orders = self.exchange.get_open_orders()
                order_ids = [o["order_id"] for o in orders]
                if order_id not in order_ids:
                    return True  # Filled or cancelled
            except Exception:
                pass
            time.sleep(2)
        return False

    def _cancel_order_safe(self, asset: str, order_id):
        """Cancel an order, ignoring errors."""
        if not order_id:
            return
        try:
            self.exchange.cancel_order(asset, order_id)
        except Exception as e:
            logger.warning(f"Failed to cancel order {order_id}: {e}")

    def cancel_all(self) -> int:
        """Cancel all open orders. Returns count cancelled."""
        try:
            results = self.exchange.cancel_all_orders()
            count = len(results)
            logger.info(f"Cancelled {count} orders")
            return count
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return 0

    def reconcile_positions(self) -> List[Dict]:
        """Fetch positions from exchange and reconcile with local state."""
        try:
            positions = self.exchange.get_positions()
            return positions
        except Exception as e:
            logger.error(f"Failed to reconcile positions: {e}")
            return []

    def cancel_stale_orders(self, max_age_seconds: int = 120):
        """Cancel orders older than max_age."""
        try:
            orders = self.exchange.get_open_orders()
            now = time.time() * 1000
            for order in orders:
                age = now - order.get("timestamp", now)
                if age > max_age_seconds * 1000:
                    self._cancel_order_safe(order["asset"], order["order_id"])
                    logger.info(f"Cancelled stale order {order['order_id']} "
                              f"({age/1000:.0f}s old)")
        except Exception as e:
            logger.error(f"Stale order cleanup failed: {e}")
