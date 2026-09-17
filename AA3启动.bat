@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist "config.yaml" (
    echo 未找到config.yaml，请先运行"AA1安装并启动.bat"
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    echo 未找到 .venv，请先运行 "AA1安装并启动.bat"；此处退回系统 Python 尝试...
    python main.py
)
pause
