#!/usr/bin/env python3
"""9-month walk-forward cross-validation — 3 non-overlapping ~3-month folds.
   Fixed best-params per strategy (identified from 3-month full-dataset sweep).
   Fold 1 = oldest, Fold 3 = most recent. Outcome walks extend to end of dataset."""

import sys
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

FOLD_BARS = 2160   # ~3 months of 1H bars
N_FOLDS   = 3
WARMUP    = 210    # bars before fold 1 needed for EMA200

# Best params from 3-month full-dataset sweep
CONFIGS = [
    {"name": "Oscar",        "key": "oscar",
     "atr_mult": 1.0, "rr": 1.5},
    {"name": "Supertrend",   "key": "supertrend",
     "atr_mult": 1.0, "rr": 1.5, "st_period": 10, "st_mult": 2.0},
    {"name": "BOS",          "key": "bos",
     "atr_mult": 1.0, "rr": 3.0, "swing_n": 5},
    {"name": "BB Reversion", "key": "bb_reversion",
     "atr_mult": 1.0, "rr": 1.5, "bb_period": 25, "bb_std": 2.5, "rsi_threshold": 30},
    {"name": "ST+BOS",       "key": "supertrend_bos",
     "atr_mult": 1.0, "rr": 3.0, "st_period": 7, "st_mult": 4.0, "swing_n": 20},
]


# ---------------------------------------------------------------------------
# Data prep
# ---------------------------------------------------------------------------

def _build_frames(raw: dict, config: dict, needed: int) -> dict:
    frames = {}
    for sym in SYMBOLS:
        df = compute_base_indicators(raw[sym["display"]])
        if config["key"] in ("supertrend", "supertrend_bos"):
            df = compute_supertrend(df, config["st_period"], config["st_mult"])
        elif config["key"] == "bb_reversion":
            df = compute_bb(df, config["bb_period"], config["bb_std"])
        df = df.dropna()
        if len(df) < needed:
            print(f"  WARNING {sym['display']}: {len(df)} bars available, need {needed}", file=sys.stderr)
        frames[sym["display"]] = df.iloc[-needed:].copy()
    return frames


# ---------------------------------------------------------------------------
# Signal + SL/TP dispatch
# ---------------------------------------------------------------------------

def _signal(df: pd.DataFrame, i: int, config: dict) -> "str | None":
    key = config["key"]
    if key == "oscar":           return signal_oscar(df, i)
    if key == "supertrend":      return signal_supertrend(df, i)
    if key == "bos":             return signal_bos(df, i, config["swing_n"])
    if key == "bb_reversion":    return signal_bb_reversion(df, i, config["rsi_threshold"])
    if key == "supertrend_bos":  return signal_supertrend_bos(df, i, config["swing_n"])
    return None


def _sl_tp(df: pd.DataFrame, i: int, direction: str, config: dict) -> "tuple[float, float]":
    entry = float(df["Close"].iloc[i])
    atr   = float(df["atr"].iloc[i])
    if config["key"] in ("bos", "supertrend_bos"):
        ob = find_ob(df, i, config["swing_n"], direction)
        return sl_tp_from_signal(direction, entry, atr, config["atr_mult"], config["rr"], sl_override=ob)
    if config["key"] == "bb_reversion":
        return sl_tp_from_signal(direction, entry, atr, 1.0, config["rr"],
                                 tp_override=float(df["bb_mid"].iloc[i]))
    return sl_tp_from_signal(direction, entry, atr, config["atr_mult"], config["rr"])


# ---------------------------------------------------------------------------
# Fold runner
# ---------------------------------------------------------------------------

