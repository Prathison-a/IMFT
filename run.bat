@echo off
title BackTester - Python Edition
echo ========================================================
echo   Launching BackTester Python Quantitative Backtester
echo ========================================================
echo.
cd /d "%~dp0"

rem Check local virtual environment first, then conda env, then system python
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=%USERPROFILE%\.conda\envs\llm_dev\python.exe"
)

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" main.py
pause

