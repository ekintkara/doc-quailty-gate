@echo off
set DQG_DIR=%~dp0..\..
cd /d "%DQG_DIR%"
call .venv\Scripts\activate.bat
echo Starting LiteLLM Proxy on port 4000...
echo Press Ctrl+C to stop.
echo.
litellm --config config\litellm\config.yaml --port 4000
