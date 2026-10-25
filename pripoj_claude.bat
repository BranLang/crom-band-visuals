@echo off
rem CROM - pripojenie k Claude (MCP server). Volitelne, az po instaluj.bat.
setlocal
title CROM - pripojenie ku Claude
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Najprv spusti instaluj.bat.
  pause
  exit /b 1
)
"%PY%" "%~dp0mcp_server.py" --setup
pause
