@echo off
setlocal
cd /d "%~dp0"

rem Prefer the Windows "py" launcher, fall back to "python" on PATH.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 clientbridge_api.py %*
) else (
    python clientbridge_api.py %*
)

rem Keep the window open when the server stops so the user can read errors.
if errorlevel 1 pause
