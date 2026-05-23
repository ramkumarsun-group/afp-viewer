# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PIL._tkinter_finder',
        'PyQt6.QtPrintSupport',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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
    console=False,          # no terminal window
    disable_windowed_traceback=False,
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
    name='AFP Viewer',
)

app = BUNDLE(
    coll,
    name='AFP Viewer.app',
    icon=None,              # add a .icns file path here if you have one
    bundle_identifier='com.afpviewer.app',
    info_plist={
        'CFBundleName': 'AFP Viewer',
        'CFBundleDisplayName': 'AFP Viewer',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,  # supports dark mode
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'AFP Print File',
                'CFBundleTypeExtensions': ['afp', 'AFP', 'listafp', 'spl'],
                'CFBundleTypeRole': 'Viewer',
            }
        ],
    },
)
