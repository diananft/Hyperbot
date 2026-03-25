"""Walk-forward optimization and parameter sensitivity analysis."""

import itertools
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from copy import deepcopy

from backtest.backtester import Backtester
from utils.logger import get_logger

logger = get_logger("optimizer")


class WalkForwardOptimizer:
    """Walk-forward optimization with overfit detection."""

    def __init__(self, config: dict):
        self.config = config
        wf = config.get("backtest", {}).get("walk_forward", {})
        self.train_days = wf.get("train_days", 60)
        self.test_days = wf.get("test_days", 30)
        self.roll_days = wf.get("roll_days", 30)
        self.target_metric = wf.get("target_metric", "sharpe")
        self.overfit_threshold = wf.get("overfit_threshold", 2.0)

    def optimize(self, data: Dict[str, pd.DataFrame]) -> Dict:
        """Run walk-forward optimization.

        Split data into rolling train/test windows.
        Optimize parameters on train, validate on test.
        """
        logger.info("Starting walk-forward optimization...")
        results = []

        # Parameter space to optimize
        param_grid = self._get_param_grid()

        # For each asset, determine time windows
        for asset, df in data.items():
            if len(df) < 100:
                continue

            # Time-based splitting
            timestamps = df["timestamp"].values
            total_duration = timestamps[-1] - timestamps[0]
            candles_per_day = len(df) / (total_duration / 86400000) if total_duration > 0 else 96

            train_candles = int(self.train_days * candles_per_day)
            test_candles = int(self.test_days * candles_per_day)
            roll_candles = int(self.roll_days * candles_per_day)

            start = 0
            window_num = 0

            while start + train_candles + test_candles <= len(df):
                train_df = df.iloc[start:start + train_candles]
                test_df = df.iloc[start + train_candles:start + train_candles + test_candles]

                # Find best params on training data
                best_params, best_train_metric = self._optimize_window(
                    {asset: train_df}, param_grid
                )

                # Validate on test data
                test_config = self._apply_params(best_params)
                bt = Backtester(test_config)
                test_result = bt.run({asset: test_df})
                test_metric = test_result["metrics"].get(f"{self.target_metric}_ratio",
                              test_result["metrics"].get(self.target_metric, 0))

                # Check for overfitting
                overfit = (best_train_metric > test_metric * self.overfit_threshold
                          if test_metric > 0 else False)

                results.append({
                    "window": window_num,
                    "asset": asset,
                    "train_metric": best_train_metric,
                    "test_metric": test_metric,
                    "best_params": best_params,
                    "overfit": overfit,
                    "test_trades": test_result["metrics"]["total_trades"],
                })

                logger.info(f"Window {window_num}: train={best_train_metric:.2f}, "
                          f"test={test_metric:.2f}, overfit={overfit}")

                start += roll_candles
                window_num += 1

        # Aggregate results
        if results:
            avg_test = np.mean([r["test_metric"] for r in results])
            overfit_rate = sum(1 for r in results if r["overfit"]) / len(results)
            logger.info(f"Optimization complete: {len(results)} windows, "
                       f"avg test {self.target_metric}={avg_test:.2f}, "
                       f"overfit rate={overfit_rate:.0%}")
        else:
            avg_test = 0
            overfit_rate = 0

        return {
            "windows": results,
            "avg_test_metric": avg_test,
            "overfit_rate": overfit_rate,
        }

    def sensitivity_analysis(self, data: Dict[str, pd.DataFrame],
                             base_params: Dict = None) -> Dict:
        """Vary each parameter +-20% and measure impact on target metric."""
        logger.info("Running sensitivity analysis...")
        base = base_params or {}
        results = {}

        param_names = [
            "signal_weights.trend", "signal_weights.momentum",
            "signal_thresholds.strong_entry", "signal_thresholds.normal_entry",
            "stop_loss.atr_multiplier",
            "take_profit.tp1_ratio", "take_profit.tp2_ratio",
        ]

        for param in param_names:
            base_val = self._get_nested(self.config, param)
            if base_val is None or base_val == 0:
                continue

            variations = []
            for mult in [0.8, 0.9, 1.0, 1.1, 1.2]:
                test_config = deepcopy(self.config)
                new_val = base_val * mult
                self._set_nested(test_config, param, new_val)

                bt = Backtester(test_config)
                result = bt.run(data)
                metric = result["metrics"].get(f"{self.target_metric}_ratio",
                         result["metrics"].get(self.target_metric, 0))
                variations.append({"multiplier": mult, "value": new_val, "metric": metric})

            results[param] = variations
            logger.info(f"Sensitivity {param}: "
                       f"{[f'{v['multiplier']}x={v['metric']:.2f}' for v in variations]}")

        return results

    def _get_param_grid(self) -> List[Dict]:
        """Define parameter grid for optimization."""
        # Keep grid small to avoid overfitting
        grid = []
        for strong_entry in [0.7, 0.8, 0.9]:
            for atr_mult in [1.0, 1.5, 2.0]:
                for tp1_ratio in [1.0, 1.5, 2.0]:
                    grid.append({
                        "signal_thresholds.strong_entry": strong_entry,
                        "stop_loss.atr_multiplier": atr_mult,
                        "take_profit.tp1_ratio": tp1_ratio,
                    })
        return grid

    def _optimize_window(self, data: Dict[str, pd.DataFrame],
                         param_grid: List[Dict]) -> Tuple[Dict, float]:
        """Find best parameters on a training window."""
        best_metric = -float("inf")
        best_params = {}

        for params in param_grid:
            test_config = self._apply_params(params)
            bt = Backtester(test_config)
            result = bt.run(data)
            metric = result["metrics"].get(f"{self.target_metric}_ratio",
                     result["metrics"].get(self.target_metric, 0))

            if metric > best_metric:
                best_metric = metric
                best_params = params

        return best_params, best_metric

    def _apply_params(self, params: Dict) -> dict:
        """Apply parameter overrides to config."""
        config = deepcopy(self.config)
        for key, value in params.items():
            self._set_nested(config, key, value)
        return config

    def _get_nested(self, d: dict, key: str):
        """Get nested dict value by dot-separated key."""
        keys = key.split(".")
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k)
            else:
                return None
        return d

    def _set_nested(self, d: dict, key: str, value):
        """Set nested dict value by dot-separated key."""
        keys = key.split(".")
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
