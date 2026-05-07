# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MT5 Copy Manual
# Build: pyinstaller mt5_copy.spec --clean --noconfirm

a = Analysis(
    ['main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('config/settings.json',              'config'),
        ('data/mappings/ticket_mapping.csv',  'data/mappings'),
        ('mql5',                              'mql5'),
    ],
    hiddenimports=[
        # pyautogui sub-dependencies
        'pyscreeze',
        'pytweening',
        'mouseinfo',
        'pygetwindow',
        'pyrect',
        'pyperclip',
        # Pillow
        'PIL.Image',
        'PIL.ImageOps',
        'PIL.ImageGrab',
        'PIL.ImageTk',
        # OpenCV
        'cv2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MT5CopyManual',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MT5CopyManual',
)
