#!/bin/bash
# Install dependencies
if ! pip show Cython > /dev/null; then
    echo "Installing Cython..."
    pip install Cython
fi
if ! pip show pyinstaller > /dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build dist antam-bot.spec

# Create backup directory for source files
mkdir -p src_backup

# Step 1: Compile modules with Cython
echo "Compiling modules to C extensions..."
python setup.py build_ext --inplace

# Step 2: Hide original Python files (so PyInstaller finds the compiled .so files)
echo "Hiding original source files..."
mv bot.py src_backup/
mv captcha.py src_backup/
# You can add main.py here later if you refactor it into a module

# Step 3: PyInstaller build
echo "Building executable..."
# Note: main.py is still Python, but it imports compiled modules
pyinstaller --onefile --name antam-bot-secure --clean main.py \
    --hidden-import bot \
    --hidden-import captcha \
    --hidden-import requests \
    --hidden-import undetected_chromedriver \
    --hidden-import selenium \
    --hidden-import selenium.webdriver.common.by \
    --hidden-import selenium.webdriver.support.ui \
    --hidden-import selenium.webdriver.support \
    --hidden-import selenium.webdriver.support.expected_conditions \
    --hidden-import selenium.webdriver.support.wait \
    --hidden-import logging \
    --hidden-import shutil \
    --hidden-import time \
    --hidden-import datetime \
    --hidden-import re \
    --hidden-import json \
    --hidden-import yaml

# Step 4: Restore source files
echo "Restoring source files..."
mv src_backup/bot.py .
mv src_backup/captcha.py .
rmdir src_backup

# Step 5: Clean up compiled artifacts (optional, keep if you want to verify)
rm -rf build
# rm *.c *.so  <-- keeping .so for now might be useful for debug, but usually we clean them
rm *.c
rm *.so

if [ -f "dist/antam-bot-secure" ]; then
    mv dist/antam-bot-secure .
    echo "Build success! Executable is at: ./antam-bot-secure"
    echo "Make sure 'creds.yaml' is in the same directory."
    chmod +x antam-bot-secure
else
    echo "Build failed."
    # Restore if anything went wrong mid-way
    if [ -d "src_backup" ]; then
        mv src_backup/* . 2>/dev/null
        rmdir src_backup
    fi
    exit 1
fi
