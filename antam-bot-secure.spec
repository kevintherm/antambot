# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['bot', 'captcha', 'requests', 'undetected_chromedriver', 'selenium', 'selenium.webdriver.common.by', 'selenium.webdriver.support.ui', 'selenium.webdriver.support', 'selenium.webdriver.support.expected_conditions', 'selenium.webdriver.support.wait', 'logging', 'shutil', 'time', 'datetime', 're', 'json', 'yaml'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='antam-bot-secure',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
