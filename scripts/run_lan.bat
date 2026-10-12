@echo off
REM ==================================================================
REM  AX Mark - share on the company network (LAN)
REM  This PC : http://localhost:8000
REM  Others  : http://<this PC IPv4>:8000   (e.g. http://10.70.59.202:8000)
REM  Settings are read from the .env file in the project root.
REM ==================================================================
cd /d "%~dp0.."

if not exist ".env" (
  echo [!] .env file not found.
  echo     Copy .env.example to .env and set DATABASE_URL first.
  pause
  exit /b 1
)

REM Plain http on the LAN: cookies must not require HTTPS, or nobody can log in.
set SESSION_HTTPS_ONLY=false

echo.
echo  Addresses of this PC (give "http://<IPv4>:8000" to other users):
ipconfig | findstr /i "IPv4"
echo.
echo  Press Ctrl+C to stop the server.
echo.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
