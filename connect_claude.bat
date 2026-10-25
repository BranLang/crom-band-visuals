@echo off
rem CROM - connect to Claude (MCP server). Optional, only after install.bat.
setlocal
title CROM - connect to Claude
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run install.bat first.
  pause
  exit /b 1
)
"%PY%" "%~dp0mcp_server.py" --setup
pause
