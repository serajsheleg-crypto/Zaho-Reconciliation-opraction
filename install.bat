@echo off
setlocal
title Zaho Reconcile - install
cd /d "%~dp0"

echo.
echo  Zaho Reconcile - one-time install
echo  ---------------------------------
echo  Needs: Python 3.11+ and XAMPP MySQL running on port 3306.
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo  [!!] Python was not found. Install it from https://www.python.org/downloads/ and tick "Add to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo  [..] Creating the Python environment
  python -m venv .venv || goto :fail
)

echo  [..] Installing packages (first time takes a minute)
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt -q || goto :fail

echo  [..] Setting up the MySQL database
rem XAMPP's default MySQL root has no password. If yours has one, run:
rem   .venv\Scripts\python.exe scripts\setup.py --root-password YOUR_PASSWORD
".venv\Scripts\python.exe" scripts\setup.py %* || goto :fail

if not exist "frontend\dist\index.html" (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo  [!!] The web interface is not built and Node.js is not installed.
    echo       Install Node.js 20+ and run install.bat again.
    goto :fail
  )
  echo  [..] Building the web interface
  pushd frontend
  call npm install --no-audit --no-fund || (popd & goto :fail)
  call npm run build || (popd & goto :fail)
  popd
)

echo.
echo  [ok] Installed. Double-click start.bat to open Zaho Reconcile.
echo       First sign-in: admin@zaho.ly / Admin@2026 (you will be asked to change it).
echo.
pause
exit /b 0

:fail
echo.
echo  [!!] Install stopped because of the error above.
pause
exit /b 1
