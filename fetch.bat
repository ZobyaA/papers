@echo off
chcp 65001 >nul
set ARXIV_SCHEDULED=1
set PYTHON=C:\Users\<你的用户名>\miniconda3\envs\quantum\python.exe
set PROJECT_DIR=C:\Users\<你的用户名>\Desktop\ClaudeCode

cd /d "%PROJECT_DIR%"

echo [%date% %time%] === fetch started (delay 1h) ===
timeout /t 3600 /nobreak > nul

echo [%date% %time%] [NEW] retrieve_qubit...
%PYTHON% papers\retrieve_qubit.py --days 3

echo [%date% %time%] [NEW] retrieve_fab...
%PYTHON% papers\retrieve_fab.py --days 3 --max-results 999

echo [%date% %time%] === fetch done ===
