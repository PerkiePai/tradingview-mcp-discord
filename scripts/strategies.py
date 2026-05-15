#!/usr/bin/env python3
"""Shared indicator + signal logic for Oscar scanner and all backtests."""

import numpy as np
import pandas as pd
import ta as ta_lib


# ---------------------------------------------------------------------------
# Indicator builders
# ---------------------------------------------------------------------------

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex or list-valued column names to simple strings (level 0)."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    elif not all(isinstance(c, str) for c in df.columns):
        df = df.copy()
        df.columns = [c[0] if isinstance(c, (list, tuple)) else str(c) for c in df.columns]
    return df


def compute_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """EMA50/200, RSI(14), MACD(12,26,9), ATR(14). Does NOT dropna — caller decides when."""
    df = _normalize_columns(df)
    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    df = df.copy()
    df["ema50"]     = ta_lib.trend.EMAIndicator(close, window=50).ema_indicator()
    df["ema200"]    = ta_lib.trend.EMAIndicator(close, window=200).ema_indicator()
    df["rsi"]       = ta_lib.momentum.RSIIndicator(close, window=14).rsi()
    df["macd_hist"] = ta_lib.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9).macd_diff()
    df["atr"]       = ta_lib.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    return df


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Add st_dir (1=bullish, -1=bearish) and st_line columns. Uses its own ATR(period)."""
    df = df.copy()
    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()

    atr_st = ta_lib.volatility.AverageTrueRange(high, low, close, window=period).average_true_range().to_numpy(dtype=float)
    hl2    = ((df["High"] + df["Low"]) / 2).to_numpy(dtype=float)
    cls    = df["Close"].to_numpy(dtype=float)
    n      = len(df)

    basic_upper = hl2 + multiplier * atr_st
    basic_lower = hl2 - multiplier * atr_st

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st_dir      = np.zeros(n, dtype=int)
    st_line     = np.full(n, np.nan)

    final_upper[0] = basic_upper[0]
    final_lower[0] = basic_lower[0]
    st_dir[0]      = 1
    st_line[0]     = final_lower[0]

    for i in range(1, n):
        if np.isnan(atr_st[i]):
            final_upper[i] = final_upper[i - 1]
            final_lower[i] = final_lower[i - 1]
            st_dir[i]      = st_dir[i - 1]
            st_line[i]     = st_line[i - 1]
            continue

        # Upper band tightens only when price is below prior upper
        final_upper[i] = (basic_upper[i]
                          if basic_upper[i] < final_upper[i - 1] or cls[i - 1] > final_upper[i - 1]
                          else final_upper[i - 1])

        # Lower band rises only when price is above prior lower
        final_lower[i] = (basic_lower[i]
                          if basic_lower[i] > final_lower[i - 1] or cls[i - 1] < final_lower[i - 1]
                          else final_lower[i - 1])

        if st_dir[i - 1] == -1:
            st_dir[i] = 1 if cls[i] > final_upper[i] else -1
        else:
            st_dir[i] = -1 if cls[i] < final_lower[i] else 1

        st_line[i] = final_lower[i] if st_dir[i] == 1 else final_upper[i]

    df["st_dir"]  = st_dir
    df["st_line"] = st_line
    return df


def compute_bb(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Add bb_upper, bb_lower, bb_mid columns."""
    df = df.copy()
    close = df["Close"].squeeze()
    bb = ta_lib.volatility.BollingerBands(close, window=period, window_dev=std)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"]   = bb.bollinger_mavg()
    return df


# ---------------------------------------------------------------------------
# Signal functions — all return "long" | "short" | None
# ---------------------------------------------------------------------------

def signal_oscar(df: pd.DataFrame, i: int) -> "str | None":
    """EMA50/200 trend + RSI cross 50 + MACD hist flip. Requires base indicators."""
    if i < 1:
        return None
    close  = float(df["Close"].iloc[i])
    ema50  = float(df["ema50"].iloc[i])
    ema200 = float(df["ema200"].iloc[i])
    rsi_l  = float(df["rsi"].iloc[i])
    rsi_p  = float(df["rsi"].iloc[i - 1])
    hist_l = float(df["macd_hist"].iloc[i])
    hist_p = float(df["macd_hist"].iloc[i - 1])

    if (close > ema50 and ema50 > ema200
            and rsi_p < 50 and rsi_l >= 50
            and hist_p <= 0 and hist_l > 0):
        return "long"
    if (close < ema50 and ema50 < ema200
            and rsi_p >= 50 and rsi_l < 50
            and hist_p >= 0 and hist_l < 0):
        return "short"
    return None


