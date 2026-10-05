@echo off
REM Keeps one server running: starts it, and starts it again if it ever stops.
REM Used by start-background.vbs (hidden). Usage: server-keepalive.bat serve.py 8000
set SCRIPT=%1
set PORT=%2
cd /d "%~dp0petrol_station"
if not exist logs mkdir logs
:loop
REM Already answering on this port? Then just check again in 30 seconds.
curl -s -o nul --max-time 3 http://127.0.0.1:%PORT%/healthz && (timeout /t 30 /nobreak >nul & goto loop)
echo [%date% %time%] starting %SCRIPT% >> logs\%SCRIPT%.log
..\.venv\Scripts\python.exe %SCRIPT% >> logs\%SCRIPT%.log 2>&1
echo [%date% %time%] %SCRIPT% stopped - restarting in 5 seconds >> logs\%SCRIPT%.log
timeout /t 5 /nobreak >nul
goto loop
