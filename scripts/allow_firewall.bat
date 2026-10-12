@echo off
REM ==================================================================
REM  Allow other PCs on the company network to reach AX Mark (TCP 8000)
REM  Right-click this file -> "Run as administrator"
REM  Only domain/private networks are opened (not public Wi-Fi).
REM  To remove later:
REM    netsh advfirewall firewall delete rule name="AX Mark (TCP 8000)"
REM ==================================================================
net session >nul 2>&1
if errorlevel 1 (
  echo [!] Administrator rights required.
  echo     Right-click allow_firewall.bat and choose "Run as administrator".
  pause
  exit /b 1
)
netsh advfirewall firewall delete rule name="AX Mark (TCP 8000)" >nul 2>&1
netsh advfirewall firewall add rule name="AX Mark (TCP 8000)" dir=in action=allow protocol=TCP localport=8000 profile=domain,private
echo.
echo Done. Other users can now open http://<this PC IPv4>:8000
pause
