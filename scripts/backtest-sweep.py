#!/usr/bin/env python3
"""Parameter sweep across all five strategies. 3-month lookback. Prints best combo per strategy
and a final comparison table."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from strategies import (
    compute_base_indicators, compute_supertrend, compute_bb,
    signal_oscar, signal_supertrend, signal_bos, signal_bb_reversion,
    signal_supertrend_bos, find_ob, sl_tp_from_signal,
)

ROOT = Path(__file__).parent.parent

SYMBOLS = [
    {"yf_ticker": "GC=F",    "display": "XAUUSD"},
    {"yf_ticker": "BTC-USD", "display": "BTCUSD"},
]

LOOKBACK = 2160   # ~3 months of 1H bars

# Per-strategy sweep grids
OSCAR_ATR_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]
OSCAR_RR_RATIOS = [1.5, 2.0, 2.5, 3.0]

ST_PERIODS    = [7, 10, 14]
ST_MULTS      = [2.0, 3.0, 4.0]
ST_RR_RATIOS  = [1.5, 2.0, 2.5]

BOS_SWING_NS  = [5, 10, 15, 20]
BOS_RR_RATIOS = [1.5, 2.0, 2.5, 3.0]

BB_PERIODS    = [15, 20, 25]
BB_STDS       = [1.5, 2.0, 2.5]
BB_RSI_THRESHOLDS = [25, 30, 35]   # long if RSI < T; short if RSI > (100-T)

STBOS_ST_PERIODS = [7, 10, 14]
STBOS_ST_MULTS   = [2.0, 3.0, 4.0]
STBOS_SWING_NS   = [5, 10, 15, 20]
STBOS_RR_RATIOS  = [1.5, 2.0, 2.5, 3.0]


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_bars(df: pd.DataFrame, strategy: str, atr_mult: float, rr: float,
             swing_n: int = 10, rsi_threshold: int = 30) -> dict:
    """Iterate bars for one symbol+strategy+param combo. Returns stats dict."""
    n         = len(df)
    start_idx = max(0, n - LOOKBACK)
    wins = losses = ambig = open_ = fires_count = 0
    total_r = 0.0

    for i in range(start_idx, n):
        direction = None
        if strategy == "oscar":
            direction = signal_oscar(df, i)
        elif strategy == "supertrend":
            direction = signal_supertrend(df, i)
        elif strategy == "bos":
            direction = signal_bos(df, i, swing_n)
        elif strategy == "bb_reversion":
            direction = signal_bb_reversion(df, i, rsi_threshold)
        elif strategy == "supertrend_bos":
            direction = signal_supertrend_bos(df, i, swing_n)

        if direction is None:
            continue

        entry   = float(df["Close"].iloc[i])
        atr_val = float(df["atr"].iloc[i])

        if strategy in ("bos", "supertrend_bos"):
            ob = find_ob(df, i, swing_n, direction)
            sl, tp = sl_tp_from_signal(direction, entry, atr_val, atr_mult, rr, sl_override=ob)
        elif strategy == "bb_reversion":
            tp_ovr = float(df["bb_mid"].iloc[i])
            sl, tp = sl_tp_from_signal(direction, entry, atr_val, 1.0, rr, tp_override=tp_ovr)
        else:
            sl, tp = sl_tp_from_signal(direction, entry, atr_val, atr_mult, rr)

        # Skip trades where SL is on the wrong side
        if direction == "long"  and sl >= entry:
            continue
        if direction == "short" and sl <= entry:
            continue

        sl_dist   = abs(entry - sl)
        tp_dist   = abs(tp - entry)
        actual_rr = tp_dist / sl_dist if sl_dist > 0 else rr

        fires_count += 1
        outcome = "open"
        for k in range(i + 1, n):
            h = float(df["High"].iloc[k])
            l = float(df["Low"].iloc[k])
            tp_hit = (h >= tp) if direction == "long" else (l <= tp)
            sl_hit = (l <= sl) if direction == "long" else (h >= sl)
            if tp_hit and sl_hit:
                outcome = "ambiguous"; break
            if tp_hit:
                outcome = "tp"; break
            if sl_hit:
                outcome = "sl"; break

        if outcome == "tp":
            wins    += 1; total_r += actual_rr
        elif outcome == "sl":
            losses  += 1; total_r -= 1.0
        elif outcome == "ambiguous":
            ambig   += 1; total_r -= 1.0
        else:
            open_   += 1

    resolved = wins + losses + ambig
    return {
        "fires":      fires_count,
        "wins":       wins,
        "losses":     losses,
        "open":       open_,
        "win_rate":   round(wins / resolved, 3) if resolved > 0 else None,
        "expectancy": round(total_r / resolved, 3) if resolved > 0 else None,
        "total_r":    round(total_r, 2),
    }


def combined_run(frames: dict, strategy: str, atr_mult: float, rr: float,
                 swing_n: int = 10, rsi_threshold: int = 30) -> dict:
    """Run across all symbols, aggregate results."""
    combined_r = combined_fires = combined_wins = combined_losses = 0
    per_sym = {}
    for sym in SYMBOLS:
        r = run_bars(frames[sym["display"]], strategy, atr_mult, rr, swing_n, rsi_threshold)
        per_sym[sym["display"]] = r
        combined_r      += r["total_r"]
        combined_fires  += r["fires"]
        combined_wins   += r["wins"]
        combined_losses += r["losses"]

    resolved = combined_wins + combined_losses
    return {
        "fires":      combined_fires,
        "wins":       combined_wins,
        "losses":     combined_losses,
        "win_rate":   round(combined_wins / resolved, 3) if resolved > 0 else None,
        "expectancy": round(combined_r / resolved, 3) if resolved > 0 else None,
        "total_r":    round(combined_r, 2),
        "per_sym":    per_sym,
    }


# ---------------------------------------------------------------------------
# Per-strategy sweep functions
# ---------------------------------------------------------------------------

def sweep_oscar(frames: dict) -> "tuple[list, dict]":
    rows = []
    for atr_mult in OSCAR_ATR_MULTS:
        for rr in OSCAR_RR_RATIOS:
            r = combined_run(frames, "oscar", atr_mult, rr)
            rows.append({"atr_mult": atr_mult, "rr": rr, **r})
    rows.sort(key=lambda x: (x["expectancy"] or -99), reverse=True)
    return rows, rows[0]


def sweep_supertrend(frames: dict) -> "tuple[list, dict]":
    rows = []
    for st_period in ST_PERIODS:
        for st_mult in ST_MULTS:
            # Build ST-augmented frames once per (period, mult) — shared across RR combos
            st_frames = {}
            for sym in SYMBOLS:
                df_st = compute_supertrend(frames[sym["display"]], st_period, st_mult).dropna()
                st_frames[sym["display"]] = df_st

            for rr in ST_RR_RATIOS:
                r = combined_run(st_frames, "supertrend", 1.0, rr)
                rows.append({"st_period": st_period, "st_mult": st_mult, "rr": rr, **r})

    rows.sort(key=lambda x: (x["expectancy"] or -99), reverse=True)
    return rows, rows[0]


def sweep_bos(frames: dict) -> "tuple[list, dict]":
    rows = []
    for swing_n in BOS_SWING_NS:
        for rr in BOS_RR_RATIOS:
            r = combined_run(frames, "bos", 1.0, rr, swing_n=swing_n)
            rows.append({"swing_n": swing_n, "rr": rr, **r})
    rows.sort(key=lambda x: (x["expectancy"] or -99), reverse=True)
    return rows, rows[0]


def sweep_supertrend_bos(frames: dict) -> "tuple[list, dict]":
    rows = []
    for st_period in STBOS_ST_PERIODS:
        for st_mult in STBOS_ST_MULTS:
            st_frames = {}
            for sym in SYMBOLS:
                df_st = compute_supertrend(frames[sym["display"]], st_period, st_mult).dropna()
                st_frames[sym["display"]] = df_st

            for swing_n in STBOS_SWING_NS:
                for rr in STBOS_RR_RATIOS:
                    r = combined_run(st_frames, "supertrend_bos", 1.0, rr, swing_n=swing_n)
                    rows.append({"st_period": st_period, "st_mult": st_mult,
                                 "swing_n": swing_n, "rr": rr, **r})
    rows.sort(key=lambda x: (x["expectancy"] or -99), reverse=True)
    return rows, rows[0]


def sweep_bb(frames: dict) -> "tuple[list, dict]":
    rows = []
    for bb_period in BB_PERIODS:
        for bb_std in BB_STDS:
            # Build BB-augmented frames once per (period, std)
            bb_frames = {}
            for sym in SYMBOLS:
                df_bb = compute_bb(frames[sym["display"]], bb_period, bb_std).dropna()
                bb_frames[sym["display"]] = df_bb

            for rsi_thr in BB_RSI_THRESHOLDS:
                r = combined_run(bb_frames, "bb_reversion", 1.0, 1.5, rsi_threshold=rsi_thr)
                rows.append({"bb_period": bb_period, "bb_std": bb_std,
                              "rsi_thr": rsi_thr, **r})

    rows.sort(key=lambda x: (x["expectancy"] or -99), reverse=True)
    return rows, rows[0]


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def _fmt(val, fmt="+.3f"):
    return f"{val:{fmt}}" if val is not None else "   n/a"


def print_oscar(rows: list) -> None:
    print(f"\n{'ATR':>5} {'RR':>5} {'Fires':>6} {'W':>4} {'L':>4} {'WinRate':>8} {'Exp/R':>7} {'TotR':>7}  "
          + "  ".join(s["display"] for s in SYMBOLS))
    print("-" * 78)
    for r in rows:
        sym_cols = "  ".join(_fmt(r["per_sym"][s["display"]]["expectancy"]) for s in SYMBOLS)
        print(f"{r['atr_mult']:>5.1f} {r['rr']:>5.1f} {r['fires']:>6} {r['wins']:>4} {r['losses']:>4} "
              f"{_fmt(r['win_rate'], '.3f'):>8} {_fmt(r['expectancy']):>7} {r['total_r']:>7.2f}  {sym_cols}")


def print_supertrend(rows: list) -> None:
    print(f"\n{'STp':>4} {'STm':>5} {'RR':>5} {'Fires':>6} {'W':>4} {'L':>4} {'WinRate':>8} {'Exp/R':>7} {'TotR':>7}  "
          + "  ".join(s["display"] for s in SYMBOLS))
    print("-" * 82)
    for r in rows:
        sym_cols = "  ".join(_fmt(r["per_sym"][s["display"]]["expectancy"]) for s in SYMBOLS)
        print(f"{r['st_period']:>4} {r['st_mult']:>5.1f} {r['rr']:>5.1f} {r['fires']:>6} {r['wins']:>4} {r['losses']:>4} "
              f"{_fmt(r['win_rate'], '.3f'):>8} {_fmt(r['expectancy']):>7} {r['total_r']:>7.2f}  {sym_cols}")


def print_bos(rows: list) -> None:
    print(f"\n{'N':>4} {'RR':>5} {'Fires':>6} {'W':>4} {'L':>4} {'WinRate':>8} {'Exp/R':>7} {'TotR':>7}  "
          + "  ".join(s["display"] for s in SYMBOLS))
    print("-" * 78)
    for r in rows:
        sym_cols = "  ".join(_fmt(r["per_sym"][s["display"]]["expectancy"]) for s in SYMBOLS)
        print(f"{r['swing_n']:>4} {r['rr']:>5.1f} {r['fires']:>6} {r['wins']:>4} {r['losses']:>4} "
              f"{_fmt(r['win_rate'], '.3f'):>8} {_fmt(r['expectancy']):>7} {r['total_r']:>7.2f}  {sym_cols}")


def print_supertrend_bos(rows: list) -> None:
    print(f"\n{'STp':>4} {'STm':>5} {'N':>4} {'RR':>5} {'Fires':>6} {'W':>4} {'L':>4} {'WinRate':>8} {'Exp/R':>7} {'TotR':>7}  "
          + "  ".join(s["display"] for s in SYMBOLS))
    print("-" * 90)
    for r in rows:
        sym_cols = "  ".join(_fmt(r["per_sym"][s["display"]]["expectancy"]) for s in SYMBOLS)
        print(f"{r['st_period']:>4} {r['st_mult']:>5.1f} {r['swing_n']:>4} {r['rr']:>5.1f} "
              f"{r['fires']:>6} {r['wins']:>4} {r['losses']:>4} "
              f"{_fmt(r['win_rate'], '.3f'):>8} {_fmt(r['expectancy']):>7} {r['total_r']:>7.2f}  {sym_cols}")


def print_bb(rows: list) -> None:
    print(f"\n{'BBp':>4} {'BBs':>5} {'RSI':>4} {'Fires':>6} {'W':>4} {'L':>4} {'WinRate':>8} {'Exp/R':>7} {'TotR':>7}  "
          + "  ".join(s["display"] for s in SYMBOLS))
    print("-" * 84)
    for r in rows:
        sym_cols = "  ".join(_fmt(r["per_sym"][s["display"]]["expectancy"]) for s in SYMBOLS)
        print(f"{r['bb_period']:>4} {r['bb_std']:>5.1f} {r['rsi_thr']:>4} {r['fires']:>6} {r['wins']:>4} {r['losses']:>4} "
              f"{_fmt(r['win_rate'], '.3f'):>8} {_fmt(r['expectancy']):>7} {r['total_r']:>7.2f}  {sym_cols}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Fetching data...", file=sys.stderr)
    raw_frames = {}
    for sym in SYMBOLS:
        print(f"  {sym['display']}...", file=sys.stderr)
        df = yf.download(sym["yf_ticker"], interval="1h", period="180d", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df.columns = df.columns.droplevel(1)
            except Exception:
                pass
        raw_frames[sym["display"]] = df

    # Compute base indicators once — shared across all strategies
    print("Computing base indicators...", file=sys.stderr)
    base_frames = {}
    for sym in SYMBOLS:
        base_frames[sym["display"]] = compute_base_indicators(raw_frames[sym["display"]]).dropna()

    # --- Oscar ---
    print("\nSweeping Oscar...", file=sys.stderr)
    oscar_rows, oscar_best = sweep_oscar(base_frames)
    print("\n=== OSCAR (EMA50/200 + RSI cross + MACD flip) ===")
    print_oscar(oscar_rows)
    print(f"\nBest: ATR x{oscar_best['atr_mult']}  RR {oscar_best['rr']}  ->  "
          f"expectancy {_fmt(oscar_best['expectancy'])} R  ({oscar_best['wins']}W/{oscar_best['losses']}L)")

    # --- Supertrend ---
    print("\nSweeping Supertrend...", file=sys.stderr)
    st_rows, st_best = sweep_supertrend(base_frames)
    print("\n=== SUPERTREND (ATR-based direction + RSI cross + MACD flip) ===")
    print_supertrend(st_rows)
    print(f"\nBest: STp={st_best['st_period']}  STm={st_best['st_mult']}  RR {st_best['rr']}  ->  "
          f"expectancy {_fmt(st_best['expectancy'])} R  ({st_best['wins']}W/{st_best['losses']}L)")

    # --- Break of Structure ---
    print("\nSweeping BOS...", file=sys.stderr)
    bos_rows, bos_best = sweep_bos(base_frames)
    print("\n=== BREAK OF STRUCTURE + ORDER BLOCKS ===")
    print_bos(bos_rows)
    print(f"\nBest: N={bos_best['swing_n']}  RR {bos_best['rr']}  ->  "
          f"expectancy {_fmt(bos_best['expectancy'])} R  ({bos_best['wins']}W/{bos_best['losses']}L)")

    # --- BB Mean Reversion ---
    print("\nSweeping BB Reversion...", file=sys.stderr)
    bb_rows, bb_best = sweep_bb(base_frames)
    print("\n=== BOLLINGER BAND MEAN REVERSION ===")
    print_bb(bb_rows)
    print(f"\nBest: BBp={bb_best['bb_period']}  BBs={bb_best['bb_std']}  RSI {bb_best['rsi_thr']}/{100 - bb_best['rsi_thr']}  ->  "
          f"expectancy {_fmt(bb_best['expectancy'])} R  ({bb_best['wins']}W/{bb_best['losses']}L)")

    # --- Supertrend + BOS ---
    print("\nSweeping Supertrend+BOS...", file=sys.stderr)
    stbos_rows, stbos_best = sweep_supertrend_bos(base_frames)
    print("\n=== SUPERTREND + BREAK OF STRUCTURE ===")
    print_supertrend_bos(stbos_rows)
    print(f"\nBest: STp={stbos_best['st_period']}  STm={stbos_best['st_mult']}  N={stbos_best['swing_n']}  RR {stbos_best['rr']}  ->  "
          f"expectancy {_fmt(stbos_best['expectancy'])} R  ({stbos_best['wins']}W/{stbos_best['losses']}L)")

    # --- Final comparison ---
    comparison = [
        ("Oscar",          f"ATR x{oscar_best['atr_mult']}  RR {oscar_best['rr']}",            oscar_best),
        ("Supertrend",     f"STp={st_best['st_period']}  STm={st_best['st_mult']}  RR {st_best['rr']}", st_best),
        ("BOS",            f"N={bos_best['swing_n']}  RR {bos_best['rr']}",                    bos_best),
        ("BB Reversion",   f"BBp={bb_best['bb_period']}  BBs={bb_best['bb_std']}  RSI {bb_best['rsi_thr']}", bb_best),
        ("ST+BOS",         f"STp={stbos_best['st_period']}  STm={stbos_best['st_mult']}  N={stbos_best['swing_n']}  RR {stbos_best['rr']}", stbos_best),
    ]
    comparison.sort(key=lambda x: (x[2]["expectancy"] or -99), reverse=True)

    print("\n" + "=" * 72)
    print("FINAL COMPARISON — best combo per strategy, 3-month lookback")
    print("=" * 72)
    print(f"{'Strategy':<16} {'Best Params':<44} {'Exp/R':>7} {'Fires':>6} {'W':>4} {'L':>4}")
    print("-" * 80)
    for name, params, r in comparison:
        print(f"{name:<16} {params:<44} {_fmt(r['expectancy']):>7} {r['fires']:>6} {r['wins']:>4} {r['losses']:>4}")
    print()


if __name__ == "__main__":
    main()
