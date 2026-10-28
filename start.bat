@echo off
setlocal
title Zaho Reconcile
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo  Zaho Reconcile is not installed yet. Running install.bat first...
  call install.bat || exit /b 1
)

set PORT=8077
for /f "tokens=2 delims==" %%p in ('findstr /b "PORT=" .env 2^>nul') do set PORT=%%p

echo.
echo  Zaho Reconcile is starting on http://127.0.0.1:%PORT%
echo  Keep this window open while you work. Close it to stop the app.
echo  (Start MySQL from the XAMPP Control Panel first.)
echo.

start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%PORT%"
cd backend
"..\.venv\Scripts\python.exe" -m app.main
pause
