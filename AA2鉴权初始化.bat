@echo off
chcp 65001 >nul
cd /d %~dp0
echo [Emerald] 鉴权初始化（幂等，可重复运行；全员换钥匙加参数 --rotate-all）
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\setup_auth.py %*
) else (
  echo 未找到 .venv，请先运行 "AA1安装并启动.bat"；此处退回系统 Python 尝试...
  where python >nul 2>nul
  if %errorlevel%==0 (
    python scripts\setup_auth.py %*
  ) else (
    py scripts\setup_auth.py %*
  )
)

echo.
pause