def run_fold(df: pd.DataFrame, fold_start: int, fold_end: int, config: dict) -> dict:
    """Scan signals in [fold_start, fold_end). Outcome walk extends to end of df."""
    wins = losses = ambig = open_ = fires = 0
    total_r = 0.0

    for i in range(fold_start, min(fold_end, len(df))):
        direction = _signal(df, i, config)
        if direction is None:
            continue

        entry  = float(df["Close"].iloc[i])
        sl, tp = _sl_tp(df, i, direction, config)

        if direction == "long"  and sl >= entry: continue
        if direction == "short" and sl <= entry: continue

        sl_dist   = abs(entry - sl)
        tp_dist   = abs(tp - entry)
        actual_rr = tp_dist / sl_dist if sl_dist > 0 else config["rr"]
        fires += 1

        outcome = "open"
        for k in range(i + 1, len(df)):
            h = float(df["High"].iloc[k])
            l = float(df["Low"].iloc[k])
            tp_hit = (h >= tp) if direction == "long" else (l <= tp)
            sl_hit = (l <= sl) if direction == "long" else (h >= sl)
            if tp_hit and sl_hit: outcome = "ambiguous"; break
            if tp_hit:            outcome = "tp";        break
            if sl_hit:            outcome = "sl";        break

        if outcome == "tp":
            wins += 1;   total_r += actual_rr
        elif outcome == "sl":
            losses += 1; total_r -= 1.0
        elif outcome == "ambiguous":
            ambig += 1;  total_r -= 1.0
        else:
            open_ += 1

    resolved = wins + losses + ambig
    return {
        "fires": fires, "wins": wins, "losses": losses, "ambig": ambig, "open": open_,
        "win_rate":   round(wins / resolved, 3) if resolved > 0 else None,
        "expectancy": round(total_r / resolved, 3) if resolved > 0 else None,
        "total_r":    round(total_r, 2),
    }


def _agg(per_sym: dict) -> dict:
    w  = sum(r["wins"]    for r in per_sym.values())
    l  = sum(r["losses"]  for r in per_sym.values())
    a  = sum(r["ambig"]   for r in per_sym.values())
    f  = sum(r["fires"]   for r in per_sym.values())
    tr = sum(r["total_r"] for r in per_sym.values())
    res = w + l + a
    return {
        "fires": f, "wins": w, "losses": l,
        "win_rate":   round(w / res,  3) if res > 0 else None,
        "expectancy": round(tr / res, 3) if res > 0 else None,
        "total_r":    round(tr, 2),
    }


