"""
SessionGridWidget — 烘焙会话筛选 + 列表组件

从 Dashboard 提取的可复用组件，包含：
- 筛选栏（日期起/止 + 豆名下拉 + 筛选/重置按钮）
- Grid Treeview（6 列：烘焙日期、烘焙时间、豆名、豆种、产地、编号/炉次）

通过 select_mode 和回调支持单选/多选、双击/右键等交互模式。
"""

import re
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Dict, List, Optional

from data.types import BeanRecord

_DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class SessionGridWidget(ttk.Frame):
    """烘焙会话筛选 + 列表组件"""

    def __init__(
        self,
        parent,
        session_repo,
        bean_map: Dict[int, BeanRecord],
        select_mode: str = 'extended',
        on_activate: Optional[Callable[[str], None]] = None,
        on_context_menu: Optional[Callable[[List[str], int, int], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            parent: 父容器
            session_repo: SqliteSessionRepository 实例
            bean_map: bean_id -> BeanRecord 字典
            select_mode: Treeview selectmode ('extended' 多选, 'browse' 单选)
            on_activate: 双击单条时的回调(session_id)
            on_context_menu: 右键多选时回调(selected_ids, x_root, y_root)，仅 extended 模式有效
            on_status: 状态消息回调
        """
        super().__init__(parent)
        self._session_repo = session_repo
        self._bean_map = bean_map
        self._select_mode = select_mode
        self._on_activate = on_activate
        self._on_context_menu = on_context_menu
        self._on_status = on_status

        # 筛选变量
        self._date_from_var = tk.StringVar()
        self._date_to_var = tk.StringVar()
        self._bean_var = tk.StringVar(value='全部')

        # 创建 UI
        self._create_filter_bar()
        self._create_grid()

    # ================================================================
    # UI 创建
    # ================================================================

    def _create_filter_bar(self):
        frame = ttk.Frame(self)
        frame.pack(fill='x', pady=(0, 5))

        ttk.Label(frame, text='日期:').pack(side='left', padx=(0, 2))
        self._date_from_entry = ttk.Entry(
            frame, textvariable=self._date_from_var, width=12,
        )
        self._date_from_entry.pack(side='left', padx=2)
        self._date_from_entry.bind('<FocusIn>', lambda e: e.widget.selection_range(0, 'end'))
        self._date_from_entry.bind('<FocusOut>', lambda e: self._normalize_date_field('from'))

        ttk.Label(frame, text='~').pack(side='left', padx=2)

        self._date_to_entry = ttk.Entry(
            frame, textvariable=self._date_to_var, width=12,
        )
        self._date_to_entry.pack(side='left', padx=2)
        self._date_to_entry.bind('<FocusIn>', lambda e: e.widget.selection_range(0, 'end'))
        self._date_to_entry.bind('<FocusOut>', lambda e: self._normalize_date_field('to'))

        ttk.Label(frame, text='  豆名:').pack(side='left', padx=(10, 2))
        self._bean_combo = ttk.Combobox(
            frame, textvariable=self._bean_var, width=16, state='readonly',
        )
        self._bean_combo.pack(side='left', padx=2)

        self._populate_bean_combo()

        ttk.Button(frame, text='筛选', command=self.refresh).pack(
            side='left', padx=(10, 2))
        ttk.Button(frame, text='重置', command=self.reset_filter).pack(
            side='left', padx=2)

    def _create_grid(self):
        container = ttk.Frame(self)
        container.pack(fill='both', expand=True)

        columns = ('is_favorite', 'roast_date', 'roast_time', 'bean_name', 'variety',
                   'origin', 'roast_no')
        self._grid_tree = ttk.Treeview(
            container, columns=columns, show='headings',
            selectmode=self._select_mode,
        )

        col_config = [
            ('is_favorite', '星标', 60),
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

        self._grid_tree.column('is_favorite', anchor='center', stretch=False)

        scroll = ttk.Scrollbar(container, orient='vertical',
                               command=self._grid_tree.yview)
        self._grid_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self._grid_tree.pack(side='left', fill='both', expand=True)

        # 双击
        self._grid_tree.bind('<Double-1>', self._on_double_click)
        # 右键（仅在 extended 模式下生效）
        if self._select_mode == 'extended':
            self._grid_tree.bind('<Button-3>', self._on_right_click)

    # ================================================================
    # 筛选逻辑
    # ================================================================

    def _populate_bean_combo(self):
        names = ['全部']
        for bean_id, info in sorted(self._bean_map.items(),
                                    key=lambda x: x[1].get('name', '')):
            names.append(info.get('name', ''))
        self._bean_combo['values'] = names
        self._bean_combo.current(0)

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
        """失焦时格式化日期字段，无效则不修改值并聚焦回错误字段"""
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

    # ================================================================
    # 公共方法
    # ================================================================

    def get_selected_ids(self) -> List[str]:
        """返回当前选中的 session_id 列表"""
        return list(self._grid_tree.selection())

    def refresh_bean_map(self, bean_map: Dict[int, BeanRecord]):
        """更新豆信息映射并刷新下拉列表"""
        self._bean_map = bean_map
        self._populate_bean_combo()

    def refresh(self, date_from: str = '', date_to: str = '',
                bean_name: str = ''):
        """筛选并刷新 grid 列表

        无参调用时使用当前筛选控件中的值。
        """
        # 使用传入参数或控件值
        if not date_from:
            date_from = self._date_from_var.get().strip()
            if date_from and not self._normalize_date_field('from'):
                return
        if not date_to:
            date_to = self._date_to_var.get().strip()
            if date_to and not self._normalize_date_field('to'):
                return

        # 如果参数也有内容，也要校验
        if date_from and not self._normalize_date(date_from):
            messagebox.showerror('日期格式错误', f'无效的日期: {date_from}')
            return
        if date_to and not self._normalize_date(date_to):
            messagebox.showerror('日期格式错误', f'无效的日期: {date_to}')
            return

        if not bean_name:
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
                '★' if s.get('is_favorite') else '☆',
                s.get('roast_date', '') or '',
                s.get('roast_time', '') or '',
                s.get('bean_name', ''),
                s.get('bean_variety', ''),
                s.get('bean_origin', ''),
                no_text,
            ))
            count += 1

        if self._on_status:
            self._on_status(f'显示 {count} 条记录')

    def reset_filter(self):
        """重置筛选条件并清空 grid"""
        self._date_from_var.set('')
        self._date_to_var.set('')
        self._bean_var.set('全部')
        for item in self._grid_tree.get_children():
            self._grid_tree.delete(item)
        if self._on_status:
            self._on_status('就绪')

    # ================================================================
    # 事件处理
    # ================================================================

    def _on_double_click(self, event):
        selection = self._grid_tree.selection()
        if not selection:
            return
        if self._on_activate:
            self._on_activate(selection[0])

    def _on_right_click(self, event):
        """右键弹出上下文菜单（支持单选）"""
        iid = self._grid_tree.identify_row(event.y)
        if not iid:
            return
        selected = set(self._grid_tree.selection())
        if iid in selected:
            pass  # 保留当前多选
        else:
            self._grid_tree.selection_set(iid)  # 仅选中点击行
            selected = {iid}
        if self._on_context_menu:
            self._on_context_menu(list(selected), event.x_root, event.y_root)
