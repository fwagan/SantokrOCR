# -*- mode: python ; coding: utf-8 -*-
"""
SantokrOCR PyInstaller Spec
"""
import sys
import os
from PyInstaller.utils.hooks import conda_support

project_root = os.getcwd()

conda_dlls = conda_support.collect_dynamic_libs("numpy", dependencies=True)


block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[str(project_root)],
    binaries=conda_dlls,
    hiddenimports=[
        # UI tkinter modules
        'ui.main_window',
        'ui.data_table',
        'ui.async_worker',
        'ui.frame_viewer',
        'ui.roi_selector',
        'ui.statistics_panel',
        'ui.slog_viewer',
        'ui.slog_comparer',
        'ui.bean_manager',
        # Core modules
        'core.video_extractor',
        'core.led_classifier',
        'core.digit_recognizer',
        'core.feature_extractor',
        'core.multi_digit_recognizer',
        'core.projection_segmenter',
        'core.digit_recognition_pipeline',
        'core.white_led_recognizer',
        # Utils
        'utils.cache_manager',
        'utils.screen_utils',
        # Matplotlib backends (dynamic imports, must be explicit)
        'matplotlib.backends.backend_tkagg',
        'matplotlib.backends.backend_agg',
    ],
    datas=[('icon.ico', '.')],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_mpl.py'],
    excludes=[
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'shiboken6',
        'test_doubao_script',
        'test_projection_segmenter',
        'test_multi_digit_integration',
        'test_timer_switch',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SantokrOCR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SantokrOCR',
)
