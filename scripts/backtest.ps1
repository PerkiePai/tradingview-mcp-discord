[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BarsFile,
    [Parameter(Mandatory)][string]$Symbol,
    [int]   $Lookback   = 168,
    [double]$RR         = 2.0,
    [double]$AtrMult    = 1.5,
    [int]   $EmaShort   = 50,
    [int]   $EmaLong    = 200,
    [int]   $RsiLength  = 14,
    [int]   $AtrLength  = 14
)

$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Self-contained 1H backtest: indicator math + signal walk + outcome walk.
# Reads OHLCV bars from a JSON file. Designed to keep math out of the LLM —
# claude -p just orchestrates (fetch via MCP, dump to file, run this) and the
# heavy iteration happens in pure PowerShell.
# -----------------------------------------------------------------------------

if (-not (Test-Path $BarsFile)) { throw "Bars file not found: $BarsFile" }

$j = (Get-Content $BarsFile -Raw) | ConvertFrom-Json
$bars = if ($j.bars) { $j.bars } else { $j }
$n = $bars.Count
$minRequired = $EmaLong + 1
if ($n -lt $minRequired) { throw "Need >= $minRequired bars for EMA$EmaLong; got $n" }

# Extract column arrays
$opens  = New-Object 'double[]' $n
$highs  = New-Object 'double[]' $n
$lows   = New-Object 'double[]' $n
$closes = New-Object 'double[]' $n
$times  = New-Object 'long[]'   $n
for ($i = 0; $i -lt $n; $i++) {
    $opens[$i]  = [double]$bars[$i].open
    $highs[$i]  = [double]$bars[$i].high
    $lows[$i]   = [double]$bars[$i].low
    $closes[$i] = [double]$bars[$i].close
    $times[$i]  = [long]  $bars[$i].time
}

# -----------------------------------------------------------------------------
# Indicators
# -----------------------------------------------------------------------------

function New-NanArray([int]$len) {
    $a = New-Object 'double[]' $len
    for ($k = 0; $k -lt $len; $k++) { $a[$k] = [double]::NaN }
    return ,$a
}

function Get-Ema {
    param([double[]]$src, [int]$len)
    $size = $src.Length
    $out = New-NanArray $size
    if ($size -lt $len) { return ,$out }
    $alpha = 2.0 / ($len + 1)
    $sum = 0.0
    for ($k = 0; $k -lt $len; $k++) { $sum += $src[$k] }
    $out[$len - 1] = $sum / $len
    for ($k = $len; $k -lt $size; $k++) {
        $out[$k] = $alpha * $src[$k] + (1 - $alpha) * $out[$k - 1]
    }
    return ,$out
}

function Get-Rsi {
    param([double[]]$src, [int]$len)
    $size = $src.Length
    $out  = New-NanArray $size
    if ($size -lt ($len + 1)) { return ,$out }
    $gains  = New-Object 'double[]' $size
    $losses = New-Object 'double[]' $size
    for ($k = 1; $k -lt $size; $k++) {
        $d = $src[$k] - $src[$k - 1]
        if ($d -ge 0) { $gains[$k] = $d } else { $losses[$k] = -$d }
    }
    $sg = 0.0; $sl = 0.0
    for ($k = 1; $k -le $len; $k++) { $sg += $gains[$k]; $sl += $losses[$k] }
    $ag = $sg / $len
    $al = $sl / $len
    $out[$len] = if ($al -eq 0) { 100.0 } else { 100.0 - 100.0 / (1 + ($ag / $al)) }
    for ($k = $len + 1; $k -lt $size; $k++) {
        $ag = ($ag * ($len - 1) + $gains[$k])  / $len
        $al = ($al * ($len - 1) + $losses[$k]) / $len
        $out[$k] = if ($al -eq 0) { 100.0 } else { 100.0 - 100.0 / (1 + ($ag / $al)) }
    }
    return ,$out
}

