"""
Dashboard — SantokrOCR 主入口

功能：
1. 筛选和浏览已处理烘焙会话
2. 启动实时识别、处理离线数据源、管理咖啡豆
3. 查看原始数据（raw data），跳转到 RecognitionWindow
"""

import os
import re
import sys
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

from data.types import BeanRecord
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.bean_repo import SqliteBeanRepository
from utils.screen_utils import center_window

_DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

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

        # 筛选变量
        self._date_from_var = tk.StringVar()
        self._date_to_var = tk.StringVar()
        self._bean_var = tk.StringVar(value='全部')

        # 创建 UI
        self._create_ui()

        # 加载数据
        self._load_bean_map()
        self._populate_bean_combo()
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

        # --- 左：筛选 + grid ---
        left_frame = ttk.Frame(self._paned)
        self._paned.add(left_frame, weight=70)

        self._create_filter_bar(left_frame)
        self._create_grid(left_frame)

        # --- 右：功能区 + raw data ---
        right_frame = ttk.Frame(self._paned)
        self._paned.add(right_frame, weight=30)

        self._create_function_area(right_frame)
        self._create_raw_data_area(right_frame)

        # --- 底：状态栏 ---
        self._create_status_bar()

    def _create_filter_bar(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill='x', pady=(0, 5))

        # 日期起
        ttk.Label(frame, text='日期:').pack(side='left', padx=(0, 2))
        self._date_from_entry = ttk.Entry(
            frame, textvariable=self._date_from_var, width=12,
        )
        self._date_from_entry.pack(side='left', padx=2)
        self._date_from_entry.bind('<FocusIn>', lambda e: e.widget.selection_range(0, 'end'))
        self._date_from_entry.bind('<FocusOut>', lambda e: self._normalize_date_field('from'))

        ttk.Label(frame, text='~').pack(side='left', padx=2)

        # 日期止（失焦时自动修正 date_to < date_from）
        self._date_to_entry = ttk.Entry(
            frame, textvariable=self._date_to_var, width=12,
        )
        self._date_to_entry.pack(side='left', padx=2)
        self._date_to_entry.bind('<FocusIn>', lambda e: e.widget.selection_range(0, 'end'))
        self._date_to_entry.bind('<FocusOut>', lambda e: self._normalize_date_field('to'))

        # 豆名
        ttk.Label(frame, text='  豆名:').pack(side='left', padx=(10, 2))
        self._bean_combo = ttk.Combobox(
            frame, textvariable=self._bean_var, width=16, state='readonly',
        )
        self._bean_combo.pack(side='left', padx=2)

        # 按钮
        ttk.Button(frame, text='筛选', command=self._do_filter).pack(
            side='left', padx=(10, 2))
        ttk.Button(frame, text='重置', command=self._reset_filter).pack(
            side='left', padx=2)

    def _create_grid(self, parent):
        # 容器
        container = ttk.Frame(parent)
        container.pack(fill='both', expand=True)

        # Treeview
        columns = ('roast_date', 'roast_time', 'bean_name', 'variety',
                   'origin', 'roast_no')
        self._grid_tree = ttk.Treeview(
            container, columns=columns, show='headings',
            selectmode='extended',
        )

        col_config = [
            ('roast_date', '烘焙日期', 90),
            ('roast_time', '烘焙时间', 70),
            ('bean_name', '豆名', 120),
            ('variety', '豆种', 100),
            ('origin', '产地', 100),
            ('roast_no', '编号/炉次', 90),
        ]
        for col, text, width in col_config:
            self._grid_tree.heading(col, text=text)
            self._grid_tree.column(col, width=width, minwidth=60)

        # 滚动条
        scroll = ttk.Scrollbar(container, orient='vertical',
                               command=self._grid_tree.yview)
        self._grid_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self._grid_tree.pack(side='left', fill='both', expand=True)

        # 交互绑定（Phase 1 占位，Phase 4/5 实现）
        self._grid_tree.bind('<Double-1>', self._on_grid_double_click)

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

    def _populate_bean_combo(self):
        names = ['全部']
        for bean_id, info in sorted(self._bean_map.items(),
                                    key=lambda x: x[1].get('name', '')):
            names.append(info.get('name', ''))
        self._bean_combo['values'] = names
        self._bean_combo.current(0)

    def _load_raw_data(self):
        """加载 is_raw_data=True 的会话到右侧列表"""
        # 清空
        for item in self._raw_tree.get_children():
            self._raw_tree.delete(item)

        sessions = self._session_repo.list_all()
        raw = [s for s in sessions if s['is_raw_data']]

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
    # 筛选
    # ================================================================

    @staticmethod
    def _normalize_date(text: str) -> str | None:
        """将多种日期格式转为 YYYY-MM-DD，无法解析返回 None"""
        text = text.strip()
        if not text:
            return ''
        if _DATE_PATTERN.match(text):
            normalized = text
        else:
            digits = text.replace('-', '').replace('/', '').replace('.', '')
            if len(digits) == 8:
                normalized = f'{digits[:4]}-{digits[4:6]}-{digits[6:8]}'
            elif len(digits) == 6:
                yy = int(digits[:2])
                prefix = '19' if yy > 50 else '20'
                normalized = f'{prefix}{digits[:2]}-{digits[2:4]}-{digits[4:6]}'
            else:
                return None
        try:
            datetime.strptime(normalized, '%Y-%m-%d')
            return normalized
        except ValueError:
            return None

    def _normalize_date_field(self, field: str) -> bool:
        """失焦时格式化日期字段，无效则不修改值并聚焦回错误字段

        Returns:
            True 表示值有效（空或合法日期），False 表示输入非法
        """
        var = self._date_from_var if field == 'from' else self._date_to_var
        entry = self._date_from_entry if field == 'from' else self._date_to_entry
        raw = var.get()
        normalized = self._normalize_date(raw)
        if normalized is None:
            messagebox.showerror('日期格式错误',
                                 f'无效的日期: {raw}\n'
                                 '支持的格式：YYYYMMDD、YYMMDD、YYYY-MM-DD')
            var.set('')
            entry.focus_set()
            return False
        # 空或有效 → 更新值
        var.set(normalized)
        if field == 'to':
            df = self._date_from_var.get()
            dt = self._date_to_var.get()
            if df and dt and dt < df:
                self._date_to_var.set(df)
        elif field == 'from' and normalized:
            df = self._date_from_var.get()
            dt = self._date_to_var.get()
            if df and not dt:
                self._date_to_var.set(df)
        return True

    def _do_filter(self):
        """查询并填充主 grid"""
        # 先格式化两个日期字段，任一无效则终止
        if not self._normalize_date_field('from'):
            return
        if not self._normalize_date_field('to'):
            return

        date_from = self._date_from_var.get().strip()
        date_to = self._date_to_var.get().strip()
        bean_name = self._bean_var.get().strip()

        # 豆名 → bean_id
        bean_id = None
        if bean_name and bean_name != '全部':
            for bid, info in self._bean_map.items():
                if info.get('name') == bean_name:
                    bean_id = bid
                    break

        # 清空 grid
        for item in self._grid_tree.get_children():
            self._grid_tree.delete(item)

        sessions = self._session_repo.list_filtered(
            date_from=date_from, date_to=date_to,
            bean_id=bean_id, is_raw_data=False)
        count = 0

        for s in sessions:
            roast_no = s.get('roast_no', '') or ''
            roast_total = s.get('roast_total', '') or ''
            if roast_total:
                no_text = f'#{roast_no}/{roast_total}'
            elif roast_no:
                no_text = f'#{roast_no}'
            else:
                no_text = ''

            item_id = s.get('session_id', '') or str(count)
            self._grid_tree.insert('', 'end', iid=item_id, values=(
                s.get('roast_date', '') or '',
                s.get('roast_time', '') or '',
                s.get('bean_name', ''),
                s.get('bean_variety', ''),
                s.get('bean_origin', ''),
                no_text,
            ))
            count += 1

        self._update_status(f'显示 {count} 条记录')

    def _reset_filter(self):
        self._date_from_var.set('')
        self._date_to_var.set('')
        self._bean_var.set('全部')
        for item in self._grid_tree.get_children():
            self._grid_tree.delete(item)
        self._update_status('就绪')

    # ================================================================
    # 交互
    # ================================================================

    def _on_grid_double_click(self, event):
        """打开 SlogViewer（通过 session_id 从 DB 加载）"""
        selection = self._grid_tree.selection()
        if not selection:
            return
        session_id = selection[0]
        from ui.slog_viewer import open_slog_viewer
        open_slog_viewer(self, session_id=session_id)

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
            self._populate_bean_combo()

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
