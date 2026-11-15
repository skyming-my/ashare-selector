@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 智选A股 - 本地选股与回测

echo.
echo   ============================================
echo      智选A股  正在启动...
echo      浏览器将自动打开 http://127.0.0.1:8765
echo      关闭本窗口即可退出
echo   ============================================
echo.

py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 goto runpy3

python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 goto runpython

echo   [错误] 未找到 Python 3.10 或更高版本。
echo   请先安装 Python 3.12: https://www.python.org/downloads/
echo   安装时务必勾选 Add python.exe to PATH。
echo.
pause
goto end

:runpy3
py -3 app.py
goto stopped

:runpython
python app.py
goto stopped

:stopped
echo.
echo   服务已退出。若上方有报错，请截图反馈。
pause

:end