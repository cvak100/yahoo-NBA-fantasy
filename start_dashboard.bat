@echo off
cd /d "%~dp0"
echo Stopping anything on port 5050...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5050" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
timeout /t 1 /nobreak >nul
echo.
echo Open: http://127.0.0.1:5050
echo.
".venv\Scripts\python.exe" main.py
pause
