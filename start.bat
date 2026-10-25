@echo off
rem CROM - start the app (double-click, or run it from a command prompt).
rem The app runs locally on http://localhost:8765 and opens in the browser.
rem Stop it with Ctrl+C or by closing this window.
title CROM
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (
  echo Python for CROM is not set up yet.
  echo Run install.bat first ^(needed once^).
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo ffmpeg is missing, no video can be saved without it. Run install.bat.
  pause
  exit /b 1
)
"%PY%" "%~dp0app.py" --open %*
if errorlevel 1 pause
