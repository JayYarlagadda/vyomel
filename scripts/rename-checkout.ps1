# Rename local checkout D:\Astra -> D:\Vyomel
# Close Cursor first, then run from PowerShell (outside this folder):
#   powershell -ExecutionPolicy Bypass -File D:\Astra\scripts\rename-checkout.ps1

$ErrorActionPreference = "Stop"
$src = "D:\Astra"
$dst = "D:\Vyomel"

if (-not (Test-Path $src)) {
    if (Test-Path $dst) {
        Write-Host "Already renamed: $dst"
        exit 0
    }
    throw "Source not found: $src"
}

if (Test-Path $dst) {
    throw "Destination already exists: $dst"
}

Rename-Item -LiteralPath $src -NewName "Vyomel"
Write-Host "Renamed $src -> $dst"
Write-Host "Reopen Cursor on $dst"
