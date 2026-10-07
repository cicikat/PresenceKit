@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 scripts\backup_weekly.py
) else (
    python -X utf8 scripts\backup_weekly.py
)
set "BACKUP_EXIT=%ERRORLEVEL%"
pause
exit /b %BACKUP_EXIT%
