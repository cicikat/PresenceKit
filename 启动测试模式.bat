@echo off
chcp 65001 >nul
echo.
echo ╔══════════════════════════════════════╗
echo ║        角色 · 测试模式启动           ║
echo ║  数据隔离在 data/test_sandbox/       ║
echo ║  不会污染角色的真实记忆              ║
echo ╚══════════════════════════════════════╝
echo.

cd /d %~dp0

where python >nul 2>nul
if %errorlevel%==0 (
  python run_test.py
) else (
  py run_test.py
)

echo.
pause
