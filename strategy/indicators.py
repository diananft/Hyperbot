"""Comprehensive technical indicators using pure pandas/numpy (no ta dependency)."""

import pandas as pd
import numpy as np
from typing import Dict, Tuple
from utils.logger import get_logger

logger = get_logger("indicators")


# ===== Pure implementations of technical indicators =====

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # When +DM > -DM, keep +DM, else 0 (and vice versa)
    cond = plus_dm > minus_dm
    plus_dm = plus_dm.where(cond, 0)
    minus_dm = minus_dm.where(~cond, 0)

    atr_val = _atr(high, low, close, period)
    plus_di = 100 * _ema(plus_dm, period) / atr_val.replace(0, np.nan)
    minus_di = 100 * _ema(minus_dm, period) / atr_val.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = _ema(dx, period)
    return adx_val, plus_di, minus_di


def _bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    middle = _sma(close, period)
    std = close.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = (upper - lower) / middle
    pctb = (close - lower) / (upper - lower).replace(0, np.nan)
    return upper, middle, lower, bandwidth, pctb


def _stochastic_rsi(close: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
                     smooth_k: int = 3, smooth_d: int = 3):
    rsi_val = _rsi(close, rsi_period)
    stoch_rsi = (rsi_val - rsi_val.rolling(stoch_period).min()) / \
                (rsi_val.rolling(stoch_period).max() - rsi_val.rolling(stoch_period).min()).replace(0, np.nan)
    k = stoch_rsi.rolling(smooth_k).mean() * 100
    d = k.rolling(smooth_d).mean()
    return k, d


def _cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3
    sma_tp = _sma(tp, period)
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma_tp) / (0.015 * mad).replace(0, np.nan)


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    return -100 * (hh - close) / (hh - ll).replace(0, np.nan)


def _mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    mf = tp * volume
    tp_diff = tp.diff()
    pos_mf = mf.where(tp_diff > 0, 0).rolling(period).sum()
    neg_mf = mf.where(tp_diff <= 0, 0).rolling(period).sum()
    ratio = pos_mf / neg_mf.replace(0, np.nan)
    return 100 - (100 / (1 + ratio))


def _roc(close: pd.Series, period: int = 12) -> pd.Series:
    return ((close - close.shift(period)) / close.shift(period).replace(0, np.nan)) * 100


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (volume * direction).cumsum()


def _ad_line(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    hl = (high - low).replace(0, np.nan)
    clv = ((close - low) - (high - close)) / hl
    return (clv.fillna(0) * volume).cumsum()


def _cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 21) -> pd.Series:
    hl = (high - low).replace(0, np.nan)
    clv = ((close - low) - (high - close)) / hl
    return (clv.fillna(0) * volume).rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)


def _ichimoku(high: pd.Series, low: pd.Series):
    conv = (high.rolling(9).max() + low.rolling(9).min()) / 2
    base = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((conv + base) / 2).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    return conv, base, span_a, span_b


