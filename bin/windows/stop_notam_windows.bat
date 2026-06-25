@echo off
setlocal
cd /d "%~dp0\..\.."

set PID_FILE=%CD%\notam_server.pid

if not exist "%PID_FILE%" (
    echo No PID file found - server may not be running.
    pause
    exit /b 0
)

set /p PID=<"%PID_FILE%"
taskkill /PID %PID% /F >nul 2>&1
if errorlevel 1 (
    echo Process %PID% was not running.
) else (
    echo NOTAM server ^(PID %PID%^) stopped.
)
del "%PID_FILE%"
pause
