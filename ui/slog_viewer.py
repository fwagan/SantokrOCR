"""
.slog 文件查看器

功能：
1. 打开并查看.slog文件（包含results和events的JSON格式）
2. 显示温度曲线、ROR分析、火力/风门曲线、事件标记、阶段条
3. 支持参数调整（重采样间隔、平滑窗口等）
4. 烘焙信息管理（豆种、产地等）
"""

# ====== Windows DPI感知（解决tkinter模糊） ======
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# 确保能导入项目模块
def _setup_path():
    """将项目根目录添加到 sys.path"""
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(this_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


_setup_path()
from data.serializers.slog import SlogSerializer
from data.sqlite.bean_repo import SqliteBeanRepository
from data.sqlite.event_repo import SqliteEventRepository
from data.sqlite.result_repo import SqliteResultRepository
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.session_writer import SessionWriter
from ui.statistics_panel import StatisticsPanel


class SlogViewer(tk.Toplevel):
    """.slog文件查看器（Toplevel版本，可嵌入或独立运行）"""

    # 烘焙信息字段定义：(键, 标签文本, 是否只读)
    ROAST_FIELDS = [
        ('variety', '豆种:', True),       # 来自生豆信息，只读
        ('process', '处理法:', True),     # 来自生豆信息，只读
        ('origin', '产地:', True),        # 来自生豆信息，只读
        ('altitude', '海拔(m):', True),   # 来自生豆信息，只读
        ('season', '产季:', True),        # 来自生豆信息，只读
        ('density', '密度(g/L):', False),
        ('moisture', '含水率(%):', False),
        ('green_weight', '生豆重量:', False),
        ('roasted_weight', '熟豆重量:', False),
        ('weight_loss', '失重率:', True),  # 自动计算，只读
    ]

    def __init__(self, master=None, file_path=None, session_id=None):
        super().__init__(master)

        self.title("Slog Viewer")
        self.minsize(900, 600)
        self._center_window()

        # 当前加载的文件路径
        self.current_path = None
        # 来源标识（用于默认导出文件名）
        self.source_identity = ""
        # DB session_id（来自 Dashboard 或 RecognitionWindow）
        self._rw_session_id = session_id

        # 数据库（保存用）
        self._session_repo = SqliteSessionRepository()
        self._result_repo = SqliteResultRepository()
        self._event_repo = SqliteEventRepository()

        # 单例子窗口引用
        self._comparer = None
        self._bean_manager = None
        self.roast_vars = {}
        self._create_roast_vars()
        self.roast_favorite_var = tk.BooleanVar(value=False)

        # 创建菜单栏
        self.create_menu()

        # 创建布局
        self._create_layout()

        # 绑定快捷键 (bind_all 确保在所有控件焦点下均有效)
        self.bind_all('<Control-o>', self._on_shortcut_open)
        self.bind_all('<Control-s>', self._on_shortcut_export)
        self.bind_all('<Control-q>', self._on_shortcut_quit)

        # 加载生豆信息
        self._load_bean_info()

        # 加载数据
        if session_id:
            self.load_from_session_id(session_id)
        elif file_path:
            self.load_file(file_path)

    def _center_window(self):
        """直接计算居中位置"""
        w, h = 1800, 1200
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ============== 全局快捷键 ==============

    def _active_for_self(self, event):
        """检查事件是否来自本窗口"""
        try:
            return event.widget.winfo_toplevel() is self
        except tk.TclError:
            return False

    def _on_shortcut_open(self, event):
        if self._active_for_self(event):
            self.open_slog()

    def _on_shortcut_export(self, event):
        if self._active_for_self(event):
            self.export_slog()

    def _on_shortcut_quit(self, event):
        if self._active_for_self(event):
            self.destroy()

    def _create_roast_vars(self):
        """创建烘焙信息的所有 StringVar"""
        for key, _, readonly in self.ROAST_FIELDS:
            self.roast_vars[key] = tk.StringVar(value='')
            if key in ('green_weight', 'roasted_weight') and not readonly:
                self.roast_vars[key].trace_add('write', lambda *_: self._update_weight_loss())
        # 烘焙次序特殊变量
        self.roast_vars['roast_no'] = tk.StringVar(value='')
        self.roast_vars['roast_total'] = tk.StringVar(value='')
        # 日期/时间分解变量
        self.roast_vars['roast_date_year'] = tk.StringVar(value='')
        self.roast_vars['roast_date_month'] = tk.StringVar(value='')
        self.roast_vars['roast_date_day'] = tk.StringVar(value='')
        self.roast_vars['roast_time_hour'] = tk.StringVar(value='')
        self.roast_vars['roast_time_minute'] = tk.StringVar(value='')
        # 生豆名称（Combobox 单独管理）
        self.bean_name_var = tk.StringVar(value='')

    def _update_weight_loss(self, *args):
        """计算失重率"""
        try:
            green = float(self.roast_vars['green_weight'].get() or 0)
            roasted = float(self.roast_vars['roasted_weight'].get() or 0)
            if green > 0 and roasted > 0:
                loss = (green - roasted) / green * 100
                self.roast_vars['weight_loss'].set(f"{loss:.1f}%")
            else:
                self.roast_vars['weight_loss'].set('')
        except ValueError:
            self.roast_vars['weight_loss'].set('')

    def create_menu(self):
        """创建菜单栏"""
        menubar = tk.Menu(self)

        op_menu = tk.Menu(menubar, tearoff=0)
        op_menu.add_command(
            label="打开", command=self.open_slog, accelerator="Ctrl+O"
        )
        op_menu.add_command(
            label="另存为", command=self.export_slog, accelerator="Ctrl+S"
        )
        op_menu.add_command(
            label="对比曲线", command=self.open_comparer
        )
        op_menu.add_separator()
        op_menu.add_command(
            label="退出", command=self.destroy, accelerator="Ctrl+Q"
        )
        menubar.add_cascade(label="操作", menu=op_menu)

        self.config(menu=menubar)

    def _create_layout(self):
        """创建左右分栏布局"""
        # 主容器
        main_container = ttk.Frame(self)
        main_container.pack(fill="both", expand=True, padx=5, pady=5)

        # 左侧面板（数据+控制）
        self.left_panel = ttk.Frame(main_container, width=500)
        self.left_panel.pack(side="left", fill="y")
        self.left_panel.pack_propagate(False)

        # 右侧面板（图表）
        right_panel = ttk.Frame(main_container)
        right_panel.pack(side="left", fill="both", expand=True)

        # 左上：烘焙信息（占据上方剩余空间）
        roast_container = ttk.Frame(self.left_panel)
        roast_container.pack(fill="both", expand=True)
        self._create_roast_info(roast_container)

        # 保存按钮
        btn_frame = ttk.Frame(self.left_panel)
        btn_frame.pack(fill="x", padx=5, pady=3)
        ttk.Button(btn_frame, text="保存到数据库",
                   command=self.save_to_database).pack(fill="x")

        # 左下：控制参数
        self.stats_panel = StatisticsPanel(right_panel, is_realtime=False)
        self.stats_panel.pack(fill="both", expand=True)
        control_container = ttk.Frame(self.left_panel)
        control_container.pack(fill="x")
        self.stats_panel.create_controls(control_container)


    def _create_roast_info(self, parent):
        """创建烘焙信息面板"""
        # 使用 Canvas + 内部 Frame 实现滚动
        canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=canvas.winfo_reqwidth())
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # 绑定鼠标滚轮
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # 更新内部 frame 宽度以匹配 canvas
        def _update_inner_width(event):
            canvas.itemconfig(1, width=event.width)
        canvas.bind("<Configure>", _update_inner_width)

        # ---- 字段创建辅助 ----
        def _add_entry(frame, label, var, readonly=False):
            row = ttk.Frame(frame)
            row.pack(fill="x", padx=6, pady=2)
            ttk.Label(row, text=label, width=12, anchor="w").pack(side="left")
            entry = ttk.Entry(row, textvariable=var)
            if readonly:
                entry.configure(state="readonly")
            entry.pack(side="left", fill="x", expand=True, padx=(0, 2))
            return row

        roast_frame = ttk.LabelFrame(inner, text="烘焙信息")
        roast_frame.pack(fill="x", padx=5, pady=5)

        # 烘焙日期（年/月/日分解）
        row = ttk.Frame(roast_frame)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text="烘焙日期:", width=12, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=self.roast_vars['roast_date_year'], width=6).pack(side="left", padx=1)
        ttk.Label(row, text="年").pack(side="left")
        ttk.Entry(row, textvariable=self.roast_vars['roast_date_month'], width=4).pack(side="left", padx=1)
        ttk.Label(row, text="月").pack(side="left")
        ttk.Entry(row, textvariable=self.roast_vars['roast_date_day'], width=4).pack(side="left", padx=1)
        ttk.Label(row, text="日").pack(side="left")
        # 烘焙时间（时/分分解）
        row = ttk.Frame(roast_frame)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text="烘焙时间:", width=12, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=self.roast_vars['roast_time_hour'], width=4).pack(side="left", padx=1)
        ttk.Label(row, text="时").pack(side="left")
        ttk.Entry(row, textvariable=self.roast_vars['roast_time_minute'], width=4).pack(side="left", padx=1)
        ttk.Label(row, text="分").pack(side="left")
        # 烘焙次序（特殊：两个短框并排）
        row = ttk.Frame(roast_frame)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text="烘焙次序:", width=12, anchor="w").pack(side="left")
        ttk.Label(row, text="第").pack(side="left")
        ttk.Entry(row, textvariable=self.roast_vars['roast_no'], width=5).pack(side="left", padx=1)
        ttk.Label(row, text="共").pack(side="left")
        ttk.Entry(row, textvariable=self.roast_vars['roast_total'], width=5).pack(side="left", padx=1)
        # 生豆名称（dropdown + 管理按钮）
        row = ttk.Frame(roast_frame)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text="生豆名称:", width=12, anchor="w").pack(side="left")
        self.bean_combo = ttk.Combobox(row, textvariable=self.bean_name_var, state="readonly")
        self.bean_combo.pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(row, text="管理", command=self._open_bean_manager).pack(side="left")
        self.bean_combo.bind('<<ComboboxSelected>>', self._on_bean_selected)
        # 剩余标准字段
        for key, label, readonly in self.ROAST_FIELDS:
            _add_entry(roast_frame, label, self.roast_vars[key], readonly)
        # 备注（多行）
        row = ttk.Frame(roast_frame)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text="备注:", width=12, anchor="w").pack(side="left")
        self.roast_notes = tk.Text(row, height=4, width=20)
        self.roast_notes.pack(side="left", fill="x", expand=True, padx=(0, 2))

        # 收藏（星标）
        fav_row = ttk.Frame(roast_frame)
        fav_row.pack(fill="x", padx=6, pady=4)
        ttk.Checkbutton(fav_row, text="收藏（星标）",
                        variable=self.roast_favorite_var).pack(side="left")

    def load_file(self, file_path):
        """从文件加载数据"""
        try:
            data = SlogSerializer.read(file_path)
        except FileNotFoundError:
            messagebox.showerror("错误", f"文件不存在:\n{file_path}", parent=self)
            return
        except ValueError as e:
            messagebox.showerror("错误", f"无法加载文件:\n{e}", parent=self)
            return

        # 校验版本
        version = data.get('_version', 0)
        if version < 1:
            messagebox.showwarning("警告", "文件格式版本过低，可能无法正确加载", parent=self)

        # 解析数据
        results = data.get('results', [])
        events = data.get('events', [])
        heater_initial = data['heater_initial']
        fan_initial = data['fan_initial']

        if not results:
            messagebox.showwarning("警告", "文件中没有有效的results数据", parent=self)
            return

        # 更新统计面板
        self.stats_panel.set_results(results)
        self.stats_panel.set_events(events, heater_initial, fan_initial)

        # 更新界面
        self.current_path = file_path
        self.source_identity = os.path.splitext(os.path.basename(file_path))[0]
        self.title(f"Slog Viewer - {self.source_identity}")
        self.stats_panel.status_var.set(
            f"已加载: {os.path.basename(file_path)} "
            f"({len(results)}条记录, {len(events)}个事件)"
        )

        # 加载烘焙信息
        self.roast_vars['roast_no'].set(data.get('roast_no', ''))
        self.roast_vars['roast_total'].set(data.get('roast_total', ''))
        self.roast_vars['green_weight'].set(str(data.get('green_weight') or ''))
        self.roast_vars['roasted_weight'].set(str(data.get('roasted_weight') or ''))

        date_str = data.get('roast_date', '')
        if date_str:
            parts = date_str.split('-')
            if len(parts) == 3:
                self.roast_vars['roast_date_year'].set(parts[0])
                self.roast_vars['roast_date_month'].set(parts[1])
                self.roast_vars['roast_date_day'].set(parts[2])
        time_str = data.get('roast_time', '')
        if time_str:
            parts = time_str.split(':')
            if len(parts) == 2:
                self.roast_vars['roast_time_hour'].set(parts[0])
                self.roast_vars['roast_time_minute'].set(parts[1])
        self.roast_notes.delete('1.0', tk.END)
        self.roast_notes.insert('1.0', data.get('notes', ''))

        # 通过 bean_name 加载生豆信息
        bean_name = data.get('bean_name', '')
        self.bean_name_var.set(bean_name)
        if bean_name:
            bean = next((b for b in self._beans_data if b['name'] == bean_name), None)
            if bean:
                self._apply_bean_info(bean)
                density = data.get('density_override')
                if density is not None:
                    self.roast_vars['density'].set(str(density))
                moisture = data.get('moisture_override')
                if moisture is not None:
                    self.roast_vars['moisture'].set(str(moisture))
            else:
                messagebox.showwarning("警告", f"找不到生豆信息: {bean_name}", parent=self)

        self._update_weight_loss()

    def _collect_roast_info(self):
        """收集烘焙信息为 dict（含 bean_name 和 override 逻辑）"""
        info = {'bean_name': self.bean_name_var.get()}
        # 组合日期
        year = self.roast_vars['roast_date_year'].get()
        month = self.roast_vars['roast_date_month'].get()
        day = self.roast_vars['roast_date_day'].get()
        info['roast_date'] = f"{year}-{month}-{day}" if year and month and day else ''
        # 组合时间
        hour = self.roast_vars['roast_time_hour'].get()
        minute = self.roast_vars['roast_time_minute'].get()
        info['roast_time'] = f"{hour}:{minute}" if hour or minute else ''
        for key in ('roast_no', 'roast_total',
                    'variety', 'process', 'origin', 'altitude', 'season',
                    'green_weight', 'roasted_weight'):
            info[key] = self.roast_vars[key].get()
        # density/moisture: 只存 override（与 bean info 默认不同才存）
        bean_name = info['bean_name']
        bean = next((b for b in self._beans_data if b['name'] == bean_name), None)
        for key in ('density', 'moisture'):
            val = self.roast_vars[key].get()
            default = bean.get(key, '') if bean else ''
            info[key] = val if val != default else ''
        info['weight_loss'] = self.roast_vars['weight_loss'].get()
        info['notes'] = self.roast_notes.get('1.0', tk.END).strip()
        return info

    # ====== 生豆信息管理 ======

    def _load_bean_info(self):
        """加载生豆信息，刷新 dropdown"""
        self._beans_data = []
        try:
            repo = SqliteBeanRepository()
            all_beans = repo.list_all()
            self._beans_data = [b for b in all_beans if not b.get('outOfStock', False)]
        except Exception:
            self._beans_data = []
        names = [b['name'] for b in self._beans_data if b.get('name')]
        self.bean_combo['values'] = names

    def _on_bean_selected(self, event=None):
        """下拉框选中生豆"""
        name = self.bean_name_var.get()
        if not name:
            return
        bean = next((b for b in self._beans_data if b['name'] == name), None)
        if bean:
            self._apply_bean_info(bean)

    def _apply_bean_info(self, bean):
        """从 bean dict 填充字段（不覆盖已存在的 density/moisture）"""
        for key in ('variety', 'process', 'origin', 'altitude', 'season'):
            self.roast_vars[key].set(bean.get(key, ''))
        for key in ('density', 'moisture'):
            if not self.roast_vars[key].get():
                self.roast_vars[key].set(bean.get(key, ''))

    def _open_bean_manager(self):
        """打开生豆信息管理窗口（单例，重复点击激活已有窗口）"""
        if self._bean_manager is not None:
            try:
                if self._bean_manager.winfo_exists():
                    self._bean_manager.lift()
                    self._bean_manager.focus_set()
                    return
            except tk.TclError:
                pass
        from ui.bean_manager import BeanManager
        self._bean_manager = BeanManager(self, on_save_callback=self._refresh_bean_dropdown)

    def _refresh_bean_dropdown(self):
        """BeanManager 保存后刷新 dropdown"""
        self._load_bean_info()
        current = self.bean_name_var.get()
        if current and current in self.bean_combo['values']:
            self.bean_name_var.set(current)

    def open_slog(self, event=None):
        """打开.slog文件"""
        file_path = filedialog.askopenfilename(
            title="打开.slog文件",
            filetypes=[("Slog files", "*.slog"), ("All files", "*.*")]
        )
        if file_path:
            self.load_file(file_path)

    def export_slog(self, event=None):
        """导出.slog文件"""
        if not self.stats_panel.results:
            messagebox.showwarning("警告", "没有可导出的数据", parent=self)
            return

        default_name = self.source_identity or "export"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".slog",
            initialfile=f"{default_name}.slog",
            filetypes=[("Slog files", "*.slog"), ("All files", "*.*")]
        )

        if not file_path:
            return

        session = {
            'roast_info': self._collect_roast_info(),
            'results': self.stats_panel.results,
            'events': self.stats_panel.events,
            'heater_initial': self.stats_panel.heater_initial,
            'fan_initial': self.stats_panel.fan_initial,
        }
        SlogSerializer.write(file_path, session)

        self.stats_panel.status_var.set(f"数据已导出到: {file_path}")

    def load_from_session_id(self, session_id: str):
        """从数据库加载会话数据"""
        session = self._session_repo.load(session_id)
        if not session:
            messagebox.showerror("错误", f"未找到会话: {session_id}", parent=self)
            return
        results = self._result_repo.load(session_id) or []
        events = self._event_repo.load(session_id) or []

        if not results:
            messagebox.showwarning("警告", "会话中没有温度数据", parent=self)
            return

        # 更新统计面板
        self.stats_panel.set_results(results)
        self.stats_panel.set_events(
            events, session.get('heater_initial', 60.0), session.get('fan_initial', 50.0))

        # 更新界面
        self._rw_session_id = session_id
        display_name = self._session_repo.get_display_name(session_id)
        self.source_identity = display_name
        self.title(f"Slog Viewer - {display_name}")
        self.stats_panel.status_var.set(
            f"已加载: {session_id} ({len(results)}条记录, {len(events)}个事件)")

        # 加载烘焙信息（roast_vars 中存在的字段，日期/时间由分解字段单独处理）
        for key in ('roast_no', 'roast_total', 'green_weight', 'roasted_weight'):
            self.roast_vars[key].set(session.get(key, '') or '')
        # 日期分解
        date_str = session.get('roast_date', '')
        if date_str:
            parts = date_str.split('-')
            if len(parts) == 3:
                self.roast_vars['roast_date_year'].set(parts[0])
                self.roast_vars['roast_date_month'].set(parts[1])
                self.roast_vars['roast_date_day'].set(parts[2])
        # 时间分解
        time_str = session.get('roast_time', '')
        if time_str:
            parts = time_str.split(':')
            if len(parts) == 2:
                self.roast_vars['roast_time_hour'].set(parts[0])
                self.roast_vars['roast_time_minute'].set(parts[1])
        # 加载豆名（通过 bean_id 查询）
        bean_id = session.get('bean_id')
        if bean_id:
            bean = SqliteBeanRepository().get_by_id(bean_id)
            if bean:
                self.bean_name_var.set(bean.get('name', ''))
                self._apply_bean_info(bean)
        # density/moisture override
        density = session.get('density_override')
        if density is not None:
            self.roast_vars['density'].set(str(density))
        moisture = session.get('moisture_override')
        if moisture is not None:
            self.roast_vars['moisture'].set(str(moisture))
        # notes
        self.roast_notes.delete('1.0', tk.END)
        self.roast_notes.insert('1.0', session.get('notes', ''))
        # 收藏
        self.roast_favorite_var.set(session.get('is_favorite', False))

    def save_to_database(self):
        """保存当前曲线/烘焙信息到数据库"""
        if not self.stats_panel.results:
            messagebox.showwarning("警告", "没有可导出的数据", parent=self)
            return

        # 字段校验（只有熟豆重量允许为空）
        errors = []
        year = self.roast_vars['roast_date_year'].get()
        month = self.roast_vars['roast_date_month'].get()
        day = self.roast_vars['roast_date_day'].get()
        if not (year and month and day):
            errors.append("烘焙日期（年/月/日）")
        hour = self.roast_vars['roast_time_hour'].get()
        minute = self.roast_vars['roast_time_minute'].get()
        if not (hour or minute):
            errors.append("烘焙时间（时/分）")
        if not self.roast_vars['roast_no'].get():
            errors.append("烘焙编号")
        if not self.roast_vars['roast_total'].get():
            errors.append("总炉数")
        if not self.roast_vars['green_weight'].get():
            errors.append("生豆重量")
        if not self.bean_name_var.get():
            errors.append("生豆名称")
        if errors:
            messagebox.showerror("保存失败",
                                 f"请填写以下必填字段:\n" + "\n".join(errors),
                                 parent=self)
            return

        from data.sqlite.session_repo import next_session_id

        roast_info = self._collect_roast_info()
        sid = self._rw_session_id or next_session_id(self._session_repo.db_path)

        session = {
            'session_id': sid,
            'is_raw_data': False,
            'bean_id': self._resolve_bean_id(roast_info.get('bean_name', '')),
            'heater_initial': self.stats_panel.heater_initial,
            'fan_initial': self.stats_panel.fan_initial,
            'density_override': self._try_float(roast_info.get('density')),
            'moisture_override': self._try_float(roast_info.get('moisture')),
            'roast_date': f"{year}-{month}-{day}",
            'roast_time': f"{hour}:{minute}",
            'roast_no': roast_info.get('roast_no', ''),
            'roast_total': roast_info.get('roast_total', ''),
            'green_weight': self._try_float(roast_info.get('green_weight')),
            'roasted_weight': self._try_float(roast_info.get('roasted_weight')),
            'notes': self.roast_notes.get('1.0', tk.END).strip(),
            'is_favorite': self.roast_favorite_var.get(),
        }

        # 原子写入
        writer = SessionWriter(session_repo=self._session_repo,
                               result_repo=self._result_repo,
                               event_repo=self._event_repo)
        writer.save_full(sid, session, self.stats_panel.results,
                         self.stats_panel._original_events)
        self._rw_session_id = sid

        self.stats_panel.status_var.set(f"已保存到数据库: {sid}")

    @staticmethod
    def _try_float(v):
        if v is None or v == '':
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    def _resolve_bean_id(self, bean_name: str):
        """通过生豆名称查找 bean_id（从 SQLite）"""
        if not bean_name:
            return None
        from data.sqlite.bean_repo import SqliteBeanRepository
        bean = SqliteBeanRepository().get_by_name(bean_name)
        return bean.get('id') if bean else None

    def open_comparer(self, event=None):
        """打开曲线对比器（单例，重复点击激活已有窗口）"""
        if self._comparer is not None:
            try:
                if self._comparer.winfo_exists():
                    self._comparer.lift()
                    self._comparer.focus_set()
                    return
            except tk.TclError:
                pass

        if not self.current_path:
            messagebox.showwarning("警告", "请先打开一个.slog文件", parent=self)
            return

        files = filedialog.askopenfilenames(
            title="选择对比的.slog文件",
            filetypes=[("Slog files", "*.slog"), ("All files", "*.*")]
        )
        if not files:
            return

        all_files = list(dict.fromkeys([self.current_path] + list(files)))
        if len(all_files) > 5:
            messagebox.showerror("错误", "最多允许5个slog参与对比", parent=self)
            return

        from ui.slog_comparer import SlogComparer
        self._comparer = SlogComparer(self, file_paths=all_files)



def open_slog_viewer(parent, session_id=None, file_path=None):
    """从父窗口打开slog viewer

    Args:
        parent: 父窗口
        file_path: .slog 文件路径（可选）
        session_id: 数据库会话 ID（可选，优先于 file_path）
    """
    return SlogViewer(parent, file_path=file_path, session_id=session_id)


def main():
    root = tk.Tk()
    root.withdraw()
    file_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = SlogViewer(root, file_path)
    app.protocol("WM_DELETE_WINDOW", lambda: (root.quit(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
