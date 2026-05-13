# Build Progress

Tracking checklist for the Oscar Discord bot. See `plan.md` for the full design.

---

## Status: ✅ All 7 steps complete — bot live

Last updated: 2026-05-13 20:40 (local)

---

## Step 1 — Project scaffolding ✅

- [x] Create `.gitignore` (ignore `.env`, `logs/`, `screenshots/`, `state/signals.json`)
- [x] Create `.env.example` with `DISCORD_WEBHOOK_URL=` + `ACCOUNT_SIZE_USD=` placeholders
- [x] Create `.env` (user pasted real webhook URL)
- [x] Create empty dirs: `scripts/`, `prompts/`, `state/`, `logs/`, `screenshots/`

## Step 2 — Discord delivery ✅

- [x] Write `scripts/send-discord.ps1`
  - Loads `.env` from project root
  - Accepts `-Content`, `-Embed` (hashtable), `-ImagePath` (optional)
  - Plain JSON post OR manual multipart/form-data with attachment (PS 5.1 compatible)
- [x] Smoke test: "Hello from Oscar" delivered to Discord channel

## Step 3 — Daily strategic briefing + watchlist ✅

Daily was upgraded from a plain 1D watchlist read to a **Strategic Analyst briefing** (dynamic 1–7 stories across markets / tech / sysengr) **plus** the original 3-symbol watchlist.

- [x] Write `prompts/daily-update.md`
  - Strategic Analyst role: dynamic story count, dense 3-line format (pulse / footprint / so-what)
  - Market stories require real chart-read FVG + liquidity levels via TradingView MCP
  - Source URL required per story (from WebSearch / WebFetch)
  - Top market story captures `image_path` via `capture_screenshot`
  - Watchlist read on 1D for XAU / BTC / EUR with EMA50/200, RSI, MACD
