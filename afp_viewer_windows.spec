# -*- mode: python ; coding: utf-8 -*-
# Windows build spec — produces dist\AFP Viewer\AFP Viewer.exe

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('samples', 'samples'),   # bundle sample files alongside the exe
    ],
    hiddenimports=[
        'PIL._tkinter_finder',
        'PyQt6.QtPrintSupport',
        'PyQt6.QtSvg',
        'PyQt6.QtXml',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AFP Viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    # icon='assets/AppIcon.ico',  # uncomment once .ico is generated
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AFP Viewer',
)
