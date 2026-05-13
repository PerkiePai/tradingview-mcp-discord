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
$logFile = Join-Path $logDir "hourly-$(Get-Date -Format 'yyyy-MM-dd-HH').log"

$stateDir  = Join-Path $root 'state'
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir | Out-Null }
$stateFile = Join-Path $stateDir 'signals.json'

function Write-Log($msg) {
    $line = "$(Get-Date -Format o) $msg"
    Add-Content -Path $logFile -Value $line -Encoding utf8
    Write-Host $line
}

Write-Log "=== Hourly run starting ==="

# ---------- Load dedupe state ----------
$state = @{}
if (Test-Path $stateFile) {
    try {
        $raw = Get-Content $stateFile -Raw
        if ($raw.Trim()) {
            $parsed = $raw | ConvertFrom-Json
            foreach ($p in $parsed.PSObject.Properties) {
                $state[$p.Name] = $p.Value
            }
        }
    } catch {
        Write-Log "WARN: could not parse $stateFile ($($_.Exception.Message)) - starting empty"
    }
}

# ---------- Invoke Claude ----------
$promptFile = Join-Path $root 'prompts\hourly-scan.md'
if (-not (Test-Path $promptFile)) { throw "Prompt file missing: $promptFile" }
$prompt = Get-Content $promptFile -Raw

Write-Log "Invoking claude -p (headless)"
$rawOutput = $prompt | claude -p 2>&1 | Out-String
Write-Log "Claude raw output:`n$rawOutput"

# Detect Claude usage-limit responses before the JSON parse. When the API is
# throttled, claude -p prints a plain-text message like
# "You've hit your limit - resets 7:50pm" instead of JSON, and the regex below
# would throw. For scheduled hourly runs we exit silently so the next cycle
# tries again on its own.
if ($rawOutput -match '(?i)(hit your limit|limit\s*[-·]\s*resets|usage limit|rate.?limit)') {
    Write-Log "Rate-limited by Claude API - exiting silently (will retry next cycle)"
    return
}

$match = [regex]::Match($rawOutput, '(?s)```json\s*(\{.*?\})\s*```')
if (-not $match.Success) {
    Write-Log "ERROR: no ```json fenced block found"
    throw "Could not parse JSON from claude output"
}
$data = $match.Groups[1].Value | ConvertFrom-Json

if ($data.error) {
    Write-Log "Scanner reported error: $($data.error) - exiting silently"
    return
}

$sendScript = Join-Path $PSScriptRoot 'send-discord.ps1'
$nowUtc     = [DateTime]::UtcNow

# ---------- Process signals ----------
$posted  = 0
$skipped = 0

foreach ($sig in $data.signals) {
    $symbol = $sig.symbol
    if (-not $symbol) { continue }

    if ($sig.signal -eq 'none') {
        Write-Log "${symbol}: none - $($sig.reason)"
        continue
    }

    if ($sig.signal -ne 'long' -and $sig.signal -ne 'short') {
        Write-Log "${symbol}: unknown signal value '$($sig.signal)' - skipping"
        continue
    }

    # ----- Dedupe check -----
    $prev = $state[$symbol]
    $shouldFire = $true
    $reason = ''

    if ($prev) {
        $prevDir = [string]$prev.last_direction
        $prevFiredAt = $null
        try { $prevFiredAt = [DateTime]::Parse($prev.fired_at).ToUniversalTime() } catch {}
        $prevSl = [double]$prev.sl
        $prevInvalidated = $false

        # Invalidation: current price broke the previous SL
        $cp = [double]$sig.current_price
        if ($prevDir -eq 'long'  -and $cp -le $prevSl) { $prevInvalidated = $true }
        if ($prevDir -eq 'short' -and $cp -ge $prevSl) { $prevInvalidated = $true }

        if (-not $prevInvalidated -and $prevDir -eq $sig.signal -and $prevFiredAt) {
            $age = $nowUtc - $prevFiredAt
            if ($age.TotalHours -lt 4) {
                $shouldFire = $false
                $reason = "deduped: same direction $prevDir fired $([math]::Round($age.TotalHours,2))h ago"
            }
        }
    }

    if (-not $shouldFire) {
        Write-Log "${symbol}: SKIP - $reason"
        $skipped++
        continue
    }

    # ----- Screenshot (taken inline by claude during scan) -----
    $shotPath = [string]$sig.screenshot_path
    $shotOk = $false
    if ($shotPath -and (Test-Path $shotPath)) {
        $shotOk = $true
        Write-Log "${symbol}: screenshot at $shotPath"
    } else {
        Write-Log "${symbol}: no screenshot (path='$shotPath')"
    }

    # ----- Build embed -----
    $isLong = $sig.signal -eq 'long'
    $color  = if ($isLong) { 0x2ECC71 } else { 0xE74C3C }
    $dirIcon = if ($isLong) { 'LONG' } else { 'SHORT' }
    $arrow  = if ($isLong) { [char]0x25B2 } else { [char]0x25BC }

    $fields = @(
        @{ name = 'Entry';   value = "$($sig.entry)";       inline = $true },
        @{ name = 'SL';      value = "$($sig.sl) (1.5xATR)"; inline = $true },
        @{ name = 'TP';      value = "$($sig.tp) (RR 2:1)";  inline = $true },
        @{ name = 'Trigger'; value = "$($sig.trigger_note)"; inline = $false },
        @{ name = 'Risk';    value = "$($sig.risk_note)";    inline = $false }
    )

    $embed = @{
        title  = "$arrow $symbol 1H - $dirIcon"
        color  = $color
        fields = $fields
        footer = @{ text = "RSI $($sig.rsi) | MACD hist $($sig.macd_hist) | ATR $($sig.atr)" }
    }

    if ($shotOk) {
        $embed.image = @{ url = "attachment://$(Split-Path $shotPath -Leaf)" }
    }

    Write-Log "${symbol}: POSTING $($sig.signal) entry=$($sig.entry) sl=$($sig.sl) tp=$($sig.tp)"
    try {
        if ($shotOk) {
            & $sendScript -Embed $embed -ImagePath $shotPath
        } else {
            & $sendScript -Embed $embed
        }
        $posted++

        # Update state on successful post
        $state[$symbol] = [pscustomobject]@{
            last_direction = $sig.signal
            fired_at       = $nowUtc.ToString('o')
            entry          = $sig.entry
            sl             = $sig.sl
            tp             = $sig.tp
            drawing_id     = [string]$sig.drawing_id
        }
    } catch {
        Write-Log "${symbol}: ERROR posting to Discord - $($_.Exception.Message)"
    }
}

# ---------- Persist state ----------
$stateObj = [pscustomobject]@{}
foreach ($k in $state.Keys) {
    $stateObj | Add-Member -NotePropertyName $k -NotePropertyValue $state[$k]
}
$stateObj | ConvertTo-Json -Depth 5 | Out-File -FilePath $stateFile -Encoding utf8

Write-Log "=== Hourly run complete: $posted posted, $skipped skipped ==="
