@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1"
if errorlevel 1 (echo Could not update the Startup shortcut.& pause & exit /b 1)
echo Autostart enabled. Remove its shortcut from the Windows Startup folder to disable.
pause
