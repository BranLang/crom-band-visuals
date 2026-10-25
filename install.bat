@echo off
rem CROM - install (needed once). Sets up Python, the libraries and ffmpeg.
rem Double-click it. It takes a few minutes and needs the internet.
setlocal
title CROM - install
cd /d "%~dp0"
echo.
echo  CROM - install
echo  ==============
echo.

rem ---- 1. Python 3.10 or newer ----
set "PYEXE="
call :findpy
if not defined PYEXE (
  echo [1/4] Python is not installed, installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  call :findpy
)
if not defined PYEXE (
  echo.
  echo  Python could not be found or installed.
  echo  Download it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH" in the installer^)
  echo  and then run install.bat again.
  pause
  exit /b 1
)
echo [1/4] Python: OK

rem ---- 2. Own environment .venv in this folder ----
if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Creating the .venv environment ...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 (
    echo  The environment could not be created.
    pause
    exit /b 1
  )
)
echo [2/4] Environment .venv: OK

rem ---- 3. Libraries ----
echo [3/4] Installing libraries ^(numpy, Pillow, opencv, fontTools^) ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo  The libraries could not be installed. Check the internet connection and try again.
  pause
  exit /b 1
)
echo [3/4] Libraries: OK

rem ---- 4. ffmpeg (video encoding) ----
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [4/4] ffmpeg is missing, installing it with winget ...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo  ffmpeg could not be found. Close this window and run install.bat once more.
  echo  If that does not help, install ffmpeg by hand: https://www.gyan.dev/ffmpeg/builds/
  pause
  exit /b 1
)
echo [4/4] ffmpeg: OK

echo.
echo  Done. Start the app by double-clicking start.bat
echo.
pause
exit /b 0

:findpy
rem full path of a Python 3.10+ (py launcher, python in PATH, or the default per-user install)
set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable) if sys.version_info >= (3, 10) else None" 2^>nul') do set "PYEXE=%%i"
if defined PYEXE exit /b 0
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable) if sys.version_info >= (3, 10) else None" 2^>nul') do set "PYEXE=%%i"
if defined PYEXE exit /b 0
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
exit /b 0
