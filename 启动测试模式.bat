@echo off
echo.
echo ╔══════════════════════════════════════╗
echo ║        角色 · 测试模式启动           ║
echo ║  数据隔离在 data/test_sandbox/       ║
echo ║  不会污染角色的真实记忆              ║
echo ╚══════════════════════════════════════╝
echo.

cd /d D:\ai\Emerald-presence

C:\Users\10434\AppData\Local\Python\pythoncore-3.14-64\python.exe run_test.py

echo.
pause
