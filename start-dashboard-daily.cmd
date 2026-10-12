@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 缺少项目虚拟环境 .venv，请按 README.md 安装依赖。
    pause
    exit /b 1
)
echo 更新后的本地助手使用 http://127.0.0.1:8783/
echo 本入口只启动网页服务，不连接模拟器，不运行领取。按 Ctrl+C 停止。
".venv\Scripts\python.exe" -u -m sgbltx_helper dashboard --port 8783 --no-browser
set "DASHBOARD_DAILY_EXIT=%ERRORLEVEL%"
if not "%DASHBOARD_DAILY_EXIT%"=="0" pause
exit /b %DASHBOARD_DAILY_EXIT%
