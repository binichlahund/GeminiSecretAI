@echo off
cd /d "%~dp0"
echo Installing Python dependencies...
python -m pip install -r requirements.txt
python -m playwright install chromium
where pythonw >nul 2>nul || (echo ERROR: Python was not found in PATH. Reinstall Python and select Add to PATH.& pause & exit /b 1)
echo.
echo Done. Start Gemini Hotkey with start_hidden.vbs.
pause

