@echo off
cd /d %~dp0
echo [Emerald] 鉴权初始化（幂等，可重复运行；全员换钥匙加参数 --rotate-all）
echo.
where python >nul 2>nul
if %errorlevel%==0 (
  python scripts\setup_auth.py %*
) else (
  py scripts\setup_auth.py %*
)
echo.
echo ============================================================
echo  凭据已写入 secrets.local.yaml（已 gitignore，请勿提交）
echo  各 token 的配置位置与轮换方法见 docs\token-rotation.md
echo  管理面板: http://127.0.0.1:8080 （登录 token 见密码本 admin-panel 条目）
echo ============================================================
pause
