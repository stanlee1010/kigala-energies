@echo off
REM Starts ONLY the training portal (practice data, separate database) on port 8001.
cd /d "%~dp0petrol_station"
echo.
echo  TRAINING PORTAL - practice data only, nothing affects the real station.
echo  Open http://localhost:8001   (tap a shift to practise - no password needed)
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo  Phones on the same network: http:%%a:8001
echo.
..\.venv\Scripts\python.exe serve_demo.py
pause
