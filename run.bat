@echo off
title BackTester - Python Edition
echo ========================================================
echo   Launching BackTester Python Quantitative Backtester
echo ========================================================
echo.
cd /d "%~dp0"

rem Runs in the llm_dev conda environment (has Flask, waitress and the other requirements).
set "PYTHON_EXE=%USERPROFILE%\.conda\envs\llm_dev\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Error: llm_dev conda environment not found at:
    echo   %PYTHON_EXE%
    pause
    exit /b 1
)

"%PYTHON_EXE%" main.py
pause
