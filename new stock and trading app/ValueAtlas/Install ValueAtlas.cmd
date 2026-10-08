@echo off
title ValueAtlas installer
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" %*
if errorlevel 1 (
  echo.
  echo Installation did not finish. Read the message above.
  pause
  exit /b 1
)
echo.
echo You can close this window.
timeout /t 15
