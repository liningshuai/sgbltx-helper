@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 缺少项目虚拟环境 .venv。请先按 README.md 的环境步骤创建并安装依赖。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m sgbltx_helper dashboard
set "DASHBOARD_EXIT=%ERRORLEVEL%"
if not "%DASHBOARD_EXIT%"=="0" pause
exit /b %DASHBOARD_EXIT%
