# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        ('frontend', 'frontend'),
        ('database/init_scripts', 'database/init_scripts'),
    ],
    hiddenimports=[
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
    ],
    hookspath=[],
    runtime_hooks=[os.path.join(SPECPATH, 'pyi_rth_crashhandler.py')],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='BeadWatch',
    debug=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=True,
    icon='frontend/static/img/logo.ico'
)
