@echo off
REM Shows whether the station system and the training portal are running.
for %%p in (8000 8001) do (
  curl -s -o nul --max-time 3 http://127.0.0.1:%%p/healthz && echo  Port %%p: RUNNING  - http://localhost:%%p || echo  Port %%p: DOWN - double-click start-background.vbs
)
echo.
echo  Logs: petrol_station\logs\
pause