function Get-Atr {
    param([double[]]$h, [double[]]$l, [double[]]$c, [int]$len)
    $size = $h.Length
    $out  = New-NanArray $size
    $tr   = New-Object 'double[]' $size
    $tr[0] = $h[0] - $l[0]
    for ($k = 1; $k -lt $size; $k++) {
        $a = $h[$k] - $l[$k]
        $b = [Math]::Abs($h[$k] - $c[$k - 1])
        $cTr = [Math]::Abs($l[$k] - $c[$k - 1])
        $tr[$k] = [Math]::Max($a, [Math]::Max($b, $cTr))
    }
    if ($size -lt $len) { return ,$out }
    $sum = 0.0
    for ($k = 0; $k -lt $len; $k++) { $sum += $tr[$k] }
    $out[$len - 1] = $sum / $len
    for ($k = $len; $k -lt $size; $k++) {
        $out[$k] = ($out[$k - 1] * ($len - 1) + $tr[$k]) / $len
    }
    return ,$out
}

$ema50  = Get-Ema $closes $EmaShort
$ema200 = Get-Ema $closes $EmaLong
$ema12  = Get-Ema $closes 12
$ema26  = Get-Ema $closes 26

# MACD line valid from bar (26-1)
$macdLine = New-NanArray $n
for ($i = 25; $i -lt $n; $i++) {
    if (-not [double]::IsNaN($ema12[$i]) -and -not [double]::IsNaN($ema26[$i])) {
        $macdLine[$i] = $ema12[$i] - $ema26[$i]
    }
}

# Signal = EMA9 of MACD line (from the first valid MACD index)
$signal = New-NanArray $n
$hist   = New-NanArray $n
$macdStart = 25
$validLen = $n - $macdStart
if ($validLen -ge 9) {
    $slice = New-Object 'double[]' $validLen
    for ($i = 0; $i -lt $validLen; $i++) { $slice[$i] = $macdLine[$macdStart + $i] }
    $signalSlice = Get-Ema $slice 9
    for ($i = 0; $i -lt $validLen; $i++) {
        $sigVal = $signalSlice[$i]
        if (-not [double]::IsNaN($sigVal)) {
            $signal[$macdStart + $i] = $sigVal
            $hist[$macdStart + $i]   = $macdLine[$macdStart + $i] - $sigVal
        }
    }
}

$rsi = Get-Rsi $closes $RsiLength
$atr = Get-Atr $highs $lows $closes $AtrLength

# -----------------------------------------------------------------------------
# Signal walk over last $Lookback bars (skip bars where any indicator is NaN
# OR the prior bar's value would be NaN)
# -----------------------------------------------------------------------------

$startIdx = [Math]::Max($EmaLong, $n - $Lookback)
$fires = New-Object System.Collections.ArrayList

