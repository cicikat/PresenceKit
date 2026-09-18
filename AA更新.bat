@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

rem Do not pull while this copy of the service is still running.
wmic process where "name='python.exe' or name='pythonw.exe'" get CommandLine 2>nul | findstr /I /C:"main.py" >nul
if not errorlevel 1 (
    echo 检测到 PresenceKit 服务仍在运行。
    echo 请先停止服务，再重新运行本更新脚本；这样可避免依赖同步时中断正在使用的环境。
    pause
    exit /b 1
)

if not exist ".git" goto :release_package

set "STATUS_FILE=%TEMP%\presencekit-update-status-%RANDOM%.txt"
git status --porcelain > "%STATUS_FILE%"
for %%A in ("%STATUS_FILE%") do if %%~zA GTR 0 goto :confirm_dirty
goto :pull

:confirm_dirty
echo.
echo 检测到本地未提交改动。更新不会主动覆盖 data、config.yaml 或 secrets，
echo 但 git pull 可能因同一文件冲突而中止。以下是当前改动：
type "%STATUS_FILE%"
set /p "CONFIRM=仍要继续拉取更新吗？输入 Y 继续，其他任意键取消: "
if /I not "%CONFIRM%"=="Y" (
    del "%STATUS_FILE%" >nul 2>nul
    echo 已取消，未执行更新。
    pause
    exit /b 0
)

:pull
del "%STATUS_FILE%" >nul 2>nul
echo 正在获取程序更新...
git pull
if errorlevel 1 goto :pull_failed

echo 更新完成，正在同步依赖...
set "UV_EXE=%~dp0tools\uv.exe"
if not exist "%UV_EXE%" (
    where uv >nul 2>nul
    if not errorlevel 1 (
        set "UV_EXE=uv"
    ) else (
        set "UV_EXE="
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo 未找到 .venv，请先运行 "AA1安装并启动.bat"。
    pause
    exit /b 1
)

if not "%UV_EXE%"=="" (
    "%UV_EXE%" pip sync requirements.lock --python .venv\Scripts\python.exe
) else (
    ".venv\Scripts\python.exe" -m pip install -r requirements.lock
)
if errorlevel 1 (
    echo 依赖同步失败。程序文件没有被本脚本回滚；请检查网络或终端报错后重试。
    pause
    exit /b 1
)

echo 正在核对完整运行依赖（图片/Word/搜索等）...
".venv\Scripts\python.exe" scripts\check_runtime_deps.py
if errorlevel 1 (
    echo 依赖已同步，但完整运行包未通过导入核对。请检查上方缺失项后重试。
    pause
    exit /b 1
)

for /f "usebackq delims=" %%V in (`git describe --tags --always 2^>nul`) do set "VERSION=%%V"
if "%VERSION%"=="" set "VERSION=当前提交"
echo 更新完成，当前版本：%VERSION%
pause
exit /b 0

:pull_failed
echo.
echo git pull 未完成。常见原因是网络问题或本地改动与远端同一文件冲突。
echo 你的 data、config.yaml 和 secrets 不会被本脚本删除；请保留终端信息并处理冲突后重试。
pause
exit /b 1

:release_package
echo 当前目录是发行版解压包，正在启动版本更新器...
if not exist ".venv\Scripts\python.exe" (
    echo 未找到 .venv，请先运行 "AA1安装并启动.bat" 完成首次安装。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" scripts\update_release.py
if errorlevel 1 (
    echo 更新未完成。请保留上方错误信息；也可手动下载 release zip 后仅覆盖程序文件。
    pause
    exit /b 1
)
pause
exit /b 0
