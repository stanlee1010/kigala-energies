@echo off
REM Double-click to start the Kigala Energies station system.
REM Staff phones on the same Wi-Fi open http://<this-computer-IP>:8000
cd /d "%~dp0petrol_station"
if not exist "..\.venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -3.12 -m venv ..\.venv || python -m venv ..\.venv
  ..\.venv\Scripts\python.exe -m pip install -r requirements.txt
)
REM The training portal (practice data, separate database) opens in its own window on port 8001.
start "Training portal (practice)" cmd /c "%~dp0start-training.bat"
echo.
echo  REAL station system:   http://localhost:8000
echo  Training portal:       http://localhost:8001  (practice only)
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo  Phones on the same network: http:%%a:8000  (training: port 8001)
echo.
REM Open the website in the default browser a few seconds after the server starts.
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8000"
..\.venv\Scripts\python.exe serve.py
pause
