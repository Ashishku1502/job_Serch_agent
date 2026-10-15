@echo off
cd /d "%~dp0"
echo ===================================================
echo Starting AI Job Agent Command Center Dashboard...
echo Dashboard will be available at http://localhost:8000
echo ===================================================
echo.
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" server.py
) else (
    python server.py
)
echo.
pause
