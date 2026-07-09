@echo off
chcp 65001 >nul

cd /d C:\Users\<你的用户名>\Desktop\ClaudeCode
set PYTHON=C:\Users\<你的用户名>\miniconda3\envs\quantum\python.exe

echo [%date% %time%] push start >> papers\data\push.log 2>nul
%PYTHON% papers\push_papers.py >> papers\data\push.log 2>&1
echo [%date% %time%] push done (exit %errorlevel%) >> papers\data\push.log 2>nul
