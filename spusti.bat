@echo off
rem CROM - spustenie aplikacie (dvojklik alebo z prikazoveho riadku).
rem Aplikacia bezi lokalne na http://localhost:8765 a otvori sa v prehliadaci.
rem Zastavis ju klavesmi Ctrl+C alebo zatvorenim tohto okna.
title CROM
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (
  echo Python pre CROM nie je pripraveny.
  echo Najprv spusti instaluj.bat ^(staci raz^).
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo Chyba ffmpeg, bez neho sa video neulozi. Spusti instaluj.bat.
  pause
  exit /b 1
)
"%PY%" "%~dp0app.py" --open %*
if errorlevel 1 pause
