#!/usr/bin/env python3
"""Oscar 1H backtest — pure Python/yfinance. No Claude / MCP / tokens needed."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from strategies import (
    compute_base_indicators, compute_supertrend, compute_bb,
    signal_oscar, signal_supertrend, signal_bos, signal_bb_reversion,
    find_ob, sl_tp_from_signal,
)

ROOT = Path(__file__).parent.parent

SYMBOLS = [
    {"yf_ticker": "GC=F",    "display": "XAUUSD"},
    {"yf_ticker": "BTC-USD", "display": "BTCUSD"},
]

LOOKBACK = 720   # ~1 month of 1H bars
RR       = 1.5
ATR_MULT = 1.0

# --- Strategy selection ---
STRATEGY         = "oscar"   # "oscar" | "supertrend" | "bos" | "bb_reversion"
ST_PERIOD        = 10
ST_MULT          = 3.0
BOS_SWING_N      = 10
BB_PERIOD        = 20
BB_STD           = 2.0
BB_RSI_THRESHOLD = 30        # long if RSI < threshold; short if RSI > (100 - threshold)


def fetch_bars(yf_ticker: str) -> pd.DataFrame:
    df = yf.download(yf_ticker, interval="1h", period="60d", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df.columns = df.columns.droplevel(1)
        except Exception:
            pass
    return df


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = compute_base_indicators(df)
    if STRATEGY == "supertrend":
        df = compute_supertrend(df, ST_PERIOD, ST_MULT)
    elif STRATEGY == "bb_reversion":
        df = compute_bb(df, BB_PERIOD, BB_STD)
    return df.dropna()


def get_signal(df: pd.DataFrame, i: int) -> "str | None":
    if STRATEGY == "oscar":
        return signal_oscar(df, i)
    if STRATEGY == "supertrend":
        return signal_supertrend(df, i)
    if STRATEGY == "bos":
        return signal_bos(df, i, BOS_SWING_N)
    if STRATEGY == "bb_reversion":
        return signal_bb_reversion(df, i, BB_RSI_THRESHOLD)
    return None


def compute_sl_tp(df: pd.DataFrame, i: int, direction: str, entry: float) -> "tuple[float, float]":
    atr = float(df["atr"].iloc[i])
    if STRATEGY == "bos":
        ob = find_ob(df, i, BOS_SWING_N, direction)
        return sl_tp_from_signal(direction, entry, atr, ATR_MULT, RR, sl_override=ob)
    if STRATEGY == "bb_reversion":
        return sl_tp_from_signal(direction, entry, atr, 1.0, RR,
                                 tp_override=float(df["bb_mid"].iloc[i]))
    return sl_tp_from_signal(direction, entry, atr, ATR_MULT, RR)


def walk_outcome(df: pd.DataFrame, fire_idx: int, direction: str,
                 entry: float, sl: float, tp: float) -> dict:
    mfe = 0.0
    mae = 0.0
    for k in range(fire_idx + 1, len(df)):
        h = float(df["High"].iloc[k])
        l = float(df["Low"].iloc[k])
        if direction == "long":
            fav = h - entry; adv = entry - l
            tp_hit = h >= tp; sl_hit = l <= sl
        else:
            fav = entry - l; adv = h - entry
            tp_hit = l <= tp; sl_hit = h >= sl
        if fav > mfe: mfe = fav
        if adv > mae: mae = adv
        if tp_hit and sl_hit:
            return {"outcome": "ambiguous", "outcome_idx": k, "mfe": mfe, "mae": mae}
        if tp_hit:
            return {"outcome": "tp",        "outcome_idx": k, "mfe": mfe, "mae": mae}
        if sl_hit:
            return {"outcome": "sl",        "outcome_idx": k, "mfe": mfe, "mae": mae}
    return {"outcome": "open", "outcome_idx": None, "mfe": mfe, "mae": mae}


def backtest_symbol(yf_ticker: str, display: str) -> dict:
    df_raw = fetch_bars(yf_ticker)
    df     = prepare(df_raw)
    n      = len(df)
    start  = max(0, n - LOOKBACK)
    fires  = []

    for i in range(start, n):
        direction = get_signal(df, i)
        if direction is None:
            continue

        entry = float(df["Close"].iloc[i])
        atr   = float(df["atr"].iloc[i])
        sl, tp = compute_sl_tp(df, i, direction, entry)

        # Sanity-check: SL must be on the losing side of entry
        if direction == "long"  and sl >= entry:
            continue
        if direction == "short" and sl <= entry:
            continue

        sl_dist   = abs(entry - sl)
        tp_dist   = abs(tp - entry)
        actual_rr = round(tp_dist / sl_dist, 3) if sl_dist > 0 else RR

        ow = walk_outcome(df, i, direction, entry, sl, tp)
        bar_time     = df.index[i]
        outcome_time = df.index[ow["outcome_idx"]].isoformat() if ow["outcome_idx"] is not None else None
        bars_to_res  = (ow["outcome_idx"] - i) if ow["outcome_idx"] is not None else None
        pnl          = {"tp": actual_rr, "sl": -1.0, "ambiguous": -1.0}.get(ow["outcome"])

        fires.append({
            "time":                     bar_time.isoformat(),
            "direction":                direction,
            "entry":                    round(entry, 4),
            "sl":                       round(sl, 4),
            "tp":                       round(tp, 4),
            "atr":                      round(atr, 4),
            "actual_rr":                actual_rr,
            "outcome":                  ow["outcome"],
            "outcome_bar":              outcome_time,
            "bars_to_resolution":       bars_to_res,
            "max_favorable_excursion":  round(ow["mfe"], 4),
            "max_adverse_excursion":    round(ow["mae"], 4),
            "pnl_in_R":                 pnl,
        })

    wins       = sum(1 for f in fires if f["outcome"] == "tp")
    losses     = sum(1 for f in fires if f["outcome"] == "sl")
    ambig      = sum(1 for f in fires if f["outcome"] == "ambiguous")
    open_      = sum(1 for f in fires if f["outcome"] == "open")
    resolved   = wins + losses + ambig
    win_rate   = round(wins / resolved, 3) if resolved > 0 else None
    total_r    = sum(f["pnl_in_R"] for f in fires if f["pnl_in_R"] is not None)
    expectancy = round(total_r / resolved, 3) if resolved > 0 else None

    return {
        "symbol":         display,
        "strategy":       STRATEGY,
        "bars_available": n,
        "lookback_bars":  LOOKBACK,
        "longs":          sum(1 for f in fires if f["direction"] == "long"),
        "shorts":         sum(1 for f in fires if f["direction"] == "short"),
        "total_fires":    len(fires),
        "fires":          fires,
        "summary": {
            "wins":            wins,
            "losses":          losses,
            "ambiguous":       ambig,
            "open":            open_,
            "win_rate":        win_rate,
            "expectancy_in_R": expectancy,
        },
    }


def main():
    results = []
    for sym in SYMBOLS:
        print(f"Backtesting {sym['display']} [{STRATEGY}]...", file=sys.stderr)
        result = backtest_symbol(sym["yf_ticker"], sym["display"])
        results.append(result)

    output = {
        "date":          datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source":        "yfinance",
        "strategy":      STRATEGY,
        "lookback_bars": LOOKBACK,
        "rr":            RR,
        "atr_mult":      ATR_MULT,
        "symbols":       results,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
