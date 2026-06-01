"""
生豆信息管理窗口

功能：
1. 加载/保存 beans.json（%APPDATA%/SantokrOCR/BeanInfo/beans.json）
2. 左侧 Treeview：名称（带 N/M/D 前缀）、已用尽
3. 右侧详情编辑面板
4. 修改追踪：黄色背景、未保存提示
"""

import copy
import tkinter as tk
from tkinter import ttk, messagebox

from data.json.bean_repo import JsonBeanRepository

BEAN_FIELDS = [
    ('name', '名称:'),
    ('variety', '豆种:'),
    ('process', '处理法:'),
    ('origin', '产地:'),
    ('altitude', '海拔(m):'),
    ('density', '密度(g/L):'),
    ('moisture', '含水率(%):'),
    ('season', '产季:'),
]


class BeanManager(tk.Toplevel):
    """生豆信息管理窗口"""

    def __init__(self, parent, on_save_callback=None):
        super().__init__(parent)
        self.on_save_callback = on_save_callback

        self.title("生豆信息管理")
        self.minsize(600, 400)
        self._center_window()

        # ---------- 数据 ----------
        self._beans = []          # 当前工作数据
        self._original = []       # 加载时的原始数据（用于 diff）
        self._new_indices = set()     # 新增记录索引
        self._deleted_indices = set()  # 标记删除的索引
        self._selected_index = None    # 当前选中的索引
        self._loading_detail = False   # 正在加载详情（抑制 trace）

        # StringVars 和 Entry 引用
        self._field_vars = {}    # field_name -> StringVar
        self._field_entries = {} # field_name -> tk.Entry
        self._outofstock_var = tk.BooleanVar(value=False)
        self._outofstock_cb = None

        # 数据层
        self._bean_repo = JsonBeanRepository()
        self._load()

        # 创建 UI
        self._create_ui()

        # 模态
        self.transient(parent)
        self.grab_set()
        self.focus_set()

        # 关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ============== 数据层 ==============

    def _center_window(self):
        """直接计算居中位置"""
        w, h = 800, 600
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _load(self):
        self._beans = []
        try:
            self._beans = self._bean_repo.list_all()
        except Exception as e:
            messagebox.showerror("错误", f"加载生豆信息失败:\n{e}")
        self._original = copy.deepcopy(self._beans)
        self._new_indices.clear()
        self._deleted_indices.clear()

    def _save(self):
        to_save = [
            b for i, b in enumerate(self._beans)
            if i not in self._deleted_indices and b.get('name', '').strip()
        ]
        self._bean_repo.save_all(to_save)

        # 重置追踪
        self._original = copy.deepcopy(to_save)
        self._new_indices.clear()
        self._deleted_indices.clear()

        # 重建索引映射：原 index → 新 index（仅对未删除且非空的记录）
        # 由于 _beans 中删除了已保存的记录，需要重建
        # 最简单的做法：重新加载
        self._beans = copy.deepcopy(to_save)
        self._selected_index = None
        self._refresh_tree()
        self._clear_detail()
        self._clear_yellow_backgrounds()

        if self.on_save_callback:
            self.on_save_callback()

    def _has_unsaved_changes(self):
        """检查是否有未保存的更改"""
        if self._new_indices or self._deleted_indices:
            return True
        for i, bean in enumerate(self._beans):
            if i >= len(self._original):
                return True
            if bean != self._original[i]:
                return True
        return False

    def _is_modified(self, idx):
        """检查指定索引的记录是否被修改（不是新增/删除，但有字段变化）"""
        if idx in self._deleted_indices or idx in self._new_indices:
            return False
        if idx >= len(self._original):
            return False  # shouldn't happen
        return self._beans[idx] != self._original[idx]

    def _get_bean_status(self, idx):
        """返回 (前缀字符, 颜色标签名, 颜色) 或 (None, None, None) 表示无标记"""
        if idx in self._deleted_indices:
            return 'D', 'red_fg', 'red'
        if idx in self._new_indices:
            return 'N', 'green_fg', 'green'
        if self._is_modified(idx):
            return 'M', 'orange_fg', '#CC8800'
        return None, None, None

    # ============== UI 创建 ==============

    def _create_ui(self):
        # 主容器
        main_container = ttk.Frame(self)
        main_container.pack(fill="both", expand=True, padx=5, pady=5)

        # 左右分栏（PanedWindow）
        paned = ttk.PanedWindow(main_container, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # ---- 左侧：Treeview ----
        left_frame = ttk.Frame(paned, width=260)
        paned.add(left_frame, weight=0)

        # Treeview 容器（占据上方空间）
        tree_container = ttk.Frame(left_frame)
        tree_container.pack(fill="both", expand=True)

        columns = ('name', 'outofstock')
        self.tree = ttk.Treeview(tree_container, columns=columns, show='headings',
                                 selectmode='browse')
        self.tree.heading('name', text='名称')
        self.tree.heading('outofstock', text='已用尽')
        self.tree.column('name', width=180)
        self.tree.column('outofstock', width=60, anchor='center')

        # 配置 tag 颜色
        self.tree.tag_configure('green_fg', foreground='green')
        self.tree.tag_configure('orange_fg', foreground='#CC8800')
        self.tree.tag_configure('red_fg', foreground='red')

        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        # [+], [-] 按钮（treeview下方）
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="+", width=3, command=self._on_add).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="-", width=3, command=self._on_delete).pack(side="left", padx=2)

        # ---- 右侧：详情编辑 ----
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)

        # 详情 LabelFrame
        detail_frame = ttk.LabelFrame(right_frame, text="生豆详情")
        detail_frame.pack(fill="both", expand=True, padx=(5, 0))

        # 字段创建
        for field_name, label_text in BEAN_FIELDS:
            row = ttk.Frame(detail_frame)
            row.pack(fill="x", padx=8, pady=3)
            ttk.Label(row, text=label_text, width=12, anchor="w").pack(side="left")
            var = tk.StringVar()
            entry = tk.Entry(row, textvariable=var)
            entry.pack(side="left", fill="x", expand=True)
            var.trace_add('write', lambda *_, fn=field_name: self._on_field_changed(fn))
            self._field_vars[field_name] = var
            self._field_entries[field_name] = entry

        # 已用尽 Checkbutton
        row = ttk.Frame(detail_frame)
        row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text="已用尽:", width=12, anchor="w").pack(side="left")
        self._outofstock_cb = tk.Checkbutton(
            row, variable=self._outofstock_var,
            command=lambda: self._on_field_changed('outOfStock')
        )
        self._outofstock_cb.pack(side="left")

        # 底部：保存按钮
        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(fill="x", padx=10, pady=(0, 10))
        save_btn = ttk.Button(bottom_frame, text="保存", command=self._save)
        save_btn.pack(side="right")

        # 绑定事件
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)

        # 初始填充
        self._refresh_tree()

    def _refresh_tree(self):
        """刷新左侧 treeview"""
        # 保存当前选中的 index
        prev_selected = None
        sel = self.tree.selection()
        if sel:
            prev_selected = int(sel[0])

        # 清空
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 填充
        for i, bean in enumerate(self._beans):
            prefix_char, tag_name, _ = self._get_bean_status(i)
            display_name = bean.get('name', '')
            if prefix_char:
                display_name = f"{prefix_char} {display_name}"

            oos = bean.get('outOfStock', False)
            oos_text = '☑' if oos else '☐'

            tags = (tag_name,) if tag_name else ()
            self.tree.insert('', 'end', iid=str(i), values=(display_name, oos_text), tags=tags)

        # 恢复选中
        if prev_selected is not None and str(prev_selected) in self.tree.get_children():
            self.tree.selection_set(str(prev_selected))
        elif self.tree.get_children():
            # 选中第一个
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self._on_tree_select()  # manually trigger

    # ============== 交互处理 ==============

    def _save_current_to_beans(self):
        """将右侧字段数据保存到 _beans 中当前选中的记录"""
        idx = self._selected_index
        if idx is None:
            return
        bean = self._beans[idx]
        for field_name in ('name', 'variety', 'process', 'origin', 'altitude', 'density', 'moisture', 'season'):
            bean[field_name] = self._field_vars[field_name].get()
        bean['outOfStock'] = self._outofstock_var.get()

    def _load_detail(self, idx):
        """加载指定索引的生豆数据到右侧字段"""
        self._loading_detail = True
        bean = self._beans[idx]
        for field_name in ('name', 'variety', 'process', 'origin', 'altitude', 'density', 'moisture', 'season'):
            self._field_vars[field_name].set(bean.get(field_name, ''))
        self._outofstock_var.set(bean.get('outOfStock', False))
        self._loading_detail = False

        # 更新黄色背景
        self._update_field_backgrounds(idx)

    def _clear_detail(self):
        """清空右侧字段"""
        self._loading_detail = True
        for field_name in self._field_vars:
            self._field_vars[field_name].set('')
        self._outofstock_var.set(False)
        self._loading_detail = False
        self._selected_index = None

    def _on_tree_select(self, event=None):
        """Treeview 选中事件"""
        sel = self.tree.selection()
        if not sel:
            self._clear_detail()
            return

        idx = int(sel[0])

        # 保存当前编辑到之前的选中
        if self._selected_index is not None and self._selected_index != idx:
            self._save_current_to_beans()

        self._selected_index = idx
        self._load_detail(idx)

    def _on_field_changed(self, field_name):
        """右侧字段值变更"""
        if self._loading_detail:
            return

        idx = self._selected_index
        if idx is None:
            return

        # 更新 _beans
        bean = self._beans[idx]
        if field_name == 'outOfStock':
            bean['outOfStock'] = self._outofstock_var.get()
        else:
            bean[field_name] = self._field_vars[field_name].get()

        # 更新黄色背景
        self._update_field_backgrounds(idx)

        # 更新 tree 显示
        self._refresh_tree()

    def _update_field_backgrounds(self, idx):
        """更新右侧字段的背景色（黄色=已修改）"""
        if idx is None or idx >= len(self._original) or idx in (self._deleted_indices | self._new_indices):
            # 新增/删除记录全部白色
            self._clear_yellow_backgrounds()
            return

        orig = self._original[idx] if idx < len(self._original) else {}
        bean = self._beans[idx]

        for field_name in ('name', 'variety', 'process', 'origin', 'altitude', 'density', 'moisture', 'season'):
            entry = self._field_entries[field_name]
            curr_val = bean.get(field_name, '')
            orig_val = orig.get(field_name, '') if orig else ''
            if curr_val != orig_val:
                entry.configure(bg='#FFFF99')
            else:
                entry.configure(bg='white')

        # Checkbutton
        curr_oos = bean.get('outOfStock', False)
        orig_oos = orig.get('outOfStock', False) if orig else False
        cb_color = '#FFFF99' if curr_oos != orig_oos else 'white'
        self._outofstock_cb.configure(selectcolor=cb_color)

    def _clear_yellow_backgrounds(self):
        """清除所有黄色背景"""
        for entry in self._field_entries.values():
            entry.configure(bg='white')
        self._outofstock_cb.configure(selectcolor='white')

    def _on_add(self):
        """新增生豆记录"""
        # 保存当前编辑
        if self._selected_index is not None:
            self._save_current_to_beans()

        new_idx = len(self._beans)
        self._beans.append({
            'name': '',
            'variety': '',
            'process': '',
            'origin': '',
            'altitude': '',
            'density': '',
            'moisture': '',
            'season': '',
            'outOfStock': False,
        })
        self._new_indices.add(new_idx)
        self._original.append({})  # 占位，不会用于 diff
        self._refresh_tree()

        # 选中新行
        self.tree.selection_set(str(new_idx))
        self.tree.see(str(new_idx))
        self._on_tree_select()

    def _on_delete(self):
        """标记删除选中的记录"""
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])

        if idx in self._new_indices:
            # 新增记录 → 直接移除
            self._beans.pop(idx)
            self._new_indices.discard(idx)
            # 调整索引
            self._new_indices = {i if i < idx else i - 1 for i in self._new_indices}
            # 如果 selected 指向 idx 之后的记录，刷新
            self._selected_index = None
            self._refresh_tree()
            # 选中附近的行
            if self.tree.get_children():
                target = str(min(idx, len(self._beans) - 1))
                self.tree.selection_set(target)
                self.tree.see(target)
                self._on_tree_select()
        else:
            # 已有记录 → 标记删除
            self._deleted_indices.add(idx)
            self._refresh_tree()
            # 右侧清空
            self._clear_detail()
            self._selected_index = None
            # 选中另一条
            remaining = [c for c in self.tree.get_children() if int(c) != idx]
            if remaining:
                self.tree.selection_set(remaining[0])
                self._on_tree_select()

    def _on_close(self):
        """关闭窗口"""
        if self._has_unsaved_changes():
            result = messagebox.askyesnocancel(
                "未保存的更改",
                "有未保存的更改，是否保存？"
            )
            if result is True:
                self._save()
                self.destroy()
            elif result is None:
                return  # 取消关闭
            else:
                self.destroy()
        else:
            self.destroy()