- [x] Write `scripts/daily-update.ps1`
  - Invokes `claude -p` headless with prompt
  - Parses fenced ```json block from output
  - Embed 1: briefing — dense format, `[src](url)` per story, headline screenshot attached as embed image
  - Embed 2: watchlist — 3 fields, color by trend majority
  - Logs to `logs/daily-YYYY-MM-DD.log`
- [x] Manual run 1 (initial design, watchlist only) — Discord output verified
- [x] Manual run 2 (Strategic Briefing version) — 3 dynamic stories + watchlist posted; ~7 min runtime

## Step 4 — Schedule daily task ✅

- [x] Register `OscarDaily` in Windows Task Scheduler
  - Trigger: daily at 08:00 local · `StartWhenAvailable` · 30-min timeout · `RestartCount 0`
  - Action: `powershell.exe -NonInteractive -ExecutionPolicy Bypass -File scripts\daily-update.ps1`
  - Runs as current user (no admin, no password prompt)
  - Next run: 2026-05-14 08:00
- [x] Manual trigger 1 (15:45) — **FAILED** (`LastResult: 1`). Root cause: PowerShell read `claude -p` stdout as Windows-1252 in non-interactive mode, mangling em-dashes (`–` → `ΓÇô`); the corrupted body broke the second Discord POST.
- [x] Fixes applied:
  - `daily-update.ps1`: force UTF-8 via `[Console]::OutputEncoding`, `$OutputEncoding`, `chcp 65001` at script start
  - `send-discord.ps1`: encode JSON body as UTF-8 bytes and set `ContentType: application/json; charset=utf-8` (PS 5.1 `Invoke-RestMethod` otherwise defaults to ISO-8859-1)
  - `daily-update.ps1`: wrap both Discord posts in try/catch + `Write-Log` so future failures surface in the log
- [x] Manual trigger 2 (15:53) — **PASS** (`LastResult: 0`); 4 stories + watchlist posted, full run logged through `=== Daily run complete ===`

## Step 5 — Hourly signal scan

- [x] Write `prompts/hourly-scan.md`
  - Iterate 3 symbols on 1H
  - Read EMA50/200, RSI(14), MACD, ATR(14)
  - Apply rules.json default signal logic
  - On fire: draw `long_position` / `short_position` via `draw_shape` (**offsets in pricescale units**, not absolute prices) then `capture_screenshot`
  - For each symbol emit `{ signal: "long" | "short" | "none", entry, sl, tp, drawing_id, screenshot_path, ... }`
- [x] Write `scripts/hourly-scan.ps1`
  - Pre-flight: prompt instructs claude to call `tv_health_check` first (returns `error: tv_not_connected` → script exits silently)
  - Invoke `claude -p` with prompt file
  - For each fired signal: check `state/signals.json` dedupe rules (4h same-direction lockout, SL-break invalidates, flip-side fires immediately)
  - Screenshot is captured inline by claude during the scan; path arrives in JSON
  - Build embed, post to Discord with image attachment
  - Update `state/signals.json`
  - Write log to `logs/hourly-YYYY-MM-DD-HH.log`
- [ ] Manual run during a known setup — verify signal posts with chart image (waiting for organic fire)
- [x] Manual run during no-setup — verified silent (0 posted, 0 skipped on 2026-05-13 14:32; all 3 symbols returned `none` with reasons)
- [x] Synthetic-signal Discord embed format verified — `[TEST] ▲ BTCUSD 1H - LONG` posted with chart image (2026-05-13 14:46)
- [x] `long_position` / `short_position` drawings verified on chart — confirmed `stopLevel`/`profitLevel` are **offsets** (price diff × pricescale), not absolute prices. Prompt updated to compute deltas. See `memory/feedback_tradingview_position_overrides.md`.

## Step 6 — Schedule hourly task

- [x] Register `OscarHourly` in Windows Task Scheduler
  - Trigger: `Once` at next :05 with `RepetitionInterval = PT1H` (repeats indefinitely)
  - First run: 2026-05-13 16:05 local
  - Action: `powershell.exe -ExecutionPolicy Bypass -NoProfile -File scripts\hourly-scan.ps1`
  - Settings: WakeToRun · StartWhenAvailable · AllowStartIfOnBatteries · MultipleInstances=IgnoreNew · 10-min timeout · 1 retry after 2 min
  - Principal: current user, Interactive logon, Limited run level (no UAC needed)
- [x] Observed 5 cycles fired on schedule (16:05, 17:05, 18:05, 19:05, 20:05). 20:05 ran cleanly (0 posted). Dedupe path **not exercised** because no signal fired in any cycle.
- [x] Runs 16:05–19:05 (4 cycles) **failed** with "You've hit your limit · resets 7:50pm" from `claude -p` instead of JSON. Script threw on JSON parse and exited. See new issue in parking lot.

## Step 7 — Documentation ✅

- [x] Update `CLAUDE.md`:
  - Rewrote opening to describe the Discord bot alongside the video project
  - Added "Oscar Discord Bot — Architecture" section: scheduled tasks table, common script behaviors (UTF-8, rate-limit guard, error logging), state/dedupe rules, `.env` location, TV-required note
  - Added "File layout" tree
  - Replaced inlined Start-Process command with reference to `.\start-tv.ps1`
  - Updated Setup State table: added Discord webhook, scheduled tasks, expanded permissions to include `WebSearch`/`WebFetch`
  - Removed stale reference to `Claude + TradingView Prompt.md` from Key Files
  - Added position-drawing offset gotcha to MCP shortcuts
  - Added 4 new troubleshooting entries (scheduled-task exit 1, mojibake, partial-embed failure, MSIX path update)
- [x] Mark this `progress.md` as complete (all steps now ✅)

---

## Open issues / parking lot

- ~~**NEW (2026-05-13 20:24):** `hourly-scan.ps1` doesn't handle `claude -p` rate-limit responses~~ — **fixed 2026-05-13**: added regex guard `(hit your limit|limit\s*[-·]\s*resets|usage limit|rate.?limit)` before the JSON parse; matches → `Write-Log` + `return` (silent exit, no Discord post). Same guard applied to `daily-update.ps1` for symmetry.
- ~~**NEW (2026-05-13 20:24):** `hourly-scan.ps1` is missing the UTF-8 encoding fix~~ — already present (lines 10–14 of the script). The mojibake in the log files was from earlier runs *before* the UTF-8 block was added. No code change needed.
- TV not running when task fires → currently logs + exits. Consider: auto-launch via `start-tv.ps1` and retry once.
- Position size math assumes account size — should it live in `rules.json` or be a `.env` var?
- Screenshots: clean up `screenshots/` older than 7 days? (cron-style cleanup task)
- Multiple watchlists in the future (crypto-only, forex-only)? Currently hardcoded to the 3 in `rules.json`.
- Daily run takes ~5–10 min due to web search + chart reads. If we want to cut this, drop chart-reads for non-headline stories (currently all market stories chart-read).
- ~~Compact briefing format (image + sources) implemented but **not yet test-run** end-to-end~~ — verified end-to-end both interactively and via Task Scheduler on 2026-05-13.
