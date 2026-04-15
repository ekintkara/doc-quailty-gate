@echo off
chcp 65001 >nul 2>&1
set DQG_DIR=%~dp0..\..
cd /d "%DQG_DIR%"

echo =========================================================
echo    Doc Quality Gate - Review Wizard
echo =========================================================
echo.

echo Recent .md files:
echo.
dir /s /b *.md 2>nul | findstr /v ".venv node_modules outputs .git README.md AGENTS.md" | more
echo.

set /p DOC_PATH="Enter document path: "

if "%DOC_PATH%"=="" (
    echo No path entered. Exiting.
    pause
    exit /b 1
)

if not exist "%DOC_PATH%" (
    echo File not found: %DOC_PATH%
    pause
    exit /b 1
)

echo.
set /p DOC_TYPE="Document type (feature_spec, implementation_plan, etc) [auto-detect]: "
set /p PROJECT_PATH="Project path for cross-reference [current dir]: "

if "%PROJECT_PATH%"=="" set PROJECT_PATH=.

echo.
call .venv\Scripts\activate.bat

if "%DOC_TYPE%"=="" (
    python -m app.cli review "%DOC_PATH%" --project "%PROJECT_PATH%"
) else (
    python -m app.cli review "%DOC_PATH%" -t %DOC_TYPE% --project "%PROJECT_PATH%"
)

echo.
pause
