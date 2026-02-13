#!/bin/bash
# Install PyInstaller if not present
if ! command -v pyinstaller &> /dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

echo "Cleaning previous builds..."
rm -rf build dist antam-bot.spec

echo "Building executable..."
pyinstaller --onefile --name antam-bot --clean main.py

if [ -f "dist/antam-bot" ]; then
    mv dist/antam-bot .
    echo "Build success! Executable is at: ./antam-bot"
    echo "Make sure 'creds.yaml' is in the same directory."
    chmod +x antam-bot
else
    echo "Build failed."
    exit 1
fi
