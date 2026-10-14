# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：生成单文件 exe（免安装 Python，双击即用）
# 打包命令：python -m PyInstaller FinReconServer.spec --noconfirm --clean

a = Analysis(
    ['run_server.py'],
    pathex=[],
    binaries=[],
    datas=[('app/static', 'app/static')],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.dialects.mysql.pymysql',
        'openpyxl',
        'email.mime.multipart',
        'email.mime.text',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pandas', 'numpy', 'matplotlib', 'scipy', 'PIL', 'torch',
        'tensorflow', 'sklearn', 'IPython', 'pytest', 'notebook',
        'PyQt5', 'PySide2', 'PySide6', 'tkinter', 'bokeh', 'numba',
    ],
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
    name='启动服务',
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
