@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "PYTHON=%PROJECT_ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Python environment not found. Running setup...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%setup.ps1"
    if errorlevel 1 (
        echo Setup failed.
        pause
        exit /b 1
    )
)

"%PYTHON%" -m outplayed_highlight_cutter
if errorlevel 1 (
    echo.
    echo Application exited with an error.
    pause
    exit /b 1
)

endlocal
