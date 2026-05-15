#!/usr/bin/env python3
"""Oscar hourly 1H signal scanner — yfinance edition. No Claude / MCP needed."""

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from strategies import compute_base_indicators, compute_supertrend, signal_supertrend, sl_tp_from_signal

ROOT       = Path(__file__).parent.parent
STATE_FILE = ROOT / "state" / "signals.json"
LOG_DIR    = ROOT / "logs"

CACHE_TTL = 55 * 60  # seconds — safe within one 1H bar

SYMBOLS = [
    {"yf_ticker": "GC=F",    "display": "XAUUSD"},
    {"yf_ticker": "BTC-USD", "display": "BTCUSD"},
]

ATR_MULT  = 1.0
RR        = 1.5
ST_PERIOD = 10
ST_MULT   = 2.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    line = f"{datetime.now().astimezone().isoformat()} {msg}"
    print(line, flush=True)
    log_file = LOG_DIR / f"hourly-{datetime.now().strftime('%Y-%m-%d-%H')}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def load_env() -> dict:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return {}
    env = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _cache_path(display: str) -> Path:
    return ROOT / "state" / f"yf-cache-{display}.json"


def load_cached_df(display: str) -> "pd.DataFrame | None":
    path = _cache_path(display)
    if not path.exists():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age > CACHE_TTL:
            return None
        df = pd.read_json(io.StringIO(meta["data"]), orient="split")
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    except Exception:
        return None


