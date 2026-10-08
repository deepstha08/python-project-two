# Removes ValueAtlas for the current user. Your saved data can optionally be kept.
$ErrorActionPreference = 'Continue'
$Target = Join-Path $env:LOCALAPPDATA 'Programs\ValueAtlas'
$Data = Join-Path $env:LOCALAPPDATA 'ValueAtlas'
Set-Location $env:TEMP

Write-Host ''
Write-Host '  Uninstalling ValueAtlas' -ForegroundColor Yellow
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*ValueAtlas*run.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

$links = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'ValueAtlas.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Startup')) 'ValueAtlas (background).lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'ValueAtlas')
)
foreach ($l in $links) { if (Test-Path $l) { Remove-Item -Recurse -Force $l } }

$keep = Read-Host 'Keep your saved rankings, max pain history and notes? (Y/n)'
if ($keep -match '^[nN]') { if (Test-Path $Data) { Remove-Item -Recurse -Force $Data } ; Write-Host '  Saved data removed.' }
else { Write-Host "  Saved data kept in $Data" }

# The script lives inside the folder being deleted, so remove it from a temporary copy.
$cleanup = Join-Path $env:TEMP 'valueatlas-cleanup.cmd'
Set-Content -Path $cleanup -Encoding ASCII -Value "@echo off`r`ntimeout /t 4 /nobreak >nul`r`nrmdir /s /q `"$Target`"`r`ndel `"%~f0`""
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$cleanup`"" -WindowStyle Hidden
Write-Host '  ValueAtlas has been removed.' -ForegroundColor Green
Start-Sleep -Seconds 2
