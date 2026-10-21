@echo off
chcp 65001 >nul
cd /d %~dp0
call :findpy
if not defined PY goto nopy

%PY% -c "import pynput, pyperclip" >nul 2>nul
if errorlevel 1 (
    echo 首次运行，正在安装依赖...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

for /f "delims=" %%i in ('%PY% -c "import sys;print(sys.executable)"') do set PYEXE=%%i
for %%d in ("%PYEXE%") do set PYW=%%~dpdpythonw.exe
if exist "%PYW%" (
    start "" "%PYW%" lankvm.py
) else (
    %PY% lankvm.py
)
exit /b 0

:findpy
set PY=
py -3 -c "import sys" >nul 2>nul && set PY=py -3 && exit /b
python -c "import sys" >nul 2>nul && set PY=python && exit /b
python3 -c "import sys" >nul 2>nul && set PY=python3 && exit /b
for %%p in ("%LOCALAPPDATA%\Programs\Python\Python3*\python.exe" "C:\Program Files\Python3*\python.exe") do (
    if exist "%%~p" set PY="%%~p"
)
exit /b

:nopy
echo.
echo 没有找到 Python。
echo 请到 https://www.python.org/downloads/ 下载安装 Python 3，
echo 安装时务必勾选 "Add python.exe to PATH"，装好后重新双击本文件。
echo.
echo 或者在已装好 Python 的电脑上运行"打包exe.bat"，生成 LanKVM.exe 后拷过来直接用。
start https://www.python.org/downloads/
pause
exit /b 1
