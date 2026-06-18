"""
Dashboard — SantokrOCR 主入口

功能：
1. 筛选和浏览已处理烘焙会话
2. 启动实时识别、处理离线数据源、管理咖啡豆
3. 查看原始数据（raw data），跳转到 RecognitionWindow
"""

import os
import sys
import tkinter as tk
from tkinter import ttk
from typing import Optional

from data.types import BeanRecord
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.bean_repo import SqliteBeanRepository
from ui.widgets.session_grid_widget import SessionGridWidget
from utils.screen_utils import center_window

class Dashboard(tk.Tk):
    """主仪表盘窗口"""

    def __init__(self):
        super().__init__()

        self.title("SantokrOCR Dashboard")
        self.minsize(1100, 600)
        center_window(self, 3200, 1900)

        # 设置图标
        try:
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
                icon_path = os.path.join(base_path, 'icon.ico')
            else:
                base_path = os.path.dirname(os.path.abspath(__file__))
                icon_path = os.path.join(base_path, '..', 'icon.ico')
            if os.path.exists(icon_path):
                self.iconbitmap(default=icon_path)
        except Exception:
            pass

        # 数据库
        self._session_repo = SqliteSessionRepository()
        self._bean_repo = SqliteBeanRepository()

        # 缓存：bean_id -> BeanRecord
        self._bean_map: dict[int, BeanRecord] = {}

        # 单例窗口引用
        self._realtime_window: Optional[tk.Toplevel] = None

        # 先加载豆信息，供 SessionGridWidget 初始化时使用
        self._load_bean_map()

        # 创建 UI
        self._create_ui()

        # 加载原始数据列表
        self._load_raw_data()

        # 绑定快捷键
        self.bind('<Control-q>', lambda e: self._on_closing())

        # 关闭事件
        self.protocol('WM_DELETE_WINDOW', self._on_closing)

        self._update_status('就绪')

    def _load_bean_map(self):
        """加载 bean_id -> BeanRecord 映射"""
        beans = self._bean_repo.list_all()
        self._bean_map = {}
        for b in beans:
            bid = b.get('id')
            if bid is not None:
                self._bean_map[bid] = b

    # ================================================================
    # UI 创建
    # ================================================================

    def _create_ui(self):
        # 主容器
        main_frame = ttk.Frame(self)
        main_frame.pack(fill='both', expand=True, padx=10, pady=5)

        # 可拖拽分栏
        self._paned = ttk.PanedWindow(main_frame, orient='horizontal')
        self._paned.pack(fill='both', expand=True)

        # --- 左：SessionGridWidget（筛选 + 列表） ---
        left_frame = ttk.Frame(self._paned)
        self._paned.add(left_frame, weight=70)

        self._session_grid = SessionGridWidget(
            left_frame, self._session_repo, self._bean_map,
            select_mode='extended',
            on_activate=self._on_grid_double_click,
            on_context_menu=self._on_grid_right_click,
            on_status=self._update_status,
        )
        self._session_grid.pack(fill='both', expand=True)

        # --- 右：功能区 + raw data ---
        right_frame = ttk.Frame(self._paned)
        self._paned.add(right_frame, weight=30)

        self._create_function_area(right_frame)
        self._create_raw_data_area(right_frame)

        # --- 底：状态栏 ---
        self._create_status_bar()

    def _create_function_area(self, parent):
        frame = ttk.LabelFrame(parent, text='功能区', padding=8)
        frame.pack(fill='x', pady=(0, 10))

        ttk.Button(frame, text='开启实时识别',
                   command=self._open_realtime).pack(fill='x', pady=2)
        ttk.Button(frame, text='处理离线数据源',
                   command=self._open_offline_source).pack(fill='x', pady=2)
        ttk.Button(frame, text='管理咖啡豆',
                   command=self._open_bean_manager).pack(fill='x', pady=2)

    def _create_raw_data_area(self, parent):
        frame = ttk.LabelFrame(parent, text='待处理原始数据', padding=8)
        frame.pack(fill='both', expand=True)

        # Treeview（两列：名称、时间）
        self._raw_tree = ttk.Treeview(
            frame, columns=('time',), show='tree headings',
        )
        self._raw_tree.heading('#0', text='名称')
        self._raw_tree.heading('time', text='创建时间')
        self._raw_tree.column('#0', width=150, minwidth=100)
        self._raw_tree.column('time', width=90, minwidth=70)

        scroll = ttk.Scrollbar(frame, orient='vertical',
                               command=self._raw_tree.yview)
        self._raw_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self._raw_tree.pack(side='left', fill='both', expand=True)

        # 双击占位（Phase 2 实现）
        self._raw_tree.bind('<Double-1>', self._on_raw_data_double_click)

    def _create_status_bar(self):
        frame = ttk.Frame(self, relief='sunken', borderwidth=1)
        frame.pack(side='bottom', fill='x', padx=10, pady=5)

        self._status_label = ttk.Label(frame, text='就绪')
        self._status_label.pack(side='left', padx=10, pady=5)

    # ================================================================
    # 数据加载
    # ================================================================

    def _load_raw_data(self):
        """加载 is_raw_data=True 的会话到右侧列表"""
        # 清空
        for item in self._raw_tree.get_children():
            self._raw_tree.delete(item)

        raw = self._session_repo.list_filtered(is_raw_data=True)

        for s in raw:
            name = s.get('notes', '') or s.get('session_id', '')
            sid = s.get('session_id', '')
            created = s.get('created_at', '') or ''
            # 截短时间戳，只取到分钟
            if len(created) > 16:
                created = created[:16]
            self._raw_tree.insert('', 'end', iid=sid, text=name,
                                  values=(created,))

    # ================================================================
    # 交互
    # ================================================================

    def _on_grid_double_click(self, session_id: str):
        """打开 SlogViewer（通过 session_id 从 DB 加载）"""
        from ui.slog_viewer import open_slog_viewer
        open_slog_viewer(self, session_id=session_id)

    def _on_grid_right_click(self, selected_ids, x_root, y_root):
        """右键菜单：回退到原始数据、星标、对比曲线"""
        ids = list(selected_ids)
        menu = tk.Menu(self, tearoff=0)
        is_multi = len(ids) >= 2

        # 回退到原始数据（多选时 disabled）
        menu.add_command(label="回退到原始数据",
                         command=lambda: self._revert_to_raw(ids))
        if is_multi:
            menu.entryconfig("回退到原始数据", state="disabled")

        # 星标（多选时 disabled）
        if is_multi:
            fav_label = "星标"
        else:
            fav_label = "取消星标" if self._session_repo.load(ids[0]).get('is_favorite') else "星标"
        menu.add_command(label=fav_label,
                         command=lambda: self._toggle_favorite(ids))
        if is_multi:
            menu.entryconfig(fav_label, state="disabled")

        # 对比曲线（仅多选时显示）
        if is_multi:
            menu.add_separator()
            menu.add_command(label=f"对比 {len(ids)} 条曲线",
                             command=lambda: self._open_comparer(ids))

        menu.tk_popup(x_root, y_root)

    def _open_comparer(self, session_ids):
        """打开 SlogComparer（多选会话对比）"""
        from ui.slog_comparer import SlogComparer
        SlogComparer(self, session_ids=list(session_ids))

    def _revert_to_raw(self, ids):
        """回退到原始数据：确认 → 更新 DB → 刷新左右 grid"""
        if not tk.messagebox.askyesno("确认",
                                      "确定要将选中记录回退到原始数据吗？"):
            return
        for sid in ids:
            session = self._session_repo.load(sid)
            if session is None:
                continue
            updates: dict = {'is_raw_data': 1}
            if not session.get('notes', '').strip():
                updates['notes'] = self._session_repo.get_display_name(sid)
            self._session_repo.update_fields(sid, **updates)
        self._session_grid.refresh()
        self._load_raw_data()

    def _toggle_favorite(self, ids):
        """切换星标状态"""
        for sid in ids:
            session = self._session_repo.load(sid)
            if session is None:
                continue
            new_val = 0 if session.get('is_favorite') else 1
            self._session_repo.update_fields(sid, is_favorite=new_val)
        self._session_grid.refresh()

    def _on_raw_data_double_click(self, event):
        """打开 RecognitionWindow(mode='raw_data')"""
        selection = self._raw_tree.selection()
        if not selection:
            return
        # iid 中存储了 session_id
        session_id = selection[0]
        from ui.recognition_window import RecognitionWindow
        rw = RecognitionWindow(self, mode='raw_data', session_id=session_id)
        rw.bind('<Destroy>', self._on_child_destroy)

    def _open_realtime(self):
        """开启实时识别（单例）"""
        if self._realtime_window is not None:
            try:
                if self._realtime_window.winfo_exists():
                    self._realtime_window.lift()
                    self._realtime_window.focus_set()
                    return
            except tk.TclError:
                pass
        from ui.camera_realtime_window import CameraRealtimeWindow
        self._realtime_window = CameraRealtimeWindow(self)
        self._realtime_window.bind('<Destroy>', self._on_child_destroy)
        self._update_status('已打开实时识别窗口')

    def _open_offline_source(self):
        """打开 RecognitionWindow(mode='video')"""
        from ui.recognition_window import RecognitionWindow
        rw = RecognitionWindow(self, mode='video')
        rw.bind('<Destroy>', self._on_child_destroy)

    def _open_bean_manager(self):
        """管理咖啡豆"""
        from ui.bean_manager import BeanManager

        def _on_bean_saved():
            self._load_bean_map()
            self._session_grid.refresh_bean_map(self._bean_map)

        BeanManager(self, on_save_callback=_on_bean_saved)

    def _update_status(self, message: str):
        self._status_label.configure(text=message)

    def _on_child_destroy(self, event):
        """子窗口关闭时自动刷新 raw data（只响应 Toplevel 自身）"""
        if event.widget == event.widget.winfo_toplevel():
            self._load_raw_data()

    def _on_closing(self):
        # 实时识别窗口打开时提示
        if self._realtime_window is not None:
            try:
                if self._realtime_window.winfo_exists():
                    self._realtime_window._on_closing()
                    if self._realtime_window.winfo_exists():
                        return
            except tk.TclError:
                pass
        self.quit()
        self.destroy()
