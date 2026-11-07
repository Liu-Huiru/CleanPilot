@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo   CleanPilot 清源 启动中...
echo ==========================================
python app.py --demo
pause
