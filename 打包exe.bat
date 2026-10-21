@echo off
chcp 65001 >nul
cd /d %~dp0
set PY=
py -3 -c "import sys" >nul 2>nul && set PY=py -3
if not defined PY python -c "import sys" >nul 2>nul && set PY=python
if not defined PY (
    echo 没有找到 Python，请先安装 https://www.python.org/downloads/ （勾选 Add python.exe to PATH）
    pause
    exit /b 1
)
%PY% -m pip install -r requirements.txt pyinstaller
%PY% -m PyInstaller -F -w -n LanKVM lankvm.py
echo.
echo 完成：dist\LanKVM.exe （拷到其他 Windows 电脑可直接运行，无需安装 Python）
pause
