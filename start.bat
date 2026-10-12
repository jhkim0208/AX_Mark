@echo off
REM ==================================================================
REM  AX Mark - double-click to start the server.
REM  The first run on a PC sets everything up:
REM    packages, .env, database, firewall (one admin prompt).
REM  This PC : http://localhost:8000
REM  Others  : http://<this PC IP>:8000  (shown in this window)
REM ==================================================================
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [!] Python is not installed.
  echo     Install Python 3.10+ from https://www.python.org/downloads/
  echo     and check "Add python.exe to PATH" during setup. Then run start.bat again.
  pause
  exit /b 1
)

%PY% scripts\start_server.py
pause
