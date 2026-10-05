@echo off
REM Stops the station system and the training portal (and their automatic restart).
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'server-keepalive\.bat|serve(_demo)?\.py' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Servers stopped. Double-click start-background.vbs to start them again.
pause
