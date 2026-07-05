@echo off
REM 用法: watchdog_napcat.bat <你的QQ号> [NapCat launcher 路径]
REM 例:   watchdog_napcat.bat 10001 "D:\NapCat\launcher_auto.bat"
if "%~1"=="" (
    echo [watchdog] 缺少参数: 请传入 QQ 号，例如 watchdog_napcat.bat 10001
    exit /b 1
)
set "QQ_ACCOUNT=%~1"
set "NAPCAT_LAUNCHER=%~2"
if "%NAPCAT_LAUNCHER%"=="" set "NAPCAT_LAUNCHER=D:\NapCat\launcher_auto.bat"
echo [watchdog] NapCat 守护进程已启动 (QQ=%QQ_ACCOUNT%)
:loop
tasklist | findstr "NapCatWinBootMain" >nul
if errorlevel 1 (
    echo [%time%] NapCat 已掉线，正在重启...
    start "" "%NAPCAT_LAUNCHER%" %QQ_ACCOUNT%
    timeout /t 20 /nobreak >nul
    echo [%time%] NapCat 已重启
) else (
    echo [%time%] NapCat 运行正常
)
timeout /t 60 /nobreak >nul
goto loop