def save_cached_df(display: str, df: "pd.DataFrame") -> None:
    (ROOT / "state").mkdir(exist_ok=True)
    _cache_path(display).write_text(
        json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "data": df.to_json(orient="split", date_format="iso"),
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def post_discord(webhook_url: str, embed: dict) -> None:
    resp = requests.post(
        webhook_url,
        json={"embeds": [embed]},
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=10,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_symbol(yf_ticker: str, display: str) -> dict:
    df = load_cached_df(display)
    if df is not None:
        log(f"{display}: cache hit ({CACHE_TTL // 60}m TTL)")
    else:
        df = yf.download(yf_ticker, interval="1h", period="30d", progress=False, auto_adjust=True)
        save_cached_df(display, df)
        log(f"{display}: fetched from yfinance, cached")

    if df is None or len(df) < 210:
        return {"symbol": display, "signal": "none", "reason": "Insufficient data"}

    # Flatten multi-level columns yfinance sometimes returns
    if isinstance(df.columns, type(df.columns)) and hasattr(df.columns, "droplevel"):
        try:
            df.columns = df.columns.droplevel(1)
        except Exception:
            pass

    df = compute_base_indicators(df)
    df = compute_supertrend(df, ST_PERIOD, ST_MULT).dropna()

    # Use last two fully closed bars (iloc[-1] = most recently closed)
    prev = df.iloc[-2]
    last = df.iloc[-1]
    i    = len(df) - 1

    price   = round(float(last["Close"]),     2)
    ema50   = round(float(last["ema50"]),      2)
    ema200  = round(float(last["ema200"]),     2)
    rsi_l   = round(float(last["rsi"]),        2)
    rsi_p   = round(float(prev["rsi"]),        2)
    hist_l  = round(float(last["macd_hist"]),  4)
    hist_p  = round(float(prev["macd_hist"]),  4)
    atr_val = round(float(last["atr"]),        2)

    base = {
        "symbol":        display,
        "current_price": price,
        "ema50":         ema50,
        "ema200":        ema200,
        "rsi":           rsi_l,
        "macd_hist":     hist_l,
        "atr":           atr_val,
    }

    st_dir  = int(df["st_dir"].iloc[-1])
    st_line = round(float(df["st_line"].iloc[-1]), 2)

    direction = signal_supertrend(df, i)

    if direction == "long":
        sl, tp = sl_tp_from_signal("long",  price, atr_val, ATR_MULT, RR)
        return {
            **base,
            "signal": "long", "entry": price, "sl": round(sl, 2), "tp": round(tp, 2), "rr": RR,
            "trigger_note": f"Supertrend bullish (line={st_line}), RSI cross 50↑ ({rsi_p}→{rsi_l}), MACD hist flipped +",
        }

    if direction == "short":
        sl, tp = sl_tp_from_signal("short", price, atr_val, ATR_MULT, RR)
        return {
            **base,
            "signal": "short", "entry": price, "sl": round(sl, 2), "tp": round(tp, 2), "rr": RR,
            "trigger_note": f"Supertrend bearish (line={st_line}), RSI cross 50↓ ({rsi_p}→{rsi_l}), MACD hist flipped -",
        }

    # Build reason for no-signal
    trend = "bullish" if st_dir == 1 else "bearish"
    reason = f"Supertrend {trend} (line={st_line}) — RSI prev={rsi_p} last={rsi_l}, MACD hist prev={hist_p} last={hist_l}: cross not confirmed"

    return {**base, "signal": "none", "reason": reason}


def build_embed(sig: dict) -> dict:
    is_long = sig["signal"] == "long"
    return {
        "title":  f"{'▲' if is_long else '▼'} {sig['symbol']} 1H — {'LONG' if is_long else 'SHORT'}",
        "color":  0x2ECC71 if is_long else 0xE74C3C,
        "fields": [
            {"name": "Entry",   "value": str(sig["entry"]),             "inline": True},
            {"name": "SL",      "value": f"{sig['sl']} (1.0xATR)", "inline": True},
            {"name": "TP",      "value": f"{sig['tp']} (RR 1.5:1)",     "inline": True},
            {"name": "Trigger", "value": sig.get("trigger_note", ""),   "inline": False},
        ],
        "footer":    {"text": f"RSI {sig['rsi']} | MACD hist {sig['macd_hist']} | ATR {sig['atr']}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log("=== Hourly scan starting (yfinance) ===")

    env = load_env()
    webhook_url = env.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        log("ERROR: DISCORD_WEBHOOK_URL not set in .env")
        sys.exit(1)

    state   = load_state()
    now_utc = datetime.now(timezone.utc)
    posted  = 0
    skipped = 0

    for sym in SYMBOLS:
        display = sym["display"]
        log(f"{display}: scanning...")

        try:
            sig = scan_symbol(sym["yf_ticker"], display)
        except Exception as e:
            log(f"{display}: ERROR — {e}")
            continue

        if sig["signal"] == "none":
            log(f"{display}: none — {sig.get('reason', '')}")
            continue

        # Dedupe: same direction within 4h unless SL was broken
        prev = state.get(display, {})
        if prev:
            prev_dir = prev.get("last_direction")
            prev_sl  = float(prev.get("sl", 0))
            try:
                prev_fired = datetime.fromisoformat(prev["fired_at"])
                if prev_fired.tzinfo is None:
                    prev_fired = prev_fired.replace(tzinfo=timezone.utc)
            except Exception:
                prev_fired = None

            cp           = sig["current_price"]
            invalidated  = (prev_dir == "long"  and cp <= prev_sl) or \
                           (prev_dir == "short" and cp >= prev_sl)

            if not invalidated and prev_dir == sig["signal"] and prev_fired:
                age_h = (now_utc - prev_fired).total_seconds() / 3600
                if age_h < 4:
                    log(f"{display}: SKIP — same direction fired {age_h:.1f}h ago")
                    skipped += 1
                    continue

        embed = build_embed(sig)
        log(f"{display}: POSTING {sig['signal']} entry={sig['entry']} sl={sig['sl']} tp={sig['tp']}")

        try:
            post_discord(webhook_url, embed)
            posted += 1
            state[display] = {
                "last_direction": sig["signal"],
                "fired_at":       now_utc.isoformat(),
                "entry":          sig["entry"],
                "sl":             sig["sl"],
                "tp":             sig["tp"],
            }
        except Exception as e:
            log(f"{display}: ERROR posting to Discord — {e}")

    save_state(state)
    log(f"=== Scan complete: {posted} posted, {skipped} skipped ===")


if __name__ == "__main__":
    main()
