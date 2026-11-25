@echo off
setlocal
cd /d "%~dp0"
echo GeminiSecretAI - Installation
echo.
python --version
if errorlevel 1 (
  echo Install Python 3.11 or newer and select Add Python to PATH.
  pause
  exit /b 1
)
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (echo Installation failed. See the error above.& pause & exit /b 1)
where pythonw >nul 2>nul
if errorlevel 1 (echo pythonw was not found. Repair your Python PATH.& pause & exit /b 1)
echo.
echo Installed. Double-click "Start GeminiSecretAI.vbs" to open the app.
pause
