[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING     = 'utf-8'
chcp 65001 | Out-Null

$root    = Split-Path $PSScriptRoot -Parent
$logDir  = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# Python handles all logic + logging; just launch it
# Full path required — Task Scheduler runs -NoProfile and misses user-local PATH entries
$py = "C:\Users\bower\AppData\Local\Python\pythoncore-3.14-64\python.exe"
& $py "$PSScriptRoot\hourly-scan.py"
if ($LASTEXITCODE -ne 0) {
    $errLine = "$(Get-Date -Format o) ERROR: py exited $LASTEXITCODE"
    $errLine | Out-File -Append -Encoding utf8 (Join-Path $logDir "hourly-$(Get-Date -Format 'yyyy-MM-dd-HH').log")
}
