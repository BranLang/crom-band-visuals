@echo off
rem CROM - instalacia (staci raz). Pripravi Python, kniznice a ffmpeg.
rem Spusti dvojklikom. Trva par minut, potrebuje internet.
setlocal
title CROM - instalacia
cd /d "%~dp0"
echo.
echo  CROM - instalacia
echo  =================
echo.

rem ---- 1. Python 3.10 alebo novsi ----
set "PYEXE="
call :findpy
if not defined PYEXE (
  echo [1/4] Python nie je nainstalovany, instalujem Python 3.12 cez winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  call :findpy
)
if not defined PYEXE (
  echo.
  echo  Python sa nepodarilo najst ani nainstalovat.
  echo  Stiahni ho z https://www.python.org/downloads/ ^(pri instalacii zaskrtni "Add python.exe to PATH"^)
  echo  a potom spusti instaluj.bat znova.
  pause
  exit /b 1
)
echo [1/4] Python: OK

rem ---- 2. Vlastne prostredie .venv v tomto priecinku ----
if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Vytvaram prostredie .venv ...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 (
    echo  Prostredie sa nepodarilo vytvorit.
    pause
    exit /b 1
  )
)
echo [2/4] Prostredie .venv: OK

rem ---- 3. Kniznice ----
echo [3/4] Instalujem kniznice ^(numpy, Pillow, opencv, fontTools^) ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo  Kniznice sa nepodarilo nainstalovat. Skontroluj internet a skus znova.
  pause
  exit /b 1
)
echo [3/4] Kniznice: OK

rem ---- 4. ffmpeg (kodovanie videa) ----
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [4/4] ffmpeg chyba, instalujem cez winget ...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo  ffmpeg sa nepodarilo najst. Zatvor toto okno a spusti instaluj.bat este raz.
  echo  Ak to nepomoze, nainstaluj ffmpeg rucne: https://www.gyan.dev/ffmpeg/builds/
  pause
  exit /b 1
)
echo [4/4] ffmpeg: OK

echo.
echo  Hotovo. Aplikaciu spustis dvojklikom na spusti.bat
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
