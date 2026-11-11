@echo off
title NCODE_PIPE // BATCH HARVESTER ^& DASHBOARD
setlocal enabledelayedexpansion

cd /d "%~dp0.."
set "PYTHONPATH=%CD%"

echo ========================================================
echo   NCODE_PIPE // BATCH HARVESTER ^& DASHBOARD
echo ========================================================
echo.

:: Detect Python
set "PY_CMD=python"
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        echo [ERROR] Python not found in PATH or standard directory!
        pause
        exit /b 1
    )
)

:: Verify & Auto-install dependencies
echo [*] Checking required dependencies (Flask, Requests, BeautifulSoup4, LXML)...
"%PY_CMD%" -c "import flask, requests, bs4, lxml" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [!] Missing dependencies detected. Installing from requirements.txt...
    "%PY_CMD%" -m pip install -r "%~dp0requirements.txt"
    if %ERRORLEVEL% NEQ 0 (
        echo [!] pip install failed, attempting with uv...
        where uv >nul 2>nul
        if %ERRORLEVEL% EQU 0 (
            uv pip install -r "%~dp0requirements.txt"
        ) else (
            echo [ERROR] Failed to install dependencies! Please install pip or check network.
            pause
            exit /b 1
        )
    )
    echo [*] Dependencies installed successfully!
) else (
    echo [*] All dependencies OK!
)
echo.

echo [*] Starting ncode_pipe server on http://127.0.0.1:5005 ...
echo [*] Press Ctrl+C in this window to stop.
echo.

start http://127.0.0.1:5005

"%PY_CMD%" -m ncode_pipe.monitor

pause
