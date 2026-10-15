@echo off
cd /d "%~dp0"
echo ===================================================
echo Starting AI Job Agent Background Pipeline...
echo ===================================================
echo.
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" main.py
) else (
    python main.py
)
echo.
pause
