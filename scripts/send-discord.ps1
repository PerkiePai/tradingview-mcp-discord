[CmdletBinding()]
param(
    [string]$Content,
    [hashtable]$Embed,
    [hashtable[]]$Embeds,
    [string]$ImagePath
)

$ErrorActionPreference = 'Stop'

# Load .env from project root (this script lives in scripts/)
$envFile = Join-Path $PSScriptRoot '..\.env'
if (-not (Test-Path $envFile)) {
    throw ".env not found at $envFile. Copy .env.example to .env and set DISCORD_WEBHOOK_URL."
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+?)\s*=\s*(.*?)\s*$') {
        Set-Item "env:$($Matches[1])" $Matches[2]
    }
}

$webhook = $env:DISCORD_WEBHOOK_URL
if (-not $webhook -or $webhook -like '*REPLACE_ME*') {
    throw 'DISCORD_WEBHOOK_URL not set in .env'
}

if (-not $Content -and -not $Embed -and -not $Embeds) {
    throw 'Must supply -Content, -Embed, or -Embeds.'
}

$payload = @{}
if ($Content) { $payload.content = $Content }
if ($Embeds)     { $payload.embeds = $Embeds }
elseif ($Embed)  { $payload.embeds = @($Embed) }
$payloadJson = $payload | ConvertTo-Json -Depth 10 -Compress

if ($ImagePath) {
    if (-not (Test-Path $ImagePath)) {
        throw "Image not found: $ImagePath"
    }
    # Manual multipart/form-data (Windows PowerShell 5.1 has no -Form param)
    $boundary  = [Guid]::NewGuid().ToString()
    $LF        = "`r`n"
    $fileName  = Split-Path $ImagePath -Leaf
    $fileBytes = [System.IO.File]::ReadAllBytes($ImagePath)
    $enc       = [System.Text.Encoding]::UTF8

    $headerText = "--$boundary$LF" +
                  "Content-Disposition: form-data; name=`"payload_json`"$LF" +
                  "Content-Type: application/json$LF$LF" +
                  "$payloadJson$LF" +
                  "--$boundary$LF" +
                  "Content-Disposition: form-data; name=`"files[0]`"; filename=`"$fileName`"$LF" +
                  "Content-Type: image/png$LF$LF"
    $headerBytes = $enc.GetBytes($headerText)
    $footerBytes = $enc.GetBytes("$LF--$boundary--$LF")

    $ms = New-Object System.IO.MemoryStream
    $ms.Write($headerBytes, 0, $headerBytes.Length)
    $ms.Write($fileBytes,   0, $fileBytes.Length)
    $ms.Write($footerBytes, 0, $footerBytes.Length)
    $body = $ms.ToArray()

    Invoke-RestMethod -Uri $webhook -Method Post -Body $body `
        -ContentType "multipart/form-data; boundary=$boundary" | Out-Null
} else {
    # Force UTF-8 body — PS 5.1's Invoke-RestMethod otherwise encodes strings as ISO-8859-1,
    # which mangles em-dashes, arrows, and other non-ASCII characters in the embed.
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($payloadJson)
    Invoke-RestMethod -Uri $webhook -Method Post -Body $bodyBytes `
        -ContentType 'application/json; charset=utf-8' | Out-Null
}

Write-Host "Discord post sent."
