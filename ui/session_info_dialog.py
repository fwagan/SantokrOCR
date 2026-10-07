"""
SessionInfoDialog — 保存会话到数据库时填写 session info 的弹窗

纯表单：负责收集 + 校验会话元信息，再经注入的 on_save 回调交给调用方写库。
本模块不含任何数据库写入（bean_repo 仅用于读取生豆列表）。

用法::

    dlg = SessionInfoDialog(parent, bean_repo, on_save=do_save,
                            default_bean_name='耶加雪菲')
    if dlg.show():
        ...   # do_save 已成功执行

on_save 抛异常即视为写入失败：弹窗保持打开、已填内容保留，用户可修正后重试。
"""

import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from data.types import BeanRecord
from utils.numeric import try_float

# 左侧只读字段（取自所选生豆）
_BEAN_READONLY_FIELDS = (
    ('variety', '豆种:'),
    ('process', '处理法:'),
    ('origin', '产地:'),
    ('altitude', '海拔(m):'),
    ('season', '产季:'),
)

# 左侧可编辑字段（与生豆默认值不同才存 override）
_BEAN_EDITABLE_FIELDS = (
    ('density', '密度(g/L):'),
    ('moisture', '含水率(%):'),
)

# 需要校验数值格式的字段
_NUMERIC_FIELDS = (
    ('green_weight', '生豆重量'),
    ('roasted_weight', '熟豆重量'),
    ('density', '密度'),
    ('moisture', '含水率'),
)

_LABEL_WIDTH = 12
_WINDOW_SIZE = (760, 440)
_MIN_SIZE = (700, 400)
_NOTES_DEFAULT_FORMAT = '%Y%m%d_%H%M%S'


def _to_text(value: Any) -> str:
    """生豆字段值 → 输入框文本（None → 空串）"""
    return '' if value is None else str(value)


