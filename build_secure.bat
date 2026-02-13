@echo off
setlocal

:: Check/Install dependencies
python -c "import Cython" 2>nul
if %errorlevel% neq 0 (
    echo Installing Cython...
    pip install Cython
)
python -c "import PyInstaller" 2>nul
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

:: Clean previous builds
echo Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist antam-bot.spec del antam-bot.spec
if exist antam-bot-secure.spec del antam-bot-secure.spec

:: Create backup directory
if not exist src_backup mkdir src_backup

:: Step 1: Compile modules with Cython
echo Compiling modules to C extensions...
python setup.py build_ext --inplace
if %errorlevel% neq 0 (
    echo [ERROR] Compilation failed. 
    echo Ensure you have a C compiler installed (e.g., Visual Studio Build Tools with "Desktop development with C++"^).
    exit /b 1
)

:: Step 2: Hide original Python files (move to backup)
echo Hiding original source files...
move bot.py src_backup\ >nul
move captcha.py src_backup\ >nul

:: Step 3: PyInstaller build
echo Building executable...
pyinstaller --onefile --name antam-bot-secure --clean main.py ^
    --hidden-import bot ^
    --hidden-import captcha ^
    --hidden-import requests ^
    --hidden-import undetected_chromedriver ^
    --hidden-import selenium ^
    --hidden-import selenium.webdriver.common.by ^
    --hidden-import selenium.webdriver.support.ui ^
    --hidden-import selenium.webdriver.support ^
    --hidden-import selenium.webdriver.support.expected_conditions ^
    --hidden-import selenium.webdriver.support.wait ^
    --hidden-import logging ^
    --hidden-import shutil ^
    --hidden-import time ^
    --hidden-import datetime ^
    --hidden-import re ^
    --hidden-import json ^
    --hidden-import yaml

if %errorlevel% neq 0 (
    echo PyInstaller build failed.
    goto restore
)

:: Step 4: Restore source files
:restore
echo Restoring source files...
if exist src_backup\bot.py move src_backup\bot.py . >nul
if exist src_backup\captcha.py move src_backup\captcha.py . >nul
if exist src_backup rmdir src_backup

:: Step 5: Clean up compiled artifacts
echo Cleaning up artifacts...
if exist build rmdir /s /q build
del *.c 2>nul
del *.pyd 2>nul 
:: Note: Windows Cython produces .pyd files (Python DLLs), not .so

if exist dist\antam-bot-secure.exe (
    move dist\antam-bot-secure.exe . >nul
    echo.
    echo ========================================================
    echo  Build success! Executable is at: antam-bot-secure.exe
    echo  Make sure 'creds.yaml' is in the same directory.
    echo ========================================================
) else (
    echo Build failed or executable not found.
    exit /b 1
)

endlocal
