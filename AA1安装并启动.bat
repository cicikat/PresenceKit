@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================================
echo   Emerald-Presence 安装并启动（uv 引导，首次运行需联网）
echo ============================================================
echo.

if not "%UV_PYTHON_INSTALL_MIRROR%"=="" goto :have_mirror_choice
if not "%UV_INDEX_URL%"=="" goto :have_mirror_choice
choice /C YN /N /M "访问 GitHub/PyPI 官方源较慢的话，是否切换国内镜像？[Y/N] "
if errorlevel 2 goto :have_mirror_choice
set "UV_PYTHON_INSTALL_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/python-build-standalone"
set "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
echo 已切换至清华镜像（仅本次运行生效）。
:have_mirror_choice

set "UV_EXE=%~dp0tools\uv.exe"
if exist "%UV_EXE%" goto :have_uv

where uv >nul 2>nul
if %errorlevel%==0 (
    set "UV_EXE=uv"
    goto :have_uv
)

echo 未找到内置 tools\uv.exe，正在联网安装 uv...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
    goto :have_uv
)
echo.
echo uv 安装失败，请检查网络连接后重试；
echo 或手动从 https://github.com/astral-sh/uv/releases 下载 uv.exe 放到本目录的 tools\ 文件夹下再重新运行本脚本。
pause
exit /b 1

:have_uv
echo 正在安装 Python 3.12（已安装则自动跳过，可能需要联网下载）...
"%UV_EXE%" python install 3.12
if errorlevel 1 goto :fail

echo 正在创建虚拟环境 .venv ...
"%UV_EXE%" venv --python 3.12 .venv
if errorlevel 1 goto :fail

echo 正在按锁文件安装依赖（requirements.lock，保证版本可复现）...
"%UV_EXE%" pip sync requirements.lock --python .venv\Scripts\python.exe
if errorlevel 1 goto :fail

if not exist "config.yaml" (
    copy config.example.yaml config.yaml >nul
    echo.
    echo 已生成 config.yaml，请用记事本打开填写配置
    echo 填写完成后依次双击 "AA2鉴权初始化.bat"（首次运行前必做）与 "AA3启动.bat"
    pause
    exit /b 0
)

echo.
echo 环境准备完成，启动中...
".venv\Scripts\python.exe" main.py
pause
exit /b 0

:fail
echo.
echo 安装失败，请检查上方报错信息。国内网络较慢时可重新运行本脚本，在提示时选择切换镜像。
pause
exit /b 1
