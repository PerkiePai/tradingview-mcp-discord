# Oscar Discord Bot — Project Plan

A Discord bot that delivers a daily watchlist update and hourly 1H trade signals for `XAUUSD` and `BTCUSD`, driven by the TradingView MCP server.

---

## Architecture

**MCP + Windows Task Scheduler.** Two scheduled tasks fire `claude -p` in non-interactive mode. Claude uses the `mcp__tradingview__*` tools to read the live TradingView Desktop chart, then a PowerShell wrapper posts results to a Discord webhook.

**Requires:** PC awake, TradingView Desktop running with `--remote-debugging-port=9222`, and the MCP server attached. If TV is down, the script logs and exits — no Discord post.

---

## File layout

```
tradingview-mcp-discord/
├── CLAUDE.md
├── plan.md                   # this file
├── progress.md               # build checklist
├── start-tv.ps1
├── .env                      # DISCORD_WEBHOOK_URL (gitignored)
├── .env.example
├── .gitignore
├── scripts/
│   ├── daily-update.ps1      # entry: daily task at 08:00
│   ├── hourly-scan.ps1       # entry: hourly task at :05
│   └── send-discord.ps1      # shared: POST text + optional PNG
├── prompts/
│   ├── daily-update.md       # prompt body for claude -p
│   └── hourly-scan.md        # prompt body for claude -p
├── state/
│   └── signals.json          # dedupe: last signal per symbol
├── logs/                     # one file per run, dated
└── screenshots/              # temp, deleted after Discord upload
```

---

## How a run works

Each `.ps1` entry script does four things:

1. **Pre-flight** — load `.env`, call `tv_health_check` over CDP. If TV is down, log and exit.
2. **Invoke Claude** — `claude -p "$(Get-Content prompts/<name>.md)"`. Claude uses MCP tools and emits structured JSON to stdout.
3. **Parse + dedupe** — read JSON, compare against `state/signals.json` to skip already-posted signals.
4. **Post to Discord** — call `send-discord.ps1` with the embed (and screenshot if applicable).

---

## Decisions (locked)

| Decision | Choice |
|---|---|
| Architecture | MCP + Windows Task Scheduler |
| Discord delivery | Webhook URL |
| Daily run time | 08:00 local |
| Hourly window | 24/7 |
| Signal strategy | rules.json default (see below) |
| Webhook ready | Yes |

---

## Task A — `OscarDaily`

**Schedule:** Daily at 08:00 local.

**For each symbol in `[EIGHTCAP:XAUUSD, EIGHTCAP:BTCUSD]`:**
- Switch chart to symbol, 1D timeframe
- `quote_get` → last, day change %
- `data_get_study_values` → RSI(14), MACD, EMA50, EMA200
- Compose a one-line read

**Output:** one Discord embed with three fields (one per symbol). Color = green if 2/3 trending up, red if 2/3 down, gray otherwise.

**Example:**
```
📊 Oscar Daily — 2026-05-13
─────────────────────────
XAUUSD   2,331 (+0.4%)   Trend ↑   RSI 54
BTCUSD   67,420 (+1.2%)  Trend ↑   RSI 58
```

---

## Task B — `OscarHourly`

**Schedule:** Every hour at :05 (gives the 1H candle time to print).

**For each symbol:**
- Switch chart to symbol, 1H
- Read EMA50, EMA200, RSI(14), MACD, ATR(14)
- Apply signal rules
- **If signal triggers:** compute entry/SL/TP, `capture_screenshot` region=`chart`, post embed + image
- **If no signal:** silent (no Discord post — don't spam hourly)

### Signal logic (rules.json default)

**Long:**
- `close > EMA50 > EMA200` (trend filter)
- `RSI(14)` crosses up from below 45 through 50 on the last closed bar
- `MACD` histogram turns positive on the last closed bar
- Entry = close of signal bar
- SL = entry − 1.5 × ATR(14)
- TP = entry + 2 × (entry − SL) → **RR 2:1**
- Risk note: "1% risk = $X at this SL distance"

**Short:** mirror.

**No signal:** any condition fails → skip.

**Example output (with chart screenshot attached):**
```
🟢 BTCUSD 1H — LONG
─────────────────────
Entry  67,420
SL     66,510   (-1.5×ATR)
TP     69,240   (+RR 2:1)
Risk   1% = $100 / 0.0015 BTC
Trigger: RSI 50 cross + MACD bullish, above 50/200 EMA
```

---

## Dedupe state — `state/signals.json`

Prevents re-firing the same signal every hour while conditions still hold.

```json
{
  "EIGHTCAP:BTCUSD": {
    "last_direction": "long",
    "fired_at": "2026-05-13T15:05:00Z",
    "entry": 67420,
    "invalidated_at": null
  }
}
```

**Rules:**
- Don't re-fire same direction for same symbol within 4 hours
- Re-fire allowed if price breaks the previous SL (signal invalidated)
- Flip-side signal fires immediately (a fresh short cancels a stale long)

---

## Discord webhook

A webhook URL posts to one channel. No bot account, no OAuth.

**Loading the URL:**
```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^([^=]+)=(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] }
}
```

**Posting text:**
```powershell
$body = @{ content = "..." } | ConvertTo-Json
Invoke-RestMethod -Uri $env:DISCORD_WEBHOOK_URL -Method Post -Body $body -ContentType 'application/json'
```

**Posting with embed + screenshot:** multipart form-data — `send-discord.ps1` wraps this.

**Limits:** 30 messages/minute per webhook, 8 MB attachments. Both comfortable for this use case.

---

## Build order

1. `.gitignore` + `.env.example` — paste real URL into `.env`
2. `send-discord.ps1` — smoke test with "hello from Oscar"
3. `prompts/daily-update.md` + `scripts/daily-update.ps1` — manual run, verify output
4. Register `OscarDaily` in Task Scheduler (08:00 daily)
5. `prompts/hourly-scan.md` + `scripts/hourly-scan.ps1` + dedupe state
6. Register `OscarHourly` in Task Scheduler (every hour at :05)
7. Update `CLAUDE.md` with new structure
