@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "答辩速查.exe" (
    start "" "答辩速查.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "launcher.py"
    exit /b
)
echo 请运行答辩速查.exe，或先安装 requirements.txt 中的源码运行依赖。
pause
