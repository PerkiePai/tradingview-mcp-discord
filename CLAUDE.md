# Claude + TradingView MCP — Project Overview

Two things live in this directory:

1. **Oscar Discord Bot** — automated daily Strategic Briefing + hourly 1H signal scanner. Posts to a Discord webhook via two Windows scheduled tasks (`OscarDaily` at 08:00 local, `OscarHourly` at every :05). Both invoke `claude -p` headless with the TradingView MCP.
2. **YouTube video production** referencing the same MCP — chart reading, Pine Script generation, alert setting, multi-symbol scanning. AI assistant in the video is **"Oscar"**.

See `plan.md` for the bot's full design and `progress.md` for the build log.

---

## ⚡ Use the TradingView MCP By Default

**This project is built around the `mcp__tradingview__*` toolset. For ANY trading, chart, market data, Pine Script, or indicator question in this directory, use the MCP tools — not web search, not guessing, not Bash workarounds.**

**On session start, do this automatically:**
1. Call `mcp__tradingview__tv_health_check`.
2. If `cdp_connected: false` (or the tool isn't available because TV wasn't running), run `start-tv.ps1` to relaunch with the debug port. If the MCP tool itself is missing from the session (TV wasn't running when Claude Code started), tell the user to run `start-tv.ps1` and restart Claude Code so the MCP server attaches.
3. Once connected, call `mcp__tradingview__chart_get_state` before answering anything chart-related.

**Tool-picking shortcuts** (full guide in the MCP server's instructions):
- Price snapshot → `quote_get`
- Bars / OHLCV → `data_get_ohlcv` (always pass `summary=true` unless you need raw bars)
- Indicator values (RSI/MACD/EMA/etc.) → `data_get_study_values`
- Custom Pine output (lines/labels/tables/boxes) → `data_get_pine_*` with `study_filter="<indicator name>"`
- Add/remove studies → `chart_manage_indicator` with the **full** indicator name (e.g. "Relative Strength Index", not "RSI")
- Change ticker / timeframe → `chart_set_symbol`, `chart_set_timeframe`
- Pine dev → `pine_set_source` → `pine_smart_compile` → `pine_get_errors`
- Screenshot → `capture_screenshot` (regions: `full`, `chart`, `strategy_tester`)
- Position drawing → `draw_shape` with `long_position` / `short_position`. **`stopLevel` and `profitLevel` are offsets in pricescale units, not absolute prices** — see `memory/feedback_tradingview_position_overrides.md`.

**Do NOT** call `pine_get_source` unless actively editing — it can return 200KB+.

---

## Oscar Discord Bot — Architecture

Two Windows scheduled tasks, both running as the current user:

| Task | Trigger | Entry script | Prompt | What it does |
|---|---|---|---|---|
| `OscarDaily` | Daily 08:00 local | `scripts/daily-update.ps1` | `prompts/daily-update.md` | WebSearch → top 1–7 news stories across markets/tech/sysengr → chart-read FVG + liquidity for market stories → 1D watchlist read. Posts 2 Discord embeds. |
| `OscarHourly` | Every hour at :05 | `scripts/hourly-scan.ps1` | `prompts/hourly-scan.md` | 1H scan of XAU/BTC/EUR. RSI(14) cross + MACD + 50/200 EMA trend. On fire: draws `long_position`/`short_position`, captures screenshot, posts to Discord. Silent when no setup. |

**Both scripts:**
- Invoke `claude -p` headless and parse a fenced ```json block from stdout
- Force UTF-8 IO (`[Console]::OutputEncoding`, `chcp 65001`) — without this, Task Scheduler runs corrupt en-dashes/arrows and break Discord embeds
- Guard against `claude -p` rate-limit responses (regex on `"hit your limit"` etc.) → silent `return` so the next cycle retries
- Log to `logs/{daily|hourly}-YYYY-MM-DD[-HH].log`
- Use `scripts/send-discord.ps1` for delivery (handles plain JSON post or manual multipart with PNG attachment, PS 5.1 compatible)

**State:** `state/signals.json` tracks last fired signal per symbol for hourly dedupe (same direction locked out 4h unless SL breaks; flip-side fires immediately).

**Discord webhook:** stored in `.env` as `DISCORD_WEBHOOK_URL`. `.env` is gitignored. `.env.example` has the template.

**TV must be running** for either task to succeed. The scripts log + exit silently if `tv_health_check` fails — no Discord noise.

---

## File layout

```
tradingview-mcp-discord/
├── CLAUDE.md              # this file
├── plan.md                # bot design
├── progress.md            # build checklist + incident log
├── start-tv.ps1           # launches TV with --remote-debugging-port=9222
├── .env                   # DISCORD_WEBHOOK_URL (gitignored)
├── .env.example
├── .gitignore
├── scripts/
│   ├── daily-update.ps1
│   ├── hourly-scan.ps1
│   └── send-discord.ps1
├── prompts/
│   ├── daily-update.md
│   └── hourly-scan.md
├── state/
│   └── signals.json       # hourly dedupe (gitignored)
├── logs/                  # per-run logs (gitignored)
└── screenshots/           # chart PNGs captured by claude (gitignored)
```

---

## Setup State (as of 2026-05-13) — Complete

| Component | Status | Location |
|---|---|---|
| MCP server | Installed | `C:\Users\bower\tradingview-mcp\` |
| MCP config | Configured | `~/.claude/mcp.json` |
| Tool permissions | Pre-approved (`mcp__tradingview__*`, `WebSearch`, `WebFetch`) | `~/.claude/settings.json` |
| Trading rules | Created | `C:\Users\bower\tradingview-mcp\rules.json` |
| TradingView Desktop | Installed (v3.1.0) | `C:\Program Files\WindowsApps\TradingView.Desktop_3.1.0.7818_x64__n534cwy3pjxzj\` |
| Discord webhook | Configured | `.env` (this dir) |
| Scheduled tasks | Registered | `OscarDaily`, `OscarHourly` |

**MCP tools load on session start** — TradingView must be running with `--remote-debugging-port=9222` before Claude Code is opened, otherwise the tools won't appear.

---

## Launching TradingView

Run `.\start-tv.ps1` from this directory. It wraps the MSIX-packaged Start-Process call.

Verify CDP is up: `http://localhost:9222/json/version` should return JSON with `TradingView/3.1.0` in the User-Agent. Or just call `mcp__tradingview__tv_health_check`.

---

## Session Startup Checklist

1. Run `.\start-tv.ps1`
2. Open Claude Code
3. Run `tv_health_check` — expect `cdp_connected: true`
4. If `cdp_connected: false`, port 9222 isn't open — relaunch TV and retry

---

## MCP Server

- **Repo:** https://github.com/tradesdontlie/tradingview-mcp (by @tradesdontlie)
- **Entry point:** `C:\Users\bower\tradingview-mcp\src\server.js`
- **Transport:** stdio (Claude Code spawns it via `node`)
- **Protocol:** Chrome DevTools Protocol on `localhost:9222`
- **78 tools** covering: chart state, quotes, OHLCV, Pine Script, indicators, alerts, drawings, watchlists, replay, screenshots, tabs, panes

---

## Trading Rules (`C:\Users\bower\tradingview-mcp\rules.json`)

```json
{
  "watchlist": ["EIGHTCAP:XAUUSD", "EIGHTCAP:BTCUSD"],
  "timeframes_to_check": ["1W", "1D", "4H"],
  "risk_rules": {
    "max_risk_per_trade": "1% of portfolio",
    "min_rr_ratio": 2,
    "no_trades_during": ["major US CPI", "FOMC", "weekend thin liquidity"]
  },
  "indicators_i_care_about": ["RSI (14)", "MACD (12, 26, 9)", "50 EMA", "200 EMA", "Volume"]
}
```

---

## Video Structure

The video has 10 sections. Key demo sections:

| Section | Demo | Prompt focus |
|---|---|---|
| 4 | Setup | Master install prompt |
| 5 | The Analyst | Read BTC chart, find setups, draw on chart |
| 6 | The Builder | Generate + inject Pine Script indicator |
| 7 | The Assistant | Entry / stop / target from live chart |
| 8 | The Automator | Scan 10 majors, set alerts, screenshot setups |
| 10 | Phone finale | Telegram voice prompts ("Oscar, read BTC for me") |

The AI assistant in the video is referred to as **"Oscar"**.

---

## Troubleshooting

- **MCP tools missing from session:** TradingView wasn't running when Claude Code started. Run `.\start-tv.ps1`, then restart Claude Code.
- **`cdp_connected: false`:** Port 9222 not open. Run `.\start-tv.ps1` again.
- **Tools not pre-approved:** Check `~/.claude/settings.json` has `mcp__tradingview__*`, `WebSearch`, and `WebFetch` in `permissions.allow`.
- **MSIX launch fails:** The exe path contains the TV version. If TV updates, re-run `Get-AppxPackage -Name "TradingView.Desktop"` to get the new `InstallLocation`, then update `start-tv.ps1`.
- **Scheduled task `LastResult: 1`:** Open `logs/{daily|hourly}-YYYY-MM-DD[-HH].log`. The two most common causes are (a) Claude API rate-limited — should be silently handled now via the regex guard, (b) TV not running — also silently handled. Anything else will have a stack trace in the log thanks to the try/catch around Discord posts.
- **Mojibake in Discord embed (`ΓÇô` instead of `–`):** The UTF-8 block at the top of `daily-update.ps1` / `hourly-scan.ps1` got removed. Restore the `[Console]::OutputEncoding` + `chcp 65001` lines.
- **Discord embed fails on watchlist but briefing posted:** `send-discord.ps1` is missing the `charset=utf-8` in ContentType, or not encoding the body as UTF-8 bytes — PS 5.1's `Invoke-RestMethod` defaults to ISO-8859-1 for string bodies.