for ($i = $startIdx; $i -lt $n; $i++) {
    if ([double]::IsNaN($ema50[$i])  -or [double]::IsNaN($ema200[$i])) { continue }
    if ([double]::IsNaN($rsi[$i])    -or [double]::IsNaN($rsi[$i - 1])) { continue }
    if ([double]::IsNaN($hist[$i])   -or [double]::IsNaN($hist[$i - 1])) { continue }

    $isLong  = ($closes[$i] -gt $ema50[$i])  -and ($ema50[$i] -gt $ema200[$i]) `
               -and ($rsi[$i - 1] -lt 50)    -and ($rsi[$i]   -ge 50) `
               -and ($hist[$i - 1] -le 0)    -and ($hist[$i]  -gt 0)
    $isShort = ($closes[$i] -lt $ema50[$i])  -and ($ema50[$i] -lt $ema200[$i]) `
               -and ($rsi[$i - 1] -ge 50)    -and ($rsi[$i]   -lt 50) `
               -and ($hist[$i - 1] -ge 0)    -and ($hist[$i]  -lt 0)
    if (-not ($isLong -or $isShort)) { continue }

    $dir   = if ($isLong) { 'long' } else { 'short' }
    $entry = $closes[$i]
    $stop  = $AtrMult * $atr[$i]
    if ($dir -eq 'long') {
        $sl = $entry - $stop
        $tp = $entry + $RR * $stop
    } else {
        $sl = $entry + $stop
        $tp = $entry - $RR * $stop
    }

    # Walk forward for outcome
    $outcome = 'open'
    $outcomeIdx = $null
    $mfe = 0.0
    $mae = 0.0
    for ($k = $i + 1; $k -lt $n; $k++) {
        $h = $highs[$k]; $l = $lows[$k]
        if ($dir -eq 'long') {
            $fav = $h - $entry; if ($fav -gt $mfe) { $mfe = $fav }
            $adv = $entry - $l; if ($adv -gt $mae) { $mae = $adv }
            $tpHit = $h -ge $tp
            $slHit = $l -le $sl
        } else {
            $fav = $entry - $l; if ($fav -gt $mfe) { $mfe = $fav }
            $adv = $h - $entry; if ($adv -gt $mae) { $mae = $adv }
            $tpHit = $l -le $tp
            $slHit = $h -ge $sl
        }
        if ($tpHit -and $slHit) { $outcome = 'ambiguous'; $outcomeIdx = $k; break }
        elseif ($tpHit)         { $outcome = 'tp';        $outcomeIdx = $k; break }
        elseif ($slHit)         { $outcome = 'sl';        $outcomeIdx = $k; break }
    }

    $pnlR = switch ($outcome) {
        'tp'        { $RR }
        'sl'        { -1.0 }
        'ambiguous' { -1.0 }
        default     { $null }
    }

    [void]$fires.Add([pscustomobject]@{
        time                    = [DateTimeOffset]::FromUnixTimeSeconds($times[$i]).UtcDateTime.ToString('o')
        direction               = $dir
        entry                   = [Math]::Round($entry, 4)
        sl                      = [Math]::Round($sl, 4)
        tp                      = [Math]::Round($tp, 4)
        rsi_prev                = [Math]::Round($rsi[$i - 1], 2)
        rsi_now                 = [Math]::Round($rsi[$i], 2)
        macd_hist_prev          = [Math]::Round($hist[$i - 1], 4)
        macd_hist_now           = [Math]::Round($hist[$i], 4)
        atr                     = [Math]::Round($atr[$i], 4)
        outcome                 = $outcome
        outcome_bar             = if ($null -eq $outcomeIdx) { $null } else { [DateTimeOffset]::FromUnixTimeSeconds($times[$outcomeIdx]).UtcDateTime.ToString('o') }
        bars_to_resolution      = if ($null -eq $outcomeIdx) { $null } else { $outcomeIdx - $i }
        max_favorable_excursion = [Math]::Round($mfe, 4)
        max_adverse_excursion   = [Math]::Round($mae, 4)
        pnl_in_R                = $pnlR
    })
}

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

$firesArr = @($fires)
$wins   = @($firesArr | Where-Object { $_.outcome -eq 'tp' }).Count
$losses = @($firesArr | Where-Object { $_.outcome -eq 'sl' }).Count
$ambig  = @($firesArr | Where-Object { $_.outcome -eq 'ambiguous' }).Count
$open   = @($firesArr | Where-Object { $_.outcome -eq 'open' }).Count
$resolved = $wins + $losses + $ambig

$winRate    = if ($resolved -gt 0) { [Math]::Round($wins / $resolved, 3) } else { $null }
$expectancy = if ($resolved -gt 0) {
    $sumR = 0.0
    foreach ($f in $firesArr) { if ($null -ne $f.pnl_in_R) { $sumR += [double]$f.pnl_in_R } }
    [Math]::Round($sumR / $resolved, 3)
} else { $null }

$result = [pscustomobject]@{
    symbol         = $Symbol
    bars_available = $n
    lookback_bars  = $Lookback
    longs          = @($firesArr | Where-Object { $_.direction -eq 'long' }).Count
    shorts         = @($firesArr | Where-Object { $_.direction -eq 'short' }).Count
    total_fires    = $firesArr.Count
    fires          = $firesArr
    summary        = [pscustomobject]@{
        wins            = $wins
        losses          = $losses
        ambiguous       = $ambig
        open            = $open
        win_rate        = $winRate
        expectancy_in_R = $expectancy
    }
}

$result | ConvertTo-Json -Depth 8 -Compress
