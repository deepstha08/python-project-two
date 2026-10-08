# ValueAtlas installer for Windows 10/11.
# Installs into %LOCALAPPDATA%\Programs\ValueAtlas for the current user. No administrator rights needed.
# Python is downloaded automatically (via uv) if it is not already installed.
param(
    [switch]$NoStartup,     # do not start ValueAtlas in the background when Windows starts
    [switch]$NoLaunch       # do not open the app when installation finishes
)
# Native tools (uv, pip) write progress to stderr; 'Continue' stops Windows PowerShell 5.1
# from treating that as an error. Critical cmdlets use -ErrorAction Stop explicitly.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Source = Split-Path -Parent $PSScriptRoot
$Target = Join-Path $env:LOCALAPPDATA 'Programs\ValueAtlas'
$Venv = Join-Path $Target '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$PyW = Join-Path $Venv 'Scripts\pythonw.exe'

function Step($text) { Write-Host ''; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ''; Write-Host "ERROR: $text" -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host '  ValueAtlas installer' -ForegroundColor Green
Write-Host "  Installing to $Target"

# ---------------------------------------------------------------- stop a running copy
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*ValueAtlas*run.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# ---------------------------------------------------------------- copy files
Step 'Copying program files'
New-Item -ItemType Directory -Force -Path $Target -ErrorAction Stop | Out-Null
foreach ($item in @('valueatlas', 'run.py', 'requirements.txt', 'starter_universe.csv', 'watchlist.csv', 'README.md', 'installer')) {
    $from = Join-Path $Source $item
    if (-not (Test-Path $from)) { Fail "Missing $item next to the installer. Extract the whole ZIP first, then run 'Install ValueAtlas.cmd' again." }
    $to = Join-Path $Target $item
    try {
        if (Test-Path $to) { Remove-Item -Recurse -Force $to -ErrorAction Stop }
        Copy-Item -Recurse -Force $from $to -ErrorAction Stop
    } catch { Fail "Could not copy $item. Close ValueAtlas if it is open and try again. ($($_.Exception.Message))" }
}

# ---------------------------------------------------------------- Python environment
Step 'Preparing Python (downloaded automatically if needed)'
$uv = $null
$uvDir = Join-Path $Target 'uv'
$uvExe = Join-Path $uvDir 'uv.exe'
if (Test-Path $uvExe) { $uv = $uvExe }
elseif (Get-Command uv.exe -ErrorAction SilentlyContinue) { $uv = (Get-Command uv.exe).Source }
else {
    try {
        $env:UV_INSTALL_DIR = $uvDir
        $env:UV_NO_MODIFY_PATH = '1'
        $uvScript = Join-Path $env:TEMP 'valueatlas-uv-install.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $uvScript -ErrorAction Stop
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $uvScript
        Remove-Item -Force $uvScript -ErrorAction SilentlyContinue
        if (Test-Path $uvExe) { $uv = $uvExe }
    } catch {
        Write-Host "  Could not download the uv tool ($($_.Exception.Message)). Trying an installed Python instead." -ForegroundColor Yellow
    }
}

$ok = $false
if ($uv) {
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Target 'python'
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    & $uv venv $Venv --python 3.12 --quiet
    if ($LASTEXITCODE -eq 0) {
        Step 'Installing packages (yfinance, Flask, pandas...)'
        & $uv pip install --python $Py -r (Join-Path $Target 'requirements.txt')
        if ($LASTEXITCODE -eq 0) { $ok = $true }
    }
}
if (-not $ok) {
    $sysPy = $null
    $sysArgs = @()
    $versionCheck = 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3 -c $versionCheck 2>$null
        if ($LASTEXITCODE -eq 0) { $sysPy = 'py.exe'; $sysArgs = @('-3') }
    }
    if (-not $sysPy -and (Get-Command python.exe -ErrorAction SilentlyContinue)) {
        & python.exe -c $versionCheck 2>$null
        if ($LASTEXITCODE -eq 0) { $sysPy = 'python.exe' }
    }
    if (-not $sysPy) {
        Fail 'Python could not be set up automatically. Check your internet connection, or install Python 3.12 from https://www.python.org/downloads/windows/ and run the installer again.'
    }
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    & $sysPy @sysArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create the Python environment.' }
    Step 'Installing packages (yfinance, Flask, pandas...)'
    & $Py -m pip install --disable-pip-version-check --upgrade pip | Out-Null
    & $Py -m pip install --disable-pip-version-check -r (Join-Path $Target 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail 'Package installation failed. Check your internet connection and run the installer again.' }
}

Push-Location $Target
& $Py -c 'import yfinance, flask, waitress, pandas, valueatlas.server'
$check = $LASTEXITCODE
Pop-Location
if ($check -ne 0) { Fail 'The installation check failed. See the messages above.' }

# ---------------------------------------------------------------- shortcuts
Step 'Creating shortcuts'
$shell = New-Object -ComObject WScript.Shell
$icon = Join-Path $Target 'valueatlas\static\icon.ico'
function Make-Shortcut($path, $exe, $arguments, $description) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $exe
    $s.Arguments = $arguments
    $s.WorkingDirectory = $Target
    $s.IconLocation = $icon
    $s.Description = $description
    $s.Save()
}
$run = '"' + (Join-Path $Target 'run.py') + '"'
$desktop = [Environment]::GetFolderPath('Desktop')
$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'ValueAtlas'
New-Item -ItemType Directory -Force -Path $menu | Out-Null
Make-Shortcut (Join-Path $desktop 'ValueAtlas.lnk') $PyW $run 'Open the ValueAtlas stock dashboard'
Make-Shortcut (Join-Path $menu 'ValueAtlas.lnk') $PyW $run 'Open the ValueAtlas stock dashboard'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
Make-Shortcut (Join-Path $menu 'Update ValueAtlas.lnk') $ps ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Target 'installer\update.ps1') + '"') 'Update the data libraries'
Make-Shortcut (Join-Path $menu 'Uninstall ValueAtlas.lnk') $ps ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Target 'installer\uninstall.ps1') + '"') 'Remove ValueAtlas'
Make-Shortcut (Join-Path $menu 'Refresh data now (console).lnk') $Py ($run + ' --refresh') 'Run a data refresh in a console window'

$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'ValueAtlas (background).lnk'
if ($NoStartup) {
    if (Test-Path $startup) { Remove-Item -Force $startup }
} else {
    Make-Shortcut $startup $PyW ($run + ' --background') 'Keeps ValueAtlas running so the daily update happens'
}

Write-Host ''
Write-Host '  ValueAtlas is installed.' -ForegroundColor Green
Write-Host '  - Open it any time from the ValueAtlas icon on your desktop.'
Write-Host '  - It runs quietly in the background after you sign in to Windows and updates the data once a day.'
Write-Host '  - The first data scan takes about 5-15 minutes.'
Write-Host ''

if (-not $NoLaunch) { Start-Process -FilePath $PyW -ArgumentList $run -WorkingDirectory $Target }
exit 0
