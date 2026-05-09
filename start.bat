@echo off
echo =========================================
echo   SmartQueue - Setup and Launch
echo =========================================

:: Try to find Python
where python >nul 2>&1
if %errorlevel%==0 (
    set PY=python
    goto :install
)

where python3 >nul 2>&1
if %errorlevel%==0 (
    set PY=python3
    goto :install
)

where py >nul 2>&1
if %errorlevel%==0 (
    set PY=py
    goto :install
)

echo [ERROR] Python not found on PATH.
echo Please install Python from https://www.python.org/downloads/
echo Make sure to check "Add Python to PATH" during installation.
pause
exit /b 1

:install
echo [1/2] Installing dependencies...
%PY% -m pip install -r requirements.txt

echo.
echo [2/2] Starting Flask server...
echo Open your browser at: http://localhost:5000
echo Press Ctrl+C to stop the server.
echo.
%PY% app.py
pause
