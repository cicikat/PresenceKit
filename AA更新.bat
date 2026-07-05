@echo off
echo 正在更新...
git pull
echo 更新完成，重新安装依赖中...
python -m pip install -r requirements.txt
echo 完成，按任意键退出
pause
