@echo off
title Story Video Studio
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo   Python is not installed yet.
  echo   1. Download it from https://www.python.org/downloads/
  echo   2. When installing, TICK the box "Add python.exe to PATH"
  echo   3. Then double-click start_windows.bat again.
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist "venv\installed.ok" (
  echo.
  echo   First start: installing everything. This takes a few minutes, only once...
  echo.
  if not exist "venv\Scripts\python.exe" (
    %PY% -m venv venv
    if errorlevel 1 (
      echo Could not create the Python environment. Reinstall Python and tick "Add python.exe to PATH".
      pause
      exit /b 1
    )
  )
  "venv\Scripts\python.exe" -m pip install --upgrade pip
  "venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   Install failed. Check your internet connection and run start_windows.bat again.
    pause
    exit /b 1
  )
  echo ok> "venv\installed.ok"
)

echo.
echo   Starting Story Video Studio... your web browser will open in a moment.
echo   Keep this window open while you use the app. Close it to stop the app.
echo.
"venv\Scripts\python.exe" app.py
pause
