@echo off
REM MatchLayer System Refresh Script for Windows
REM Use with Windows Task Scheduler for periodic execution

setlocal enabledelayedexpansion

REM Configuration
set API_URL=http://localhost:8000
set LOG_DIR=%USERPROFILE%\matchlayer\logs
set LOG_FILE=%LOG_DIR%\refresh.log

REM Create log directory if it doesn't exist
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM Timestamp
for /f "tokens=1-4 delims=/ " %%a in ('date /t') do (set MYDATE=%%c-%%a-%%b)
for /f "tokens=1-2 delims=: " %%a in ('time /t') do (set MYTIME=%%a:%%b)
set TIMESTAMP=%MYDATE% %MYTIME%

REM Log start
echo [%TIMESTAMP%] Starting system refresh... >> "%LOG_FILE%"

REM Make API call using curl (requires curl to be installed)
curl -s -X POST "%API_URL%/system/refresh" -H "Content-Type: application/json" --max-time 300 > "%TEMP%\matchlayer_response.txt" 2>&1

REM Check if curl succeeded
if %ERRORLEVEL% EQU 0 (
    echo [%TIMESTAMP%] SUCCESS >> "%LOG_FILE%"
    type "%TEMP%\matchlayer_response.txt" >> "%LOG_FILE%"
) else (
    echo [%TIMESTAMP%] FAILURE >> "%LOG_FILE%"
    type "%TEMP%\matchlayer_response.txt" >> "%LOG_FILE%"
)

echo [%TIMESTAMP%] Refresh completed >> "%LOG_FILE%"
echo --- >> "%LOG_FILE%"

REM Cleanup
del "%TEMP%\matchlayer_response.txt" 2>nul

endlocal
