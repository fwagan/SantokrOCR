"""tkinter/Qt 共存兼容层

在渐进迁移过程中，允许从 tkinter 窗口启动 PySide6 QDialog。
迁移完成后删除此文件。
"""

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication


class QtApp:
    """管理 QApplication 生命周期，与 tkinter 共存"""

    _instance = None

    @classmethod
    def get_app(cls):
        """获取或创建 QApplication"""
        if cls._instance is None:
            app = QApplication.instance()
            if app is None:
                app = QApplication(sys.argv)
            cls._instance = app
        return cls._instance

    @classmethod
    def run_dialog(cls, dialog_factory, tk_parent, on_finished=None):
        """从 tkinter 上下文运行 Qt 对话框（非阻塞）

        Args:
            dialog_factory: 返回 QDialog 的可调用对象
            tk_parent: tkinter 父窗口（用于 after() 事件泵）
            on_finished: 对话框关闭后回调
        """
        app = cls.get_app()
        dialog = dialog_factory()

        def pump_qt():
            app.processEvents()
            try:
                tk_parent.after(30, pump_qt)
            except Exception:
                pass

        def on_dialog_finished():
            if on_finished:
                # 延迟执行，避免在 processEvents() 内嵌套调用 tkinter
                QTimer.singleShot(0, on_finished)

        dialog.finished.connect(on_dialog_finished)
        dialog.show()
        pump_qt()
        return dialog