def _psar(high: pd.Series, low: pd.Series, close: pd.Series,
          af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2):
    """Parabolic SAR - simplified implementation."""
    n = len(close)
    psar = pd.Series(np.nan, index=close.index)
    psar_up = pd.Series(np.nan, index=close.index)
    psar_down = pd.Series(np.nan, index=close.index)

    if n < 2:
        return psar, psar_up, psar_down

    bull = True
    af = af_start
    ep = low.iloc[0]
    hp = high.iloc[0]
    lp = low.iloc[0]
    psar.iloc[0] = high.iloc[0]

    for i in range(1, n):
        if bull:
            psar.iloc[i] = psar.iloc[i-1] + af * (hp - psar.iloc[i-1])
            psar.iloc[i] = min(psar.iloc[i], low.iloc[i-1])
            if i >= 2:
                psar.iloc[i] = min(psar.iloc[i], low.iloc[i-2])

            if low.iloc[i] < psar.iloc[i]:
                bull = False
                psar.iloc[i] = hp
                lp = low.iloc[i]
                af = af_start
            else:
                if high.iloc[i] > hp:
                    hp = high.iloc[i]
                    af = min(af + af_step, af_max)
                psar_up.iloc[i] = psar.iloc[i]
        else:
            psar.iloc[i] = psar.iloc[i-1] + af * (lp - psar.iloc[i-1])
            psar.iloc[i] = max(psar.iloc[i], high.iloc[i-1])
            if i >= 2:
                psar.iloc[i] = max(psar.iloc[i], high.iloc[i-2])

            if high.iloc[i] > psar.iloc[i]:
                bull = True
                psar.iloc[i] = lp
                hp = high.iloc[i]
                af = af_start
            else:
                if low.iloc[i] < lp:
                    lp = low.iloc[i]
                    af = min(af + af_step, af_max)
                psar_down.iloc[i] = psar.iloc[i]

    return psar, psar_up, psar_down


def _keltner_channels(high: pd.Series, low: pd.Series, close: pd.Series,
                      period: int = 20, atr_period: int = 14, mult: float = 1.5):
    middle = _ema(close, period)
    atr_val = _atr(high, low, close, atr_period)
    upper = middle + mult * atr_val
    lower = middle - mult * atr_val
    return upper, middle, lower


