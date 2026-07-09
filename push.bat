@echo off
chcp 65001 >nul

REM === 自动推导项目根目录（%~dp0 = 此脚本所在目录，.. 即项目根） ===
cd /d "%~dp0.."

echo [%date% %time%] push start >> papers\data\push.log 2>nul
conda run -n quantum python papers\push_papers.py >> papers\data\push.log 2>&1
echo [%date% %time%] push done (exit %errorlevel%) >> papers\data\push.log 2>nul
