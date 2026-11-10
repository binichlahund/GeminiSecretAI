@echo off
rem Stops only the local Gemini Hotkey bridge on port 8001, then starts the updated app.
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force } }"
timeout /t 1 /nobreak >nul
start "" wscript.exe "%~dp0start_hidden.vbs"