class TechnicalIndicators:
    """Calculate all technical indicators on OHLCV DataFrames."""

    def __init__(self, config: dict):
        ind = config.get("indicators", {})
        self.ema_periods = ind.get("ema_periods", [9, 21, 50, 200])
        self.sma_periods = ind.get("sma_periods", [20, 50, 200])
        self.macd_params = ind.get("macd", [12, 26, 9])
        self.adx_period = ind.get("adx_period", 14)
        self.rsi_period = ind.get("rsi_period", 14)
        self.stochrsi_params = ind.get("stochrsi", [14, 14, 3, 3])
        self.cci_period = ind.get("cci_period", 20)
        self.williams_period = ind.get("williams_period", 14)
        self.mfi_period = ind.get("mfi_period", 14)
        self.roc_period = ind.get("roc_period", 12)
        self.bb_period = ind.get("bb_period", 20)
        self.bb_std = ind.get("bb_std", 2.0)
        self.atr_period = ind.get("atr_period", 14)
        self.keltner_period = ind.get("keltner_period", 20)
        self.keltner_atr_mult = ind.get("keltner_atr_mult", 1.5)
        self.hvol_period = ind.get("hvol_period", 20)
        self.cmf_period = ind.get("cmf_period", 21)
        self.supertrend_period = ind.get("supertrend_period", 10)
        self.supertrend_mult = ind.get("supertrend_mult", 3.0)
        self.linreg_period = ind.get("linreg_period", 20)

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all indicators on a candle DataFrame.

        Input df must have columns: open, high, low, close, volume
        Returns df with all indicator columns added.
        """
        if len(df) < 50:
            logger.warning(f"Insufficient data for indicators: {len(df)} candles")
            return df

        df = df.copy()
        h, l, c, v = df["high"], df["low"], df["close"], df["volume"]
        o = df["open"]

        # ===== TREND =====
        for p in self.ema_periods:
            df[f"ema_{p}"] = _ema(c, p)
        for p in self.sma_periods:
            df[f"sma_{p}"] = _sma(c, p)

        # MACD
        fast, slow, sig = self.macd_params
        df["macd"], df["macd_signal"], df["macd_histogram"] = _macd(c, fast, slow, sig)

        # ADX / DI+ / DI-
        df["adx"], df["di_plus"], df["di_minus"] = _adx(h, l, c, self.adx_period)

        # Ichimoku
        df["ichimoku_conv"], df["ichimoku_base"], df["ichimoku_a"], df["ichimoku_b"] = _ichimoku(h, l)

        # Supertrend
        df = self._supertrend(df, self.supertrend_period, self.supertrend_mult)

        # Parabolic SAR
        df["psar"], df["psar_up"], df["psar_down"] = _psar(h, l, c)

        # Linear Regression Slope + R²
        df["linreg_slope"], df["linreg_r2"] = self._linear_regression(c, self.linreg_period)

        # ===== MOMENTUM =====
        df["rsi"] = _rsi(c, self.rsi_period)
        df["rsi_bullish_div"], df["rsi_bearish_div"] = self._rsi_divergence(df)

        # Stochastic RSI
        df["stochrsi_k"], df["stochrsi_d"] = _stochastic_rsi(
            c, self.stochrsi_params[0], self.stochrsi_params[1],
            self.stochrsi_params[2], self.stochrsi_params[3]
        )

        df["cci"] = _cci(h, l, c, self.cci_period)
        df["williams_r"] = _williams_r(h, l, c, self.williams_period)
        df["mfi"] = _mfi(h, l, c, v, self.mfi_period)
        df["roc"] = _roc(c, self.roc_period)

        # ===== VOLATILITY =====
        bb_upper, bb_mid, bb_lower, bb_bw, bb_pctb = _bollinger_bands(c, self.bb_period, self.bb_std)
        df["bb_upper"] = bb_upper
        df["bb_middle"] = bb_mid
        df["bb_lower"] = bb_lower
        df["bb_bandwidth"] = bb_bw
        df["bb_pctb"] = bb_pctb

        df["atr"] = _atr(h, l, c, self.atr_period)

        kc_upper, kc_mid, kc_lower = _keltner_channels(h, l, c, self.keltner_period,
                                                         self.atr_period, self.keltner_atr_mult)
        df["kc_upper"] = kc_upper
        df["kc_middle"] = kc_mid
        df["kc_lower"] = kc_lower

        # Historical Volatility (annualized)
        log_ret = np.log(c / c.shift(1))
        df["hvol"] = log_ret.rolling(window=self.hvol_period).std() * np.sqrt(365)

        # BB Squeeze (BB inside KC)
        df["squeeze"] = (df["bb_upper"] < df["kc_upper"]) & (df["bb_lower"] > df["kc_lower"])
        df["squeeze_release"] = df["squeeze"].shift(1).fillna(False) & ~df["squeeze"]

        # ===== VOLUME =====
        df["vwap"] = self._calculate_vwap(df)
        df["vwap_std"] = (c - df["vwap"]).rolling(20).std()
        df["vwap_upper1"] = df["vwap"] + df["vwap_std"]
        df["vwap_lower1"] = df["vwap"] - df["vwap_std"]
        df["vwap_upper2"] = df["vwap"] + 2 * df["vwap_std"]
        df["vwap_lower2"] = df["vwap"] - 2 * df["vwap_std"]

        df["obv"] = _obv(c, v)
        df["obv_ma"] = df["obv"].rolling(20).mean()

        df["cvd"] = self._calculate_cvd(df)

        df["volume_ma_20"] = v.rolling(20).mean()
        df["volume_ratio"] = v / df["volume_ma_20"].replace(0, np.nan)

        df["ad_line"] = _ad_line(h, l, c, v)
        df["cmf"] = _cmf(h, l, c, v, self.cmf_period)

        return df

    def _supertrend(self, df: pd.DataFrame, period: int, multiplier: float) -> pd.DataFrame:
        """Calculate Supertrend indicator."""
        hl2 = (df["high"] + df["low"]) / 2
        atr_val = _atr(df["high"], df["low"], df["close"], period)

        upper_band = hl2 + multiplier * atr_val
        lower_band = hl2 - multiplier * atr_val

        supertrend = pd.Series(np.nan, index=df.index)
        direction = pd.Series(1, index=df.index)

        for i in range(period, len(df)):
            if df["close"].iloc[i] > upper_band.iloc[i - 1]:
                direction.iloc[i] = 1
            elif df["close"].iloc[i] < lower_band.iloc[i - 1]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1]

            if direction.iloc[i] == 1:
                lower_band.iloc[i] = max(lower_band.iloc[i],
                                          lower_band.iloc[i - 1] if direction.iloc[i - 1] == 1 else lower_band.iloc[i])
                supertrend.iloc[i] = lower_band.iloc[i]
            else:
                upper_band.iloc[i] = min(upper_band.iloc[i],
                                          upper_band.iloc[i - 1] if direction.iloc[i - 1] == -1 else upper_band.iloc[i])
                supertrend.iloc[i] = upper_band.iloc[i]

        df["supertrend"] = supertrend
        df["supertrend_dir"] = direction
        return df

    def _linear_regression(self, series: pd.Series, period: int) -> Tuple[pd.Series, pd.Series]:
        """Calculate rolling linear regression slope and R²."""
        slope = pd.Series(np.nan, index=series.index)
        r_squared = pd.Series(np.nan, index=series.index)

        for i in range(period - 1, len(series)):
            y = series.iloc[i - period + 1:i + 1].values
            if np.any(np.isnan(y)):
                continue
            x = np.arange(period)
            x_mean = x.mean()
            y_mean = y.mean()
            ss_xy = np.sum((x - x_mean) * (y - y_mean))
            ss_xx = np.sum((x - x_mean) ** 2)
            ss_yy = np.sum((y - y_mean) ** 2)
            if ss_xx == 0 or ss_yy == 0:
                continue
            b = ss_xy / ss_xx
            r2 = (ss_xy ** 2) / (ss_xx * ss_yy)
            slope.iloc[i] = b
            r_squared.iloc[i] = r2

        return slope, r_squared

    def _rsi_divergence(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """Detect RSI bullish and bearish divergence."""
        lookback = 14
        bullish = pd.Series(False, index=df.index)
        bearish = pd.Series(False, index=df.index)

        if "rsi" not in df.columns or len(df) < lookback * 2:
            return bullish, bearish

        close = df["close"]
        rsi = df["rsi"]

        for i in range(lookback * 2, len(df)):
            window_close = close.iloc[i - lookback:i + 1]
            window_rsi = rsi.iloc[i - lookback:i + 1]

            if window_rsi.isna().any():
                continue

            if (close.iloc[i] < window_close.iloc[:-1].min() and
                rsi.iloc[i] > window_rsi.iloc[:-1].min()):
                bullish.iloc[i] = True

            if (close.iloc[i] > window_close.iloc[:-1].max() and
                rsi.iloc[i] < window_rsi.iloc[:-1].max()):
                bearish.iloc[i] = True

        return bullish, bearish

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """Calculate VWAP."""
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        cum_tp_vol = (typical_price * df["volume"]).cumsum()
        cum_vol = df["volume"].cumsum()
        return cum_tp_vol / cum_vol.replace(0, np.nan)

    def _calculate_cvd(self, df: pd.DataFrame) -> pd.Series:
        """Approximate Cumulative Volume Delta from OHLCV."""
        hl_range = df["high"] - df["low"]
        hl_range = hl_range.replace(0, np.nan)
        buy_pct = (df["close"] - df["low"]) / hl_range
        buy_vol = df["volume"] * buy_pct.fillna(0.5)
        sell_vol = df["volume"] - buy_vol
        delta = buy_vol - sell_vol
        return delta.cumsum()

    def get_latest_values(self, df: pd.DataFrame) -> dict:
        """Extract the latest indicator values as a flat dict."""
        if len(df) == 0:
            return {}
        last = df.iloc[-1]
        result = {}
        for col in df.columns:
            if col not in ("timestamp", "open", "high", "low", "close", "volume"):
                val = last[col]
                if pd.notna(val):
                    result[col] = float(val) if not isinstance(val, bool) else val
        return result
