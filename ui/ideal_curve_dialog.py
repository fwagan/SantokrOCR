"""
IdealCurveDialog — 从数据库中选择理想曲线会话

纯选择器，不含数据处理逻辑。返回 session_id 后由调用方加载和处理。
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional

from data.sqlite.bean_repo import SqliteBeanRepository
from data.sqlite.session_repo import SqliteSessionRepository
from ui.widgets.session_grid_widget import SessionGridWidget


class IdealCurveDialog(tk.Toplevel):
    """选择理想曲线的对话框（纯选择器，返回 session_id）"""

    def __init__(
        self,
        parent,
        session_repo: SqliteSessionRepository,
        bean_repo: SqliteBeanRepository,
    ):
        super().__init__(parent)
        self._result: Optional[str] = None

        self.title('选择理想曲线')
        self.minsize(700, 400)
        self.transient(parent)
        self.grab_set()

        # 居中
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 750, 480
        self.geometry(f'{w}x{h}+{px + (pw - w) // 2}+{py + (ph - h) // 2}')

        # 加载豆信息
        beans = bean_repo.list_all()
        bean_map = {}
        for b in beans:
            bid = b.get('id')
            if bid is not None:
                bean_map[bid] = b

        # SessionGridWidget（单选模式）
        self._grid = SessionGridWidget(
            self, session_repo, bean_map,
            select_mode='browse',
            on_activate=self._on_select,
        )
        self._grid.pack(fill='both', expand=True, padx=10, pady=(10, 0))

        # 底部按钮
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill='x', padx=10, pady=(5, 10))

        ttk.Button(btn_frame, text='确定', command=self._on_ok).pack(side='right', padx=(4, 0))
        ttk.Button(btn_frame, text='取消', command=self._on_cancel).pack(side='right')

        self.protocol('WM_DELETE_WINDOW', self._on_cancel)
        self.bind('<Escape>', lambda e: self._on_cancel())

        # 等待窗口关闭
        self.wait_window()

    @property
    def result(self) -> Optional[str]:
        """用户选中的 session_id，取消则返回 None"""
        return self._result

    def _on_select(self, session_id: str):
        """双击选中"""
        self._result = session_id
        self.destroy()

    def _on_ok(self):
        selected = self._grid.get_selected_ids()
        if not selected:
            return
        self._result = selected[0]
        self.destroy()

    def _on_cancel(self):
        self._result = None
        self.destroy()
