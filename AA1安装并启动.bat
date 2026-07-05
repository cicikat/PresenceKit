@echo off
echo 正在检查Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo 未检测到Python，请先安装Python 3.12+
    echo 正在打开下载页面...
    start https://www.python.org/downloads/
    pause
    exit
)
echo 正在安装依赖...
python -m pip install -r requirements.txt
if not exist "config.yaml" (
    copy config.example.yaml config.yaml
    echo.
    echo 已生成config.yaml，请用记事本打开填写配置
    echo 填写完成后双击"启动.bat"运行
    pause
    exit
)
echo 启动中...
python main.py
pause
