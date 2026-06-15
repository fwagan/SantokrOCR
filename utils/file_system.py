"""
文件系统操作管理

统一管理文件对话框、缓存路径、永久存储路径等文件系统操作。
"""

import os
import shutil
from tkinter import filedialog
from typing import Optional


class Paths:
    """路径管理"""

    @staticmethod
    def _base_dir() -> str:
        """获取 SantokrOCR 配置根目录"""
        app_data = os.environ.get(
            'APPDATA',
            os.path.expanduser('~/.local/share'),
        )
        return os.path.join(app_data, 'SantokrOCR')

    @classmethod
    def db(cls) -> str:
        """获取默认 SQLite 数据库路径"""
        return os.path.join(cls._base_dir(), 'santokr.db')

    @classmethod
    def frame_captures(cls, session_id: str) -> str:
        """获取指定会话的永久帧截图目录"""
        return os.path.join(cls._base_dir(), 'FrameCaptures', session_id)

    @classmethod
    def ensure_frame_captures(cls, session_id: str) -> str:
        """获取并创建帧截图目录"""
        path = cls.frame_captures(session_id)
        os.makedirs(path, exist_ok=True)
        return path


class FileDialogs:
    """文件对话框"""

    @staticmethod
    def open_video(parent) -> Optional[str]:
        """打开选择数据源文件对话框"""
        return filedialog.askopenfilename(
            parent=parent,
            title='选择数据源文件',
            filetypes=[
                ('支持的文件', '*.mp4 *.mov *.avi *.mkv *.srlog'),
                ('视频文件', '*.mp4 *.mov *.avi *.mkv'),
                ('会话文件', '*.srlog'),
                ('所有文件', '*.*'),
            ],
        )


class FileOperations:
    """文件操作"""

    @staticmethod
    def copy_frames(src_dir: str, dst_dir: str) -> int:
        """复制帧截图文件

        Args:
            src_dir: 源目录
            dst_dir: 目标目录

        Returns:
            复制的文件数量
        """
        if not os.path.isdir(src_dir):
            return 0
        os.makedirs(dst_dir, exist_ok=True)
        count = 0
        for fname in os.listdir(src_dir):
            src = os.path.join(src_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, dst_dir)
                count += 1
        return count

    @staticmethod
    def remove_dir(path: str) -> None:
        """安全删除目录"""
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
