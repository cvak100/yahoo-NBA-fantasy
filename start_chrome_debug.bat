@echo off
REM Open Chrome with remote debugging so the scraper can attach.
REM IMPORTANT: close ALL existing Chrome windows first.

set CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe

REM Persistent profile — Yahoo login survives restarts
set PROFILE=%~dp0.chrome_profile

echo.
echo Starting Chrome with remote debugging (port 9222)...
echo Profile: %PROFILE%
echo.
echo When Chrome is open and you are signed in to Yahoo, run:
echo   .venv\Scripts\python main.py
echo.

start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check "https://basketball.fantasysports.yahoo.com/nba/49605"