def _fmt(val, fmt="+.3f"):
    return f"{val:{fmt}}" if val is not None else "  n/a"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    needed = WARMUP + FOLD_BARS * N_FOLDS
    print(f"Bars needed per symbol: {needed}  (~{needed / 24 / 30.44:.1f} months at 24h/day)", file=sys.stderr)

    from datetime import date, timedelta

    # Fetch 3 non-overlapping ~3-month windows then concat.
    # period="730d" hits Yahoo's exact limit and fails intermittently — use
    # explicit start/end with a small safety margin instead.
    today = date.today()
    windows = [
        (today - timedelta(days=270), today - timedelta(days=180)),
        (today - timedelta(days=180), today - timedelta(days=90)),
        (today - timedelta(days=90),  today),
    ]
    # Add warmup to the earliest window
    fetch_start = today - timedelta(days=600)   # gold trades ~16h/day so needs ~580 calendar days for 9 months of bars
    fetch_end   = today

    print(f"Fetching 1H data {fetch_start} -> {fetch_end}...", file=sys.stderr)
    raw = {}
    for sym in SYMBOLS:
        print(f"  {sym['display']}...", file=sys.stderr)
        df = yf.download(sym["yf_ticker"], interval="1h",
                         start=fetch_start.isoformat(), end=fetch_end.isoformat(),
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            try: df.columns = df.columns.droplevel(1)
            except Exception: pass
        raw[sym["display"]] = df
        print(f"    -> {len(df)} bars", file=sys.stderr)

    summary = []

    for config in CONFIGS:
        name = config["name"]
        print(f"\nCV: {name}...", file=sys.stderr)
        frames = _build_frames(raw, config, needed)

        param_str = ", ".join(f"{k}={v}" for k, v in config.items() if k not in ("name", "key"))
        print(f"\n{'=' * 76}")
        print(f"  {name}  [{param_str}]")
        print(f"{'=' * 76}")
        print(f"  {'':6} {'Period':<22} {'Fires':>6} {'W':>4} {'L':>4} "
              f"{'WinRate':>8} {'Exp/R':>7} {'TotR':>6}  {'XAU':>7}  {'BTC':>7}")
        print(f"  {'-' * 82}")

        fold_exps   = []
        fold_fires  = []
        fold_wrates = []
        for fold_idx in range(N_FOLDS):
            fold_start = WARMUP + fold_idx * FOLD_BARS
            fold_end   = fold_start + FOLD_BARS

            # Date range — use XAUUSD as calendar reference
            ref = frames[SYMBOLS[0]["display"]]
            t0 = ref.index[fold_start].strftime("%m/%d/%y") if fold_start < len(ref) else "?"
            t1 = ref.index[min(fold_end - 1, len(ref) - 1)].strftime("%m/%d/%y")
            period_str = f"{t0} - {t1}"

            per_sym = {
                sym["display"]: run_fold(frames[sym["display"]], fold_start, fold_end, config)
                for sym in SYMBOLS
            }
            agg = _agg(per_sym)
            fold_exps.append(agg["expectancy"])
            fold_fires.append(agg["fires"])
            fold_wrates.append(agg["win_rate"])

            sym_cols = "  ".join(_fmt(per_sym[s["display"]]["expectancy"]) for s in SYMBOLS)
            print(f"  F{fold_idx + 1}     {period_str:<22} {agg['fires']:>6} {agg['wins']:>4} "
                  f"{agg['losses']:>4} {_fmt(agg['win_rate'], '.3f'):>8} "
                  f"{_fmt(agg['expectancy']):>7} {agg['total_r']:>6.1f}  {sym_cols}")

        valid_exp = [e for e in fold_exps if e is not None]
        avg   = round(sum(valid_exp) / len(valid_exp), 3) if valid_exp else None
        n_pos = sum(1 for e in valid_exp if e > 0)
        avg_fires  = round(sum(fold_fires)  / len(fold_fires))
        valid_wr   = [w for w in fold_wrates if w is not None]
        avg_wr     = round(sum(valid_wr) / len(valid_wr), 3) if valid_wr else None
        print(f"  {'AVG':<30} {'':>6} {'':>4} {'':>4} {'':>8} "
              f"{_fmt(avg):>7}         [{n_pos}/{len(valid_exp)} folds positive]")
        summary.append({
            "name": name, "avg": avg, "n_pos": n_pos, "folds": fold_exps,
            "avg_fires": avg_fires, "avg_wr": avg_wr,
            "fold_fires": fold_fires, "fold_wrates": fold_wrates,
        })

    summary.sort(key=lambda x: (x["avg"] or -99), reverse=True)

    print(f"\n{'=' * 96}")
    print("CROSS-VALIDATION SUMMARY  (9 months  |  3 x ~3-month folds)")
    print(f"{'=' * 96}")
    print(f"  {'Strategy':<14}  "
          f"{'F1':>16}  {'F2':>16}  {'F3':>16}  "
          f"{'AvgExp':>7}  {'AvgWR':>6}  {'Fires/F':>7}  Consistent")
    print(f"  {'-' * 92}")
    for row in summary:
        fold_cols = "  ".join(
            f"{_fmt(e):>7} {_fmt(w, '.3f'):>6}"
            for e, w in zip(row["folds"], row["fold_wrates"])
        )
        print(f"  {row['name']:<14}  {fold_cols}  "
              f"{_fmt(row['avg']):>7}  {_fmt(row['avg_wr'], '.3f'):>6}  "
              f"{row['avg_fires']:>7}  "
              f"{row['n_pos']}/{len(row['folds'])}+")
    print()


if __name__ == "__main__":
    main()
