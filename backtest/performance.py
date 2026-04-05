"""Backtest performance metrics and reporting."""

import numpy as np
import pandas as pd
from typing import Dict, List
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    HAS_PLOT = True
except ImportError:
    HAS_PLOT = False

from utils.logger import get_logger

logger = get_logger("performance")


class PerformanceAnalyzer:
    """Calculate backtest performance metrics and generate reports."""

    def __init__(self, initial_capital: float = 10000):
        self.initial_capital = initial_capital

    def calculate_metrics(self, trades: List[Dict],
                          equity_curve: List[float]) -> Dict:
        """Calculate comprehensive performance metrics."""
        if not trades or not equity_curve:
            return self._empty_metrics()

        equity = np.array(equity_curve)
        pnls = [t.get("pnl", 0) for t in trades]
        pnl_pcts = [t.get("pnl_pct", 0) for t in trades]

        # Basic metrics
        total_return = (equity[-1] - self.initial_capital) / self.initial_capital
        total_trades = len(trades)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / total_trades if total_trades > 0 else 0

        # Drawdown
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        max_drawdown = drawdown.max()
        # Drawdown duration
        dd_start = None
        max_dd_duration = 0
        for i, dd in enumerate(drawdown):
            if dd > 0 and dd_start is None:
                dd_start = i
            elif dd == 0 and dd_start is not None:
                max_dd_duration = max(max_dd_duration, i - dd_start)
                dd_start = None

        # Returns for ratio calculations
        daily_returns = pd.Series(equity).pct_change().dropna()

        # Sharpe Ratio (annualized, assuming ~365 trading days for crypto)
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(365)
        else:
            sharpe = 0

        # Sortino Ratio
        downside = daily_returns[daily_returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino = daily_returns.mean() / downside.std() * np.sqrt(365)
        else:
            sortino = 0

        # Calmar Ratio
        if max_drawdown > 0:
            # Approximate CAGR
            n_days = len(equity_curve)
            cagr = (equity[-1] / self.initial_capital) ** (365 / max(n_days, 1)) - 1
            calmar = cagr / max_drawdown
        else:
            cagr = total_return
            calmar = 0

        # Profit Factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Expectancy
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # Consecutive wins/losses
        max_consec_wins = self._max_consecutive(pnls, positive=True)
        max_consec_losses = self._max_consecutive(pnls, positive=False)

        # Average trade duration
        durations = [t.get("duration_minutes", 0) for t in trades if t.get("duration_minutes")]
        avg_duration = np.mean(durations) if durations else 0

        return {
            "total_return": total_return,
            "total_return_pct": total_return * 100,
            "cagr": cagr,
            "max_drawdown": max_drawdown,
            "max_drawdown_pct": max_drawdown * 100,
            "max_dd_duration": max_dd_duration,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "win_rate_pct": win_rate * 100,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "avg_duration_minutes": avg_duration,
            "total_pnl": sum(pnls),
            "total_fees": sum(t.get("fees", 0) for t in trades),
            "final_equity": equity[-1],
        }

    def print_report(self, metrics: Dict):
        """Print a formatted performance report."""
        print("\n" + "=" * 60)
        print("BACKTEST PERFORMANCE REPORT")
        print("=" * 60)
        print(f"  Total Return:       {metrics['total_return_pct']:+.2f}%")
        print(f"  CAGR:               {metrics['cagr']*100:+.2f}%")
        print(f"  Final Equity:       ${metrics['final_equity']:,.2f}")
        print(f"  Total PnL:          ${metrics['total_pnl']:+,.2f}")
        print(f"  Total Fees:         ${metrics['total_fees']:,.2f}")
        print("-" * 60)
        print(f"  Max Drawdown:       {metrics['max_drawdown_pct']:.2f}%")
        print(f"  Sharpe Ratio:       {metrics['sharpe_ratio']:.2f}")
        print(f"  Sortino Ratio:      {metrics['sortino_ratio']:.2f}")
        print(f"  Calmar Ratio:       {metrics['calmar_ratio']:.2f}")
        print(f"  Profit Factor:      {metrics['profit_factor']:.2f}")
        print("-" * 60)
        print(f"  Total Trades:       {metrics['total_trades']}")
        print(f"  Win Rate:           {metrics['win_rate_pct']:.1f}%")
        print(f"  Avg Win:            ${metrics['avg_win']:+,.2f}")
        print(f"  Avg Loss:           ${metrics['avg_loss']:,.2f}")
        print(f"  Expectancy:         ${metrics['expectancy']:+,.2f}")
        print(f"  Max Consec Wins:    {metrics['max_consecutive_wins']}")
        print(f"  Max Consec Losses:  {metrics['max_consecutive_losses']}")
        print(f"  Avg Duration:       {metrics['avg_duration_minutes']:.0f} min")
        print("=" * 60)

    def save_charts(self, equity_curve: List[float], trades: List[Dict],
                    output_dir: str = "backtest_results"):
        """Save equity curve and monthly heatmap as PNG."""
        if not HAS_PLOT:
            logger.warning("matplotlib not available, skipping charts")
            return

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Equity curve
        fig, axes = plt.subplots(2, 1, figsize=(14, 10))

        axes[0].plot(equity_curve, linewidth=1.5, color="#2196F3")
        axes[0].axhline(y=self.initial_capital, color="gray", linestyle="--", alpha=0.5)
        axes[0].fill_between(range(len(equity_curve)), self.initial_capital,
                             equity_curve, alpha=0.1, color="#2196F3")
        axes[0].set_title("Equity Curve", fontsize=14)
        axes[0].set_ylabel("Equity ($)")
        axes[0].grid(True, alpha=0.3)

        # Drawdown
        equity = np.array(equity_curve)
        peak = np.maximum.accumulate(equity)
        dd = (peak - equity) / peak * 100
        axes[1].fill_between(range(len(dd)), 0, -dd, alpha=0.5, color="#F44336")
        axes[1].set_title("Drawdown", fontsize=14)
        axes[1].set_ylabel("Drawdown (%)")
        axes[1].grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{output_dir}/equity_curve.png", dpi=150)
        plt.close()

        # Monthly heatmap
        if trades:
            self._save_monthly_heatmap(trades, output_dir)

    def _save_monthly_heatmap(self, trades: List[Dict], output_dir: str):
        """Save monthly PnL heatmap."""
        if not HAS_PLOT:
            return

        # Group trades by month
        monthly_pnl = {}
        for t in trades:
            ts = t.get("timestamp", t.get("exit_time", 0))
            if isinstance(ts, (int, float)) and ts > 0:
                month = pd.Timestamp(ts, unit="ms" if ts > 1e12 else "s").strftime("%Y-%m")
            else:
                month = "unknown"
            monthly_pnl[month] = monthly_pnl.get(month, 0) + t.get("pnl", 0)

        if not monthly_pnl or all(k == "unknown" for k in monthly_pnl):
            return

        months = sorted(k for k in monthly_pnl.keys() if k != "unknown")
        if not months:
            return

        values = [monthly_pnl[m] for m in months]

        fig, ax = plt.subplots(figsize=(12, 4))
        colors = ["#F44336" if v < 0 else "#4CAF50" for v in values]
        ax.bar(months, values, color=colors)
        ax.set_title("Monthly PnL", fontsize=14)
        ax.set_ylabel("PnL ($)")
        ax.grid(True, alpha=0.3, axis="y")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(f"{output_dir}/monthly_pnl.png", dpi=150)
        plt.close()

    def _max_consecutive(self, pnls: list, positive: bool) -> int:
        max_count = 0
        count = 0
        for p in pnls:
            if (positive and p > 0) or (not positive and p <= 0):
                count += 1
                max_count = max(max_count, count)
            else:
                count = 0
        return max_count

    def _empty_metrics(self):
        return {k: 0 for k in [
            "total_return", "total_return_pct", "cagr", "max_drawdown",
            "max_drawdown_pct", "max_dd_duration", "sharpe_ratio",
            "sortino_ratio", "calmar_ratio", "total_trades", "win_rate",
            "win_rate_pct", "profit_factor", "avg_win", "avg_loss",
            "expectancy", "max_consecutive_wins", "max_consecutive_losses",
            "avg_duration_minutes", "total_pnl", "total_fees", "final_equity",
        ]}
