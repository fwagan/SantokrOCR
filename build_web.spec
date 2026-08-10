# -*- mode: python ; coding: utf-8 -*-
"""Mobile Event Marker Web 进程 PyInstaller Spec

输出：build/web/WebServer/WebServer.exe（onedir，独立可执行，不涉及解压）
运行：pyinstaller -y --distpath build/web --workpath build/web_pyi build_web.spec
（build_web.bat 会先构建前端 dist 再调用本 spec）

web/backend/main.py 是 GUI 入口（单实例/端口冲突/uvicorn）。
前端产物 web/frontend/dist 通过 datas 打入 onedir 的 _internal，
server.py 的 _mount_static 在 frozen 模式下从 _MEIPASS/web/frontend/dist 挂载。
"""
import os

from PyInstaller.utils.hooks import collect_submodules

project_root = os.getcwd()

# 前端构建产物存在才打入（build_web.bat 先跑 npm run build）
_dists = os.path.join(project_root, 'web', 'frontend', 'dist')
datas = []
if os.path.isdir(_dists):
    datas.append((_dists, 'web/frontend/dist'))

a = Analysis(
    ['web/backend/main.py'],
    pathex=[project_root],
    binaries=[],
    hiddenimports=[
        # Web 后端包
        'web.backend.main',
        'web.backend.server',
        'web.backend.ipc_client',
        'web.backend.config',
        # FastAPI / uvicorn / starlette 动态导入较多，显式收集
        'fastapi',
        *collect_submodules('fastapi'),
        *collect_submodules('uvicorn'),
        *collect_submodules('starlette'),
    ],
    datas=datas,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 主程序重型依赖，Web 进程不需要（保持打包体积小）
        'cv2', 'numpy', 'matplotlib', 'PIL', 'pandas', 'scipy',
        'PySide6', 'PySide6.QtCore', 'PySide6.QtGui',
        'PySide6.QtWidgets', 'PySide6.QtNetwork', 'shiboken6',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WebServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
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
    name='WebServer',
)
