[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# Force UTF-8 for both PowerShell IO and the claude -p subprocess.
# Without this, Task Scheduler / non-interactive runs use the OEM codepage
# (typically 437) and Unicode dashes / arrows arrive as mojibake (e.g. "ΓÇô" for "–"),
# which can later break Discord embed validation.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING     = 'utf-8'
chcp 65001 | Out-Null

$root = Split-Path $PSScriptRoot -Parent

$logDir  = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir "daily-$(Get-Date -Format 'yyyy-MM-dd').log"

function Write-Log($msg) {
    $line = "$(Get-Date -Format o) $msg"
    Add-Content -Path $logFile -Value $line -Encoding utf8
    Write-Host $line
}

Write-Log "=== Daily run starting ==="

$promptFile = Join-Path $root 'prompts\daily-update.md'
if (-not (Test-Path $promptFile)) { throw "Prompt file missing: $promptFile" }
$prompt = Get-Content $promptFile -Raw

Write-Log "Invoking claude -p --model opus (headless) - this may take several minutes"
$rawOutput = $prompt | claude -p --model opus 2>&1 | Out-String
Write-Log "Claude raw output:`n$rawOutput"

# Detect Claude usage-limit responses before the JSON parse.
if ($rawOutput -match '(?i)(hit your limit|limit\s*[-·]\s*resets|usage limit|rate.?limit)') {
    Write-Log "Rate-limited by Claude API - exiting silently"
    return
}

# Extract JSON from fenced block (allow either {} or any structure)
$match = [regex]::Match($rawOutput, '(?s)```json\s*(\{.*?\})\s*```')
if (-not $match.Success) {
    Write-Log "ERROR: no ```json fenced block found"
    throw "Could not parse JSON from claude output"
}
$jsonText = $match.Groups[1].Value
$data = $jsonText | ConvertFrom-Json

$sendScript = Join-Path $PSScriptRoot 'send-discord.ps1'

function Get-Domain($url) {
    try { ([Uri]$url).Host -replace '^www\.','' } catch { '' }
}

# ---------- Strategic Briefing — header + one embed per story ----------
$catMeta = @{
    market  = @{ emoji = [char]::ConvertFromUtf32(0x1F4C8); color = 0x27AE60; label = 'Market'  }   # 📈 emerald
    tech    = @{ emoji = [char]::ConvertFromUtf32(0x1F916); color = 0x8E44AD; label = 'Tech'    }   # 🤖 purple
    sysengr = @{ emoji = [char]::ConvertFromUtf32(0x1F6E0); color = 0xE67E22; label = 'SysEngr' }   # 🛠 orange
}

# Sort: market first (image likely lives here), then tech, then sysengr
$catOrder = @{ market = 0; tech = 1; sysengr = 2 }
$briefingSorted = @($data.briefing | Sort-Object @{Expression={ $o = $catOrder[$_.category]; if ($null -eq $o) {99} else {$o} }})

$badSources = 0
$counts = @{ market = 0; tech = 0; sysengr = 0 }
foreach ($s in $briefingSorted) {
    if ($counts.ContainsKey($s.category)) { $counts[$s.category]++ }
}

$summaryParts = @()
foreach ($k in 'market','tech','sysengr') {
    if ($counts[$k] -gt 0) {
        $summaryParts += "$($catMeta[$k].emoji) $($counts[$k]) $($catMeta[$k].label.ToLower())"
    }
}
$summary = if ($summaryParts.Count) { $summaryParts -join '  ·  ' } else { 'No high-impact news today' }

# Resolve image path (will live on the first eligible story embed below)
$imgPath = $null
$imgName = $null
if ($data.image_path -and (Test-Path $data.image_path)) {
    $imgPath = $data.image_path
    $imgName = Split-Path $imgPath -Leaf
    Write-Log "Will attach headline screenshot: $imgPath"
} elseif ($data.image_path) {
    Write-Log "WARN: image_path provided but file missing: $($data.image_path)"
}

# Header embed
$dateNice = try { ([DateTime]::Parse($data.date)).ToString('ddd, MMM d yyyy') } catch { $data.date }
$headerEmbed = @{
    title       = "Strategic Briefing"
    description = "**$dateNice**`n$summary"
    color       = 0x34495E   # wet asphalt
    footer      = @{ text = "Oscar · powered by Claude Opus + TradingView MCP" }
}

# Per-story embeds
$storyEmbeds  = @()
$imageUsed    = $false
foreach ($s in $briefingSorted) {
    $meta = $catMeta[$s.category]
    if (-not $meta) { $meta = @{ emoji = '•'; color = 0x95A5A6; label = 'Other' } }

    # Reject TradingView chart URLs as "sources"
    $url = [string]$s.source_url
    if ($url -match 'tradingview\.com') {
        Write-Log "WARN: rejecting TradingView chart as source for: $($s.pulse)"
        $badSources++
        $url = ''
    }

    $title = "$($meta.emoji)  $($s.pulse)"
    if ($title.Length -gt 256) { $title = $title.Substring(0, 253) + '...' }

    $fields = @(
        @{ name = ":mag: Footprint";   value = [string]$s.footprint; inline = $false },
        @{ name = ":dart: So What";    value = [string]$s.so_what;   inline = $false }
    )

    $embed = @{
        title  = $title
        color  = $meta.color
        fields = $fields
    }
    if ($url) {
        $embed.url    = $url
        $embed.footer = @{ text = (Get-Domain $url) }
    } else {
        $embed.footer = @{ text = "no source" }
    }

    # Attach the headline screenshot to the first market story; if no market, the first story
    if ($imgPath -and -not $imageUsed) {
        if ($s.category -eq 'market' -or ($briefingSorted[0] -eq $s -and $null -ne $imgPath)) {
            $embed.image = @{ url = "attachment://$imgName" }
            $imageUsed = $true
            Write-Log "Attaching screenshot to story: $($s.pulse)"
        }
    }

    $storyEmbeds += $embed
}

if ($badSources -gt 0) {
    Write-Log "WARN: $badSources story/stories had no valid news source (chart URL rejected)"
}

# Discord allows up to 10 embeds per message; we have header + N stories.
$allEmbeds = @($headerEmbed) + $storyEmbeds
if ($allEmbeds.Count -gt 10) {
    Write-Log "WARN: $($allEmbeds.Count) embeds requested; trimming to 10 (Discord limit)"
    $allEmbeds = $allEmbeds[0..9]
}

Write-Log "Posting briefing — header + $($storyEmbeds.Count) story embed(s)"
try {
    if ($imgPath -and $imageUsed) {
        & $sendScript -Embeds $allEmbeds -ImagePath $imgPath
    } else {
        & $sendScript -Embeds $allEmbeds
    }
} catch {
    Write-Log "ERROR posting briefing: $($_.Exception.Message)"
    Write-Log "Stack: $($_.ScriptStackTrace)"
    throw
}

# ---------- Watchlist embed ----------
$symbolEmoji = @{
    XAUUSD = [char]::ConvertFromUtf32(0x1F947)   # 🥇
    BTCUSD = [char]::ConvertFromUtf32(0x20BF)    # ₿
}
$trendChip = @{
    up   = ":green_circle: ↑ Up"
    down = ":red_circle: ↓ Down"
    flat = ":white_circle: → Flat"
}
$macdChip = @{
    bullish = ":green_circle: bull"
    bearish = ":red_circle: bear"
    neutral = ":white_circle: neutral"
}

$upCount   = ($data.watchlist | Where-Object { $_.trend -eq 'up' }).Count
$downCount = ($data.watchlist | Where-Object { $_.trend -eq 'down' }).Count
$wlColor =
    if ($upCount -ge ($data.watchlist.Count / 2 + 0.5))   { 0x2ECC71 }   # green
    elseif ($downCount -ge ($data.watchlist.Count / 2 + 0.5)) { 0xE74C3C }   # red
    else                      { 0x95A5A6 }   # gray

$wlFields = @()
foreach ($s in $data.watchlist) {
    $sym   = [string]$s.symbol
    $emoji = $symbolEmoji[$sym]; if (-not $emoji) { $emoji = '•' }
    $sign  = if ($s.change_pct -ge 0) { '+' } else { '' }

    $trend = $trendChip[[string]$s.trend]; if (-not $trend) { $trend = $s.trend }
    $macd  = $macdChip[[string]$s.macd_state]; if (-not $macd) { $macd = $s.macd_state }

    $line1 = "**$($s.price)**  ($sign$($s.change_pct)%)"
    $line2 = "$trend  ·  RSI **$($s.rsi)**  ·  MACD $macd"
    $line3 = $s.note

    $wlFields += @{
        name   = "$emoji  $sym"
        value  = "$line1`n$line2`n$line3"
        inline = $false
    }
}

$watchlistEmbed = @{
    title  = ":bar_chart: Watchlist · 1D"
    color  = $wlColor
    fields = $wlFields
    footer = @{ text = "Daily close · $($data.date)" }
}

Write-Log "Posting watchlist embed ($($data.watchlist.Count) symbols)"
try {
    & $sendScript -Embed $watchlistEmbed
} catch {
    Write-Log "ERROR posting watchlist: $($_.Exception.Message)"
    Write-Log "Stack: $($_.ScriptStackTrace)"
    throw
}

Write-Log "=== Daily run complete ==="