def signal_supertrend(df: pd.DataFrame, i: int) -> "str | None":
    """Supertrend direction + RSI cross 50 + MACD hist flip. Requires base + ST indicators."""
    if i < 1:
        return None
    st_dir = int(df["st_dir"].iloc[i])
    rsi_l  = float(df["rsi"].iloc[i])
    rsi_p  = float(df["rsi"].iloc[i - 1])
    hist_l = float(df["macd_hist"].iloc[i])
    hist_p = float(df["macd_hist"].iloc[i - 1])

    if (st_dir == 1
            and rsi_p < 50 and rsi_l >= 50
            and hist_p <= 0 and hist_l > 0):
        return "long"
    if (st_dir == -1
            and rsi_p >= 50 and rsi_l < 50
            and hist_p >= 0 and hist_l < 0):
        return "short"
    return None


def signal_bos(df: pd.DataFrame, i: int, swing_n: int = 10) -> "str | None":
    """Break of structure: close exceeds highest/lowest close over prior swing_n bars."""
    if i < swing_n + 1:
        return None
    close      = float(df["Close"].iloc[i])
    prev_high  = float(df["Close"].iloc[i - swing_n : i].max())
    prev_low   = float(df["Close"].iloc[i - swing_n : i].min())

    if close > prev_high:
        return "long"
    if close < prev_low:
        return "short"
    return None


def signal_supertrend_bos(df: pd.DataFrame, i: int, swing_n: int = 10) -> "str | None":
    """Supertrend trend filter + BOS entry trigger (no RSI/MACD).
    Long: ST bullish + close breaks above prior swing_n-bar high.
    Short: ST bearish + close breaks below prior swing_n-bar low."""
    if i < swing_n + 1:
        return None
    st_dir    = int(df["st_dir"].iloc[i])
    close     = float(df["Close"].iloc[i])
    prev_high = float(df["Close"].iloc[i - swing_n : i].max())
    prev_low  = float(df["Close"].iloc[i - swing_n : i].min())
    if st_dir == 1  and close > prev_high:
        return "long"
    if st_dir == -1 and close < prev_low:
        return "short"
    return None


def signal_bb_reversion(df: pd.DataFrame, i: int, rsi_threshold: int = 30) -> "str | None":
    """BB outer band touch + RSI extreme. Requires base + BB indicators."""
    close    = float(df["Close"].iloc[i])
    bb_upper = float(df["bb_upper"].iloc[i])
    bb_lower = float(df["bb_lower"].iloc[i])
    rsi      = float(df["rsi"].iloc[i])

    if close < bb_lower and rsi < rsi_threshold:
        return "long"
    if close > bb_upper and rsi > (100 - rsi_threshold):
        return "short"
    return None


# ---------------------------------------------------------------------------
# Order block locator (for BOS SL)
# ---------------------------------------------------------------------------

def find_ob(df: pd.DataFrame, i: int, swing_n: int, direction: str) -> "float | None":
    """Return OB low (long) or OB high (short) — the last opposing candle before bar i.
    Returns None if no qualifying candle found in the prior swing_n bars."""
    for k in range(i - 1, max(i - swing_n - 1, 0), -1):
        o = float(df["Open"].iloc[k])
        c = float(df["Close"].iloc[k])
        if direction == "long" and c < o:
            return float(df["Low"].iloc[k])
        if direction == "short" and c > o:
            return float(df["High"].iloc[k])
    return None


# ---------------------------------------------------------------------------
# SL / TP calculator
# ---------------------------------------------------------------------------

def sl_tp_from_signal(
    direction:   str,
    entry:       float,
    atr:         float,
    atr_mult:    float = 1.0,
    rr:          float = 1.5,
    tp_override: "float | None" = None,
    sl_override: "float | None" = None,
) -> "tuple[float, float]":
    """Compute (sl, tp).

    sl_override replaces the ATR-based SL (e.g. order block price for BOS).
    tp_override replaces the RR-based TP (e.g. BB midline for mean reversion).
    """
    if sl_override is not None:
        sl      = sl_override
        sl_dist = abs(entry - sl)
    else:
        sl_dist = atr_mult * atr
        sl = (entry - sl_dist) if direction == "long" else (entry + sl_dist)

    if tp_override is not None:
        tp = tp_override
    else:
        tp = (entry + rr * sl_dist) if direction == "long" else (entry - rr * sl_dist)

    return round(sl, 6), round(tp, 6)
