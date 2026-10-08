# Updates the data libraries (mainly yfinance, which changes when Yahoo changes its website).
$ErrorActionPreference = 'Continue'
$Target = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Target '.venv\Scripts\python.exe'
$uv = Join-Path $Target 'uv\uv.exe'
Write-Host ''
Write-Host '  Updating ValueAtlas libraries...' -ForegroundColor Cyan
if (Test-Path $uv) { & $uv pip install --python $Py --upgrade -r (Join-Path $Target 'requirements.txt') }
else { & $Py -m pip install --disable-pip-version-check --upgrade -r (Join-Path $Target 'requirements.txt') }
if ($LASTEXITCODE -eq 0) {
    Write-Host '  Done. Restart ValueAtlas (sign out and in, or close it from Task Manager and open it again).' -ForegroundColor Green
} else {
    Write-Host '  Update failed. Check your internet connection.' -ForegroundColor Red
}
Read-Host 'Press Enter to close'