class SessionInfoDialog(tk.Toplevel):
    """填写 session info 的弹窗（阻塞式模态）"""

    def __init__(
        self,
        parent,
        bean_repo,
        on_save: Callable[[Dict[str, Any]], None],
        default_bean_name: str = '',
    ):
        """
        Args:
            parent: 父窗口
            bean_repo: 生豆仓库，只需提供 list_all()
            on_save: 写库动作，收到已归一化的字段 dict；抛异常表示失败
            default_bean_name: 预选生豆名（来自理想曲线），空串则不预选
        """
        super().__init__(parent)

        self._parent = parent
        self._bean_repo = bean_repo
        self._on_save = on_save
        self._default_bean_name = default_bean_name

        self._beans: List[BeanRecord] = []
        self._vars: Dict[str, tk.StringVar] = {}
        self._entries: Dict[str, ttk.Entry] = {}
        self._name_var = tk.StringVar()
        self._is_raw_var = tk.BooleanVar(value=False)
        self._notes_text: Optional[tk.Text] = None

        # on_save 正常返回后置 True，调用方据此区分「成功」与「取消/失败」
        self.saved = False

        self.title('保存会话到数据库')
        self.minsize(*_MIN_SIZE)
        self._create_layout()
        self._load_beans()
        self._apply_default_bean()

        self.protocol('WM_DELETE_WINDOW', self._on_cancel)
        self.bind('<Escape>', lambda e: self._on_cancel())
        self.bind('<Return>', self._on_return)

    def show(self) -> bool:
        """以模态方式显示并等待关闭

        Returns:
            True 表示 on_save 已成功执行，False 表示用户取消或写入失败。
        """
        self.transient(self._parent)
        self.grab_set()
        self._center_on(self._parent)
        self.bean_combo.focus_set()
        self.wait_window()
        return self.saved

    # ================================================================
    # 布局
    # ================================================================

    def _create_layout(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill='both', expand=True)

        cols = ttk.Frame(outer)
        cols.pack(fill='both', expand=True)

        left = ttk.LabelFrame(cols, text='生豆信息', padding=8)
        left.pack(side='left', fill='both', expand=True, padx=(0, 6))
        right = ttk.LabelFrame(cols, text='烘焙信息', padding=8)
        right.pack(side='left', fill='both', expand=True)

        self._create_bean_panel(left)
        self._create_roast_panel(right)

        btns = ttk.Frame(outer)
        btns.pack(fill='x', pady=(10, 0))
        ttk.Button(btns, text='确定', command=self._on_ok).pack(side='right')
        ttk.Button(btns, text='取消', command=self._on_cancel).pack(
            side='right', padx=(0, 6))

    def _create_bean_panel(self, parent):
        parent.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(parent, text='生豆名称:', width=_LABEL_WIDTH, anchor='w').grid(
            row=row, column=0, sticky='w', pady=2)
        self.bean_combo = ttk.Combobox(parent, textvariable=self._name_var,
                                       state='readonly')
        self.bean_combo.grid(row=row, column=1, sticky='ew', pady=2)
        self.bean_combo.bind('<<ComboboxSelected>>', self._on_bean_selected)
        row += 1

        for key, label in _BEAN_READONLY_FIELDS:
            self._add_row(parent, row, key, label, readonly=True)
            row += 1

        for key, label in _BEAN_EDITABLE_FIELDS:
            self._add_row(parent, row, key, label)
            row += 1

    def _create_roast_panel(self, parent):
        parent.columnconfigure(1, weight=1)
        row = 0
        now = datetime.now()

        self._add_row(parent, row, 'roast_date', '烘焙日期:')
        self._vars['roast_date'].set(now.strftime('%Y-%m-%d'))
        row += 1

        self._add_row(parent, row, 'roast_time', '烘焙时间:')
        self._vars['roast_time'].set(now.strftime('%H:%M'))
        row += 1

        ttk.Label(parent, text='烘焙次序:', width=_LABEL_WIDTH, anchor='w').grid(
            row=row, column=0, sticky='w', pady=2)
        order = ttk.Frame(parent)
        order.grid(row=row, column=1, sticky='ew', pady=2)
        ttk.Label(order, text='第').pack(side='left')
        self._add_compact(order, 'roast_no')
        ttk.Label(order, text='共').pack(side='left', padx=(6, 0))
        self._add_compact(order, 'roast_total')
        row += 1

        self._add_row(parent, row, 'green_weight', '生豆重量:')
        row += 1
        self._add_row(parent, row, 'roasted_weight', '熟豆重量:')
        row += 1
        self._add_row(parent, row, 'weight_loss', '失重率:', readonly=True)
        row += 1

        ttk.Label(parent, text='备注:', width=_LABEL_WIDTH, anchor='w').grid(
            row=row, column=0, sticky='nw', pady=2)
        self._notes_text = tk.Text(parent, height=3, width=20)
        self._notes_text.grid(row=row, column=1, sticky='ew', pady=2)
        row += 1

        ttk.Checkbutton(parent, text='保存为原始数据', variable=self._is_raw_var,
                        command=self._on_raw_toggled).grid(
            row=row, column=0, columnspan=2, sticky='w', pady=(8, 0))

        for key in ('green_weight', 'roasted_weight'):
            self._vars[key].trace_add('write', self._update_weight_loss)

    def _add_row(self, parent, row, key, label, readonly=False):
        """第 row 行：「标签 + 撑满整行的输入框」，并登记 StringVar"""
        ttk.Label(parent, text=label, width=_LABEL_WIDTH, anchor='w').grid(
            row=row, column=0, sticky='w', pady=2)
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var)
        if readonly:
            entry.configure(state='readonly')
        entry.grid(row=row, column=1, sticky='ew', pady=2)
        self._vars[key] = var
        self._entries[key] = entry

    def _add_compact(self, parent, key):
        """并排短输入框（用于「第 X 共 Y」），并登记 StringVar"""
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=5)
        entry.pack(side='left', padx=2)
        self._vars[key] = var
        self._entries[key] = entry

    def _center_on(self, parent):
        w, h = _WINDOW_SIZE
        try:
            x = parent.winfo_x() + (parent.winfo_width() - w) // 2
            y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        except Exception:
            x = y = 0
        self.geometry(f'{w}x{h}+{max(x, 0)}+{max(y, 0)}')

    # ================================================================
    # 生豆
    # ================================================================

    def _load_beans(self):
        try:
            all_beans = self._bean_repo.list_all()
        except Exception:
            all_beans = []
        self._beans = [b for b in all_beans if not b.get('outOfStock', False)]
        self.bean_combo['values'] = [
            b.get('name', '') for b in self._beans if b.get('name')
        ]

    def _apply_default_bean(self):
        """按理想曲线的生豆名预选（不存在或缺货则保持未选中）"""
        name = self._default_bean_name
        if name and name in self.bean_combo['values']:
            self._name_var.set(name)
            self._on_bean_selected()

    def _selected_bean(self) -> Optional[BeanRecord]:
        name = self._name_var.get()
        return next((b for b in self._beans if b.get('name') == name), None)

    def _on_bean_selected(self, event=None):
        bean = self._selected_bean()
        if bean:
            self._apply_bean_info(bean)

    @staticmethod
    def _bean_default_text(bean: Optional[BeanRecord], key: str) -> str:
        if not bean:
            return ''
        return _to_text(bean.get(key))

    def _apply_bean_info(self, bean: BeanRecord):
        """填充派生字段；密度/含水率仅在当前为空时带入默认值"""
        for key, _ in _BEAN_READONLY_FIELDS:
            self._vars[key].set(_to_text(bean.get(key)))
        for key, _ in _BEAN_EDITABLE_FIELDS:
            if not self._vars[key].get().strip():
                self._vars[key].set(_to_text(bean.get(key)))

    # ================================================================
    # 联动
    # ================================================================

    def _update_weight_loss(self, *_):
        """失重率 = (生豆 − 熟豆) / 生豆 × 100"""
        green = try_float(self._vars['green_weight'].get().strip())
        roasted = try_float(self._vars['roasted_weight'].get().strip())
        if green and roasted and green > 0 and roasted > 0:
            self._vars['weight_loss'].set(f'{(green - roasted) / green * 100:.1f}%')
        else:
            self._vars['weight_loss'].set('')

    def _on_raw_toggled(self):
        """勾选「保存为原始数据」时，备注为空则填默认名"""
        if self._is_raw_var.get() and not self._notes().strip():
            self._fill_default_notes()

    def _fill_default_notes(self):
        self._notes_text.delete('1.0', 'end')
        self._notes_text.insert('1.0', datetime.now().strftime(_NOTES_DEFAULT_FORMAT))

    def _on_return(self, event=None):
        """Return = 确定；焦点在备注框时让 tk.Text 自己换行"""
        if self.focus_get() is self._notes_text:
            return
        self._on_ok()

    def _notes(self) -> str:
        if self._notes_text is None:
            return ''
        return self._notes_text.get('1.0', 'end-1c')

    # ================================================================
    # 收集 / 校验 / 提交
    # ================================================================

    def _collect(self) -> Dict[str, Any]:
        """收集为 RoastSession 兼容的字段 dict"""
        bean = self._selected_bean()
        data: Dict[str, Any] = {
            'bean_id': bean.get('id') if bean else None,
            'is_raw_data': bool(self._is_raw_var.get()),
            'roast_date': self._vars['roast_date'].get().strip(),
            'roast_time': self._vars['roast_time'].get().strip(),
            'roast_no': self._vars['roast_no'].get().strip(),
            'roast_total': self._vars['roast_total'].get().strip(),
            'green_weight': try_float(self._vars['green_weight'].get().strip()),
            'roasted_weight': try_float(self._vars['roasted_weight'].get().strip()),
            'notes': self._notes().strip(),
        }
        # density/moisture 只在偏离生豆默认值时才存 override
        for key, _ in _BEAN_EDITABLE_FIELDS:
            text = self._vars[key].get().strip()
            default = self._bean_default_text(bean, key)
            data[f'{key}_override'] = (
                try_float(text) if text and text != default else None)
        return data

    def _validate(self, data: Dict[str, Any]) -> List[str]:
        """按「是否原始数据」动态校验，返回错误字段列表（空 = 通过）"""
        errors: List[str] = []
        is_raw = self._is_raw_var.get()

        if not is_raw:
            if not self._name_var.get().strip():
                errors.append('生豆名称')

            date_text = data['roast_date']
            if not date_text:
                errors.append('烘焙日期')
            elif not self._is_valid_format(date_text, '%Y-%m-%d'):
                errors.append('烘焙日期（格式须为 YYYY-MM-DD）')

            time_text = data['roast_time']
            if not time_text:
                errors.append('烘焙时间')
            elif not self._is_valid_format(time_text, '%H:%M'):
                errors.append('烘焙时间（格式须为 HH:MM）')

            if not data['roast_no']:
                errors.append('烘焙次序（第）')
            if not data['roast_total']:
                errors.append('烘焙次序（共）')

        # 数值格式：非空时必须可解析（两种模式都校验）
        for key, label in _NUMERIC_FIELDS:
            raw = self._vars[key].get().strip()
            if raw and try_float(raw) is None:
                errors.append(f'{label}（须为数值）')

        if not is_raw and data['green_weight'] is None:
            errors.append('生豆重量')

        return errors

    @staticmethod
    def _is_valid_format(text: str, fmt: str) -> bool:
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            return False

    def _on_ok(self):
        # 原始数据模式的备注兜底：保证总有可显示的名字
        if self._is_raw_var.get() and not self._notes().strip():
            self._fill_default_notes()

        data = self._collect()
        errors = self._validate(data)
        if errors:
            messagebox.showerror(
                '保存失败', '请检查以下字段:\n' + '\n'.join(errors), parent=self)
            return

        try:
            self._on_save(data)
        except Exception as e:
            messagebox.showerror('保存失败', f'数据库写入错误:\n{e}', parent=self)
            return

        self.saved = True
        self.destroy()

    def _on_cancel(self):
        self.saved = False
        self.destroy()
