"""Historical data loading for backtesting."""

import os
import time
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Optional
from utils.logger import get_logger

logger = get_logger("backtest_data")

CACHE_DIR = Path("data/cache")


class BacktestDataLoader:
    """Load and prepare historical data for backtesting."""

    def __init__(self, exchange, config: dict):
        self.exchange = exchange
        self.config = config
        bt_cfg = config.get("backtest", {})
        self.start_date = bt_cfg.get("start_date", "2025-01-01")
        self.end_date = bt_cfg.get("end_date", "2025-03-25")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def load_data(self, asset: str, interval: str = "15m") -> pd.DataFrame:
        """Load historical candle data for an asset."""
        cache_file = CACHE_DIR / f"bt_{asset}_{interval}_{self.start_date}_{self.end_date}.parquet"

        if cache_file.exists():
            logger.info(f"Loading cached backtest data: {cache_file}")
            return pd.read_parquet(cache_file)

        logger.info(f"Fetching historical data for {asset} {interval}...")
        all_candles = []

        # Fetch in chunks (API may limit)
        interval_ms = self._interval_to_ms(interval)
        start_ts = int(pd.Timestamp(self.start_date).timestamp() * 1000)
        end_ts = int(pd.Timestamp(self.end_date).timestamp() * 1000)

        current = start_ts
        while current < end_ts:
            chunk_end = min(current + 500 * interval_ms, end_ts)
            try:
                candles = self.exchange.get_candles(asset, interval, limit=500)
                if candles:
                    # Filter to our date range
                    for c in candles:
                        if start_ts <= c["timestamp"] <= end_ts:
                            all_candles.append(c)
                    current = chunk_end
                else:
                    break
            except Exception as e:
                logger.error(f"Data fetch failed: {e}")
                break
            time.sleep(0.2)  # Rate limiting

        if not all_candles:
            logger.warning(f"No historical data for {asset}")
            return pd.DataFrame()

        df = pd.DataFrame(all_candles)
        df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)

        # Cache
        df.to_parquet(cache_file, index=False)
        logger.info(f"Loaded {len(df)} candles for {asset} {interval}")
        return df

    def load_all_assets(self, assets: list, interval: str = "15m") -> Dict[str, pd.DataFrame]:
        """Load data for all assets."""
        data = {}
        for asset in assets:
            data[asset] = self.load_data(asset, interval)
        return data

    def _interval_to_ms(self, interval: str) -> int:
        multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
        unit = interval[-1]
        value = int(interval[:-1])
        return value * multipliers.get(unit, 60_000)
