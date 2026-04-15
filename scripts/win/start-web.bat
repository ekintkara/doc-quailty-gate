@echo off
set DQG_DIR=%~dp0..\..
cd /d "%DQG_DIR%"
call .venv\Scripts\activate.bat
echo Starting Doc Quality Gate Web UI...
echo Open http://localhost:8080 in your browser.
echo Press Ctrl+C to stop.
echo.
python -m app.cli web --port 8080
