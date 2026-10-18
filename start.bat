@echo off
rem One-click launcher for Windows. Always uses the project's own .venv Python,
rem so an activated conda/base environment cannot get in the way.
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [setup] Creating the virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 goto :fail
    echo [setup] Installing dependencies ...
    "%PY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :fail
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo [config] Created .env from .env.example.
    echo          Put your API key in .env, save the file, then run start.bat again.
    echo          Example:  ANTHROPIC_API_KEY=sk-ant-...   ^(no space after =^)
    start "" notepad ".env"
    goto :end
)

echo [check] Checking configuration and API access ...
"%PY%" -m story_agent --check
if errorlevel 1 goto :fail

echo.
echo [run] Starting the web interface. Close this window or press Ctrl+C to stop.
"%PY%" -m story_agent.web --open
goto :end

:fail
echo.
echo Startup stopped. Read the messages above, fix .env, then run start.bat again.

:end
pause
