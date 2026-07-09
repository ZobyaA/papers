@echo off
chcp 65001 >nul
set ARXIV_SCHEDULED=1

REM === 自动推导项目根目录（%~dp0 = 此脚本所在目录，.. 即项目根） ===
cd /d "%~dp0.."

echo [%date% %time%] === fetch started (delay 1h) ===
timeout /t 3600 /nobreak > nul

echo [%date% %time%] [NEW] retrieve_qubit...
conda run -n quantum python papers\retrieve_qubit.py --days 3

echo [%date% %time%] [NEW] retrieve_fab...
conda run -n quantum python papers\retrieve_fab.py --days 3 --max-results 999

echo [%date% %time%] === fetch done ===
