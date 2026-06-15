"""
Slog曲线对比器 - 多曲线对比分析工具

功能：
1. 加载多个.slog文件进行曲线对比（最多5条）
2. 以入豆/回温/一爆开始事件对齐时间轴
3. 显示豆温、风温、ROR、火力、风门曲线
4. 独立运行或从SlogViewer启动
"""

# ====== Windows DPI感知 ======
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import sys

import numpy as np
from scipy.signal import savgol_filter
from scipy.interpolate import interp1d
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.transforms import blended_transform_factory
from matplotlib.ticker import FuncFormatter
import warnings
warnings.filterwarnings('ignore')


# 确保能导入项目模块
def _setup_path():
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(this_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


_setup_path()

from data.serializers.slog import SlogSerializer
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.result_repo import SqliteResultRepository
from data.sqlite.event_repo import SqliteEventRepository

# ====== 常量 ======
MAX_SLOG_COUNT = 5

# 颜色映射（中文名 → 色值）
COLOR_MAP = [
    ('蓝色', '#1f77b4'),
    ('橙色', '#ff7f0e'),
    ('绿色', '#2ca02c'),
    ('红色', '#d62728'),
    ('紫色', '#9467bd'),
]
COLOR_NAMES = [c[0] for c in COLOR_MAP]
COLOR_HEXES = [c[1] for c in COLOR_MAP]

# 对齐事件选项
ALIGN_OPTIONS = ['入豆', '回温', '一爆开始']

# 曲线类型配置
CURVE_CONFIG = {
    '豆温': {'default': True, 'axis': 'main', 'linestyle': '-', 'linewidth': 2},
    '风温': {'default': True, 'axis': 'main', 'linestyle': '--', 'linewidth': 2},
    'ROR':  {'default': False, 'axis': 'ror', 'linestyle': '-.', 'linewidth': 1.5},
    '火力': {'default': False, 'axis': 'hf', 'linestyle': ':', 'linewidth': 1.5},
    '风门': {'default': False, 'axis': 'hf', 'linestyle': (0, (10, 3)), 'linewidth': 1.5},
}

# 处理参数
SAMPLING_INTERVAL = 1.0
SMOOTH_WINDOW = 15
SMOOTH_POLYORDER = 3
ROR_INTERVAL = 15.0

# ROR轴
ROR_COMPRESSION_FACTOR = 12.0
ROR_CUSTOM_TICKS = [-120, -100, -80, -60, -40, -20, 0, 5, 10, 15, 20, 25, 30]
ROR_YLIM = (-120, 30)

REQUIRED_EVENTS = ['入豆', '回温', '一爆开始']


# ====== 中文字体 ======
def setup_chinese_font():
    try:
        font_paths = [
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
        ]
        for font_path in font_paths:
            if os.path.exists(font_path):
                matplotlib.font_manager.fontManager.addfont(font_path)
                font_name = matplotlib.font_manager.FontProperties(fname=font_path).get_name()
                plt.rcParams['font.sans-serif'] = [font_name]
                plt.rcParams['axes.unicode_minus'] = False
                return True
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun']
        plt.rcParams['axes.unicode_minus'] = False
        return True
    except Exception:
        return False


setup_chinese_font()


# ====== ROR非均匀Y轴变换 ======
def ror_forward(x):
    return np.where(x >= 0, x, x / ROR_COMPRESSION_FACTOR)


def ror_inverse(x):
    return np.where(x >= 0, x, x * ROR_COMPRESSION_FACTOR)


# ====== 数据处理函数（静态，从StatisticsPanel提取） ======

def extract_valid_data(results):
    """
    从results中提取有效数据

    Returns:
        (timestamps, temp1_values, temp2_values) numpy数组
    """
    timestamps = []
    temp1_values = []
    temp2_values = []

    for result in results:
        if '?' in str(result.get('temp1_full', '')) or '?' in str(result.get('temp2', '')):
            continue
        try:
            timestamp = float(result['timestamp'])
            if timestamp < 0:
                continue
            temp1 = float(result['temp1_full'])
            temp2 = float(result['temp2'])
            timestamps.append(timestamp)
            temp1_values.append(temp1)
            temp2_values.append(temp2)
        except (ValueError, KeyError):
            continue

    return (np.array(timestamps), np.array(temp1_values),
            np.array(temp2_values))


def resample_data(timestamps, values, sampling_interval):
    """等间隔重采样"""
    if len(timestamps) < 2:
        return timestamps, values

    start_time = np.min(timestamps)
    end_time = np.max(timestamps)
    resampled_time = np.arange(start_time, end_time + sampling_interval, sampling_interval)
    interp_func = interp1d(timestamps, values, kind='linear',
                           bounds_error=False, fill_value='extrapolate')
    resampled_values = interp_func(resampled_time)
    return resampled_time, resampled_values


def smooth_data(time, values, window_seconds, polyorder):
    """Savitzky-Golay滤波平滑"""
    if len(values) < window_seconds:
        return values

    if len(time) > 1:
        dt = time[1] - time[0]
        window_points = int(window_seconds / dt)
        window_points = window_points if window_points % 2 == 1 else window_points + 1
        window_points = max(polyorder + 1, window_points)
        if window_points <= len(values):
            try:
                return savgol_filter(values, window_points, polyorder)
            except Exception:
                pass
    return values


def compute_ror(time, temperature, sampling_interval, ror_interval):
    """计算ROR（升温速率）"""
    if len(temperature) < 2:
        return np.array([]), np.array([])

    step = max(1, int(round(ror_interval / sampling_interval)))
    if step >= len(temperature):
        return np.array([]), np.array([])

    dt = step * sampling_interval
    dT = temperature[step:] - temperature[:-step]
    ror_values = (dT / dt) * 60.0
    ror_time = time[step:]
    return ror_time, ror_values


def build_heater_fan_data(resampled_time, events, heater_initial, fan_initial):
    """从事件数据构建火力和风门的时间序列"""
    if resampled_time is None or len(resampled_time) == 0:
        return None, None

    heater = np.full_like(resampled_time, float(heater_initial))
    fan = np.full_like(resampled_time, float(fan_initial))

    sorted_events = sorted(events, key=lambda x: x.get('time', 0))
    for ev in sorted_events:
        ev_time = ev.get('time', 0)
        ev_type = ev.get('type', '')
        ev_value = ev.get('value')
        if ev_value is None:
            continue
        idx = np.searchsorted(resampled_time, ev_time)
        if idx >= len(resampled_time):
            continue
        if ev_type == '调整火力':
            heater[idx:] = float(ev_value)
        elif ev_type == '调整风门':
            fan[idx:] = float(ev_value)

    return heater, fan


def seconds_to_mmss(seconds):
    """格式化秒数为mm:ss，支持负数"""
    if seconds is None:
        return "00:00"
    abs_sec = abs(seconds)
    minutes = int(abs_sec // 60)
    secs = int(abs_sec % 60)
    if seconds < 0:
        return f"-{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# ====== SlogComparer主类 ======

class SlogComparer(tk.Toplevel):
    """Slog曲线对比器"""

    def __init__(self, master=None, file_paths=None, session_ids=None):
        super().__init__(master)
        self.title("Slog Comparer - 曲线对比")
        self.minsize(1400, 900)
        self._center_window()

        # 数据库（session_ids 模式用）
        self._session_repo = None
        self._result_repo = None
        self._event_repo = None

        # 状态
        self.slogs = []  # list[dict] 每个元素为一个slog的数据
        self.align_var = tk.StringVar(value='入豆')
        self.align_var.trace_add('write', lambda *_: self._on_refresh())
        self.ror_interval_var = tk.DoubleVar(value=ROR_INTERVAL)
        self._current_ror_interval = ROR_INTERVAL
        self._main_ax = None
        self._ror_ax = None
        self._hf_ax = None

        # 曲线类型开关
        self.curve_vars = {}
        for name, cfg in CURVE_CONFIG.items():
            var = tk.BooleanVar(value=cfg['default'])
            var.trace_add('write', lambda *_: self._on_refresh())
            self.curve_vars[name] = var

        # 创建UI
        self._create_ui()

        # 如果有初始数据，加载
        if session_ids:
            self._session_repo = SqliteSessionRepository()
            self._result_repo = SqliteResultRepository()
            self._event_repo = SqliteEventRepository()
            self.load_from_session_ids(session_ids)
        elif file_paths:
            self._load_multiple_slogs(file_paths)

        # 快捷键
        self.bind('<Control-q>', lambda e: self.destroy())

    # ========== UI ==========

    def _center_window(self):
        """直接计算居中位置"""
        w, h = 2800, 1800
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _create_ui(self):
        """构建完整UI"""
        # 上半区
        top_frame = ttk.Frame(self)
        top_frame.pack(fill="x", padx=10, pady=(10, 5))

        # 左上：曲线管理
        left_frame = ttk.LabelFrame(top_frame, text="曲线管理")
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self._create_slog_management(left_frame)

        # 右上：对齐与显示
        right_frame = ttk.LabelFrame(top_frame, text="对齐与显示")
        right_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))
        self._create_control_panel(right_frame)

        # 下半区：图表
        self._create_chart_area()

        # 窗口resize自动刷新
        self.bind('<Configure>', self._on_window_resize)

    def _create_slog_management(self, parent):
        """创建曲线管理区域"""
        # 添加按钮
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill="x", padx=5, pady=(5, 2))
        ttk.Button(btn_frame, text="+ 选择曲线",
                   command=self._on_add_slog).pack(side="left", padx=2)

        # 可滚动列表
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        self._slog_canvas = tk.Canvas(list_frame, height=200, highlightthickness=0)
        self._slog_scrollbar = ttk.Scrollbar(list_frame, orient="vertical",
                                              command=self._slog_canvas.yview)
        self._slog_inner = ttk.Frame(self._slog_canvas)

        self._slog_inner.bind("<Configure>", lambda e: self._slog_canvas.configure(
            scrollregion=self._slog_canvas.bbox("all")))

        self._slog_canvas.create_window((0, 0), window=self._slog_inner, anchor="nw", tags="inner")
        self._slog_canvas.configure(yscrollcommand=self._slog_scrollbar.set)

        self._slog_canvas.pack(side="left", fill="both", expand=True)
        self._slog_scrollbar.pack(side="right", fill="y")

        # 初始提示
        self._no_slog_label = ttk.Label(self._slog_inner, text="（暂无曲线，点击上方按钮添加）",
                                        foreground="gray")
        self._no_slog_label.pack(padx=10, pady=10)

    def _rebuild_slog_list(self):
        """重建slog列表UI"""
        # 清空内帧
        for w in self._slog_inner.winfo_children():
            w.destroy()

        if not self.slogs:
            self._no_slog_label = ttk.Label(self._slog_inner, text="（暂无曲线，点击上方按钮添加）",
                                            foreground="gray")
            self._no_slog_label.pack(padx=10, pady=10)
            return

        for idx, slog in enumerate(self.slogs):
            row = ttk.Frame(self._slog_inner)
            row.pack(fill="x", padx=3, pady=2)

            # 复选框
            cb = ttk.Checkbutton(row, variable=slog['visible'],
                                 command=lambda i=idx: self._on_visible_toggled(i))
            cb.pack(side="left", padx=(2, 5))

            # 文件名（显示短名称）
            name_label = ttk.Label(row, text=slog['name'], width=30, anchor="w")
            name_label.pack(side="left", padx=(0, 5), fill="x", expand=True)

            # 颜色下拉
            color_var = slog['color']
            color_cb = ttk.Combobox(row, values=COLOR_NAMES, state="readonly",
                                     width=8)
            color_cb.set(color_var.get())
            color_cb.bind('<<ComboboxSelected>>',
                          lambda e, i=idx: self._on_color_changed(i, e.widget))
            color_cb.pack(side="left", padx=(0, 2))

            # 右键菜单
            name_label.bind('<Button-3>',
                           lambda e, i=idx: self._show_context_menu(e, i))
            row.bind('<Button-3>',
                    lambda e, i=idx: self._show_context_menu(e, i))

    def _show_context_menu(self, event, idx):
        """显示右键菜单"""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="移除该曲线",
                         command=lambda: self._remove_slog(idx))
        menu.tk_popup(event.x_root, event.y_root)

    def _create_control_panel(self, parent):
        """创建控制面板"""
        # 对齐方式
        align_frame = ttk.LabelFrame(parent, text="对齐方式")
        align_frame.pack(fill="x", padx=8, pady=(8, 5))

        for opt in ALIGN_OPTIONS:
            ttk.Radiobutton(align_frame, text=opt, variable=self.align_var,
                           value=opt).pack(side="left", padx=(10, 0), pady=3)

        # 显示曲线
        curve_frame = ttk.LabelFrame(parent, text="显示曲线")
        curve_frame.pack(fill="x", padx=8, pady=5)

        # 第一行：复选框
        cb_row = ttk.Frame(curve_frame)
        cb_row.pack(fill="x")
        for name in CURVE_CONFIG:
            ttk.Checkbutton(cb_row, text=name,
                           variable=self.curve_vars[name]).pack(
                               side="left", padx=(10, 0), pady=3)

        # 第二行：ROR参数（勾选ROR时显示）
        self.ror_param_frame = ttk.Frame(curve_frame)
        ttk.Label(self.ror_param_frame, text="ROR步长(秒):").pack(
            side="left", padx=(10, 2))
        ror_spin = ttk.Spinbox(self.ror_param_frame, from_=1, to=30, increment=1,
                               textvariable=self.ror_interval_var, width=6)
        ror_spin.pack(side="left")
        ror_spin.bind('<Return>', self._on_ror_param_changed)
        ror_spin.bind('<FocusOut>', self._on_ror_param_changed)

        # 排除阶段外数据
        filter_frame = ttk.Frame(parent)
        filter_frame.pack(fill="x", padx=8, pady=(0, 5))
        self.exclude_outside_var = tk.BooleanVar(value=False)
        self.exclude_outside_var.trace_add('write', lambda *_: self._on_refresh())
        ttk.Checkbutton(filter_frame, text="排除阶段外数据",
                       variable=self.exclude_outside_var).pack(side="left", padx=(10, 0))

    def _create_chart_area(self):
        """创建图表区域"""
        chart_frame = ttk.Frame(self)
        chart_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 鼠标读数栏（图表上方，靠右显示）
        cursor_frame = ttk.Frame(chart_frame)
        cursor_frame.pack(fill="x", pady=(0, 2))
        self.cursor_label = ttk.Label(cursor_frame, text="", anchor="e",
                                       font=("", 9))
        self.cursor_label.pack(side="right", padx=5)

        self.fig = Figure(figsize=(14, 8), dpi=150)
        self.canvas = FigureCanvasTkAgg(self.fig, chart_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _on_window_resize(self, event):
        """窗口resize回调"""
        if event.widget == self:
            self._on_refresh()

    # ========== 加载与处理 ==========

    def _load_multiple_slogs(self, file_paths):
        """加载多个slog文件"""
        loaded_paths = {s['path'] for s in self.slogs}
        for path in file_paths:
            if path in loaded_paths:
                messagebox.showwarning("警告", f"文件已添加: {os.path.basename(path)}")
                continue
            if len(self.slogs) >= MAX_SLOG_COUNT:
                messagebox.showerror("错误", f"最多只能对比{MAX_SLOG_COUNT}条曲线")
                break
            self._load_slog(path)

    def _load_slog(self, file_path):
        """加载单个slog文件"""
        try:
            data = SlogSerializer.read(file_path)
        except FileNotFoundError:
            messagebox.showerror("错误", f"文件不存在:\n{file_path}", parent=self)
            return
        except ValueError as e:
            messagebox.showerror("错误", f"无法加载文件:\n{file_path}\n{e}", parent=self)
            return

        version = data.get('_version', 0)
        if version < 1:
            if not messagebox.askyesno("警告", "文件格式版本过低，是否继续加载?"):
                return

        results = data.get('results', [])
        events = data.get('events', [])
        heater_initial = data['heater_initial']
        fan_initial = data['fan_initial']

        if not results:
            messagebox.showwarning("警告", f"{os.path.basename(file_path)} 没有有效数据")
            return

        # 校验必需事件
        ok, missing = self._validate_required_events(events)
        if not ok:
            messagebox.showerror("错误",
                f"{os.path.basename(file_path)} 缺少必需事件: {', '.join(missing)}")
            return

        # 创建slog数据字典
        fname = os.path.basename(file_path)
        color_idx = len(self.slogs) % len(COLOR_MAP)
        slog_data = {
            'path': file_path,
            'name': fname,
            'visible': tk.BooleanVar(value=True),
            'color': tk.StringVar(value=COLOR_NAMES[color_idx]),
            'results': results,
            'events': events,
            'heater_initial': heater_initial,
            'fan_initial': fan_initial,
            'alignment': {},
            # 以下由_process_slog填充
            'resampled_time': None,
            'smooth_temp1': None,
            'smooth_temp2': None,
            'ror_time': None,
            'ror_values': None,
            'heater': None,
            'fan': None,
        }

        # 提取对齐事件时间
        for ev_type in REQUIRED_EVENTS:
            t = self._get_event_time(events, ev_type)
            slog_data['alignment'][ev_type] = t

        # 处理数据
        self._process_slog(slog_data)

        self.slogs.append(slog_data)
        self._rebuild_slog_list()
        self._on_refresh()

    def load_from_session_ids(self, session_ids):
        """从数据库加载多个会话进行对比"""
        for sid in session_ids:
            if len(self.slogs) >= MAX_SLOG_COUNT:
                messagebox.showerror("错误", f"最多只能对比{MAX_SLOG_COUNT}条曲线")
                break
            self._load_session(sid)

    def _load_session(self, session_id):
        """从数据库加载单个会话"""
        session = self._session_repo.load(session_id)
        if not session:
            messagebox.showerror("错误", f"未找到会话: {session_id}", parent=self)
            return
        results = self._result_repo.load(session_id) or []
        events = self._event_repo.load(session_id) or []

        if not results:
            messagebox.showwarning("警告", f"会话 {session_id} 没有温度数据", parent=self)
            return

        # 校验必需事件
        ok, missing = self._validate_required_events(events)
        if not ok:
            messagebox.showerror("错误",
                f"会话 {session_id} 缺少必需事件: {', '.join(missing)}", parent=self)
            return

        name = self._session_repo.get_display_name(session_id)

        color_idx = len(self.slogs) % len(COLOR_MAP)
        slog_data = {
            'path': session_id,
            'name': name,
            'visible': tk.BooleanVar(value=True),
            'color': tk.StringVar(value=COLOR_NAMES[color_idx]),
            'results': results,
            'events': events,
            'heater_initial': session.get('heater_initial', 60.0),
            'fan_initial': session.get('fan_initial', 50.0),
            'alignment': {},
            'resampled_time': None,
            'smooth_temp1': None,
            'smooth_temp2': None,
            'ror_time': None,
            'ror_values': None,
            'heater': None,
            'fan': None,
        }

        for ev_type in REQUIRED_EVENTS:
            t = self._get_event_time(events, ev_type)
            slog_data['alignment'][ev_type] = t

        self._process_slog(slog_data)
        self.slogs.append(slog_data)
        self._rebuild_slog_list()
        self._on_refresh()

    def _validate_required_events(self, events):
        """校验必需事件是否存在"""
        found = set()
        for ev in events:
            ev_type = ev.get('type', '')
            if ev_type in REQUIRED_EVENTS:
                found.add(ev_type)
        missing = [e for e in REQUIRED_EVENTS if e not in found]
        return (len(missing) == 0, missing)

    def _process_slog(self, slog_data):
        """处理单个slog数据：提取→重采样→平滑→ROR→火力风门"""
        timestamps, temp1, temp2 = extract_valid_data(slog_data['results'])
        if len(timestamps) < 2:
            return

        # 重采样
        slog_data['resampled_time'], slog_data['resampled_temp1'] = \
            resample_data(timestamps, temp1, SAMPLING_INTERVAL)
        _, slog_data['resampled_temp2'] = \
            resample_data(timestamps, temp2, SAMPLING_INTERVAL)

        # 平滑
        slog_data['smooth_temp1'] = smooth_data(
            slog_data['resampled_time'], slog_data['resampled_temp1'],
            SMOOTH_WINDOW, SMOOTH_POLYORDER)
        slog_data['smooth_temp2'] = smooth_data(
            slog_data['resampled_time'], slog_data['resampled_temp2'],
            SMOOTH_WINDOW, SMOOTH_POLYORDER)

        # ROR
        ror_interval = self.ror_interval_var.get() if hasattr(self, 'ror_interval_var') else ROR_INTERVAL
        slog_data['ror_time'], slog_data['ror_values'] = compute_ror(
            slog_data['resampled_time'], slog_data['smooth_temp1'],
            SAMPLING_INTERVAL, ror_interval)

        # 火力风门
        slog_data['heater'], slog_data['fan'] = build_heater_fan_data(
            slog_data['resampled_time'], slog_data['events'],
            slog_data['heater_initial'], slog_data['fan_initial'])

    def _get_event_time(self, events, event_type):
        """获取指定事件的时间"""
        for ev in events:
            if ev.get('type', '') == event_type:
                return ev.get('time', 0.0)
        return 0.0

    # ========== 交互回调 ==========

    def _on_add_slog(self):
        """添加更多slog"""
        if len(self.slogs) >= MAX_SLOG_COUNT:
            messagebox.showerror("错误", f"最多只能对比{MAX_SLOG_COUNT}条曲线")
            return

        files = filedialog.askopenfilenames(
            title="选择.slog文件",
            filetypes=[("Slog files", "*.slog"), ("All files", "*.*")]
        )
        if files:
            self._load_multiple_slogs(files)

    def _remove_slog(self, idx):
        """移除一个slog"""
        if 0 <= idx < len(self.slogs):
            del self.slogs[idx]
            self._rebuild_slog_list()
            self._on_refresh()

    def _on_visible_toggled(self, idx):
        """复选框回调"""
        self._on_refresh()

    def _on_color_changed(self, idx, widget):
        """颜色下拉回调"""
        if 0 <= idx < len(self.slogs):
            self.slogs[idx]['color'].set(widget.get())
            self._on_refresh()

    def _on_ror_param_changed(self, event=None):
        """ROR参数变更回调"""
        new_val = self.ror_interval_var.get()
        if abs(new_val - self._current_ror_interval) > 0.01:
            self._current_ror_interval = new_val
            for slog in self.slogs:
                slog['ror_time'], slog['ror_values'] = compute_ror(
                    slog['resampled_time'], slog['smooth_temp1'],
                    SAMPLING_INTERVAL, new_val)
        self._on_refresh()

    def _on_refresh(self, *args):
        """刷新图表"""
        # 切换ROR参数面板显隐
        if hasattr(self, 'ror_param_frame'):
            if self.curve_vars['ROR'].get():
                if not self.ror_param_frame.winfo_ismapped():
                    self.ror_param_frame.pack(fill="x", padx=8, pady=2)
            else:
                if self.ror_param_frame.winfo_ismapped():
                    self.ror_param_frame.pack_forget()
        self._plot_comparison()

    # ========== 绘图 ==========

    def _plot_comparison(self):
        """绘制对比曲线"""
        self.fig.clear()

        align_event = self.align_var.get()
        active_curve_types = [name for name, var in self.curve_vars.items()
                              if var.get()]
        visible_slogs = [s for s in self.slogs if s['visible'].get()]

        # 无数据
        if not self.slogs:
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, '请添加曲线', ha='center', va='center',
                   transform=ax.transAxes, fontsize=14, color='gray')
            self.canvas.draw()
            return

        if not active_curve_types:
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, '请选择要显示的曲线类型', ha='center', va='center',
                   transform=ax.transAxes, fontsize=14, color='gray')
            self.canvas.draw()
            return

        # 创建主坐标轴
        self._main_ax = self.fig.add_subplot(111)
        self._main_ax.set_xlabel('时间 (mm:ss)')
        self._main_ax.set_ylabel('温度 (℃)', color='tab:blue')
        self._main_ax.tick_params(axis='y', labelcolor='tab:blue')
        self._main_ax.grid(True, alpha=0.3)
        self._main_ax.set_title('曲线对比 (对齐: %s)' % align_event)
        ax = self._main_ax  # 本地别名，供剩余绘图代码使用

        # 准备ROR和HF轴
        self._ror_ax = None
        self._hf_ax = None
        has_ror = 'ROR' in active_curve_types and any(
            s['ror_values'] is not None and len(s['ror_values']) > 0
            for s in visible_slogs)
        has_hf = ('火力' in active_curve_types or '风门' in active_curve_types) and any(
            s['heater'] is not None for s in visible_slogs)

        if has_ror:
            self._ror_ax = self._main_ax.twinx()
            self._ror_ax.set_ylabel('ROR (℃/min)', color='tab:red')
            self._ror_ax.set_yscale('function', functions=(ror_forward, ror_inverse))
            self._ror_ax.set_ylim(*ROR_YLIM)
            self._ror_ax.yaxis.set_major_locator(matplotlib.ticker.FixedLocator(ROR_CUSTOM_TICKS))
            self._ror_ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
            self._ror_ax.tick_params(axis='y', labelcolor='tab:red')
            self._ror_ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)

        if has_hf:
            self._hf_ax = self._main_ax.twinx()
            hf_pos = 1.05 if has_ror else 1.00
            self._hf_ax.spines['right'].set_position(('axes', hf_pos))
            self._hf_ax.set_ylabel('火力/风门 (%)', color='green')
            self._hf_ax.set_ylim(0, 200)
            self._hf_ax.tick_params(axis='y', labelcolor='green')

        # 收集所有图例
        all_lines = []
        all_labels = []

        # 遍历并绘制每个slog
        for slog in self.slogs:
            visible = slog['visible'].get()
            alpha = 1.0 if visible else 0.2
            color_name = slog['color'].get()
            color_hex = COLOR_HEXES[COLOR_NAMES.index(color_name)] if color_name in COLOR_NAMES else COLOR_HEXES[0]

            # 对齐偏移
            align_time = slog['alignment'].get(align_event, 0.0)

            # 排除阶段外数据：计算每个slog的过滤掩码
            filter_mask = None
            filter_ct = None
            filter_dt = None
            if hasattr(self, 'exclude_outside_var') and self.exclude_outside_var.get():
                filter_ct = slog['alignment'].get('入豆', None)
                for ev in slog['events']:
                    if ev.get('type', '') == '烘焙结束':
                        filter_dt = ev.get('time', None)
                        break
                if filter_ct is not None and filter_dt is not None and filter_dt > filter_ct:
                    rt = slog['resampled_time']
                    filter_mask = (rt >= filter_ct) & (rt <= filter_dt)

            for curve_name in active_curve_types:
                if curve_name == 'ROR':
                    data_time = slog['ror_time']
                    data_vals = slog['ror_values']
                    if data_time is None or len(data_time) == 0:
                        continue
                    if self._ror_ax is None:
                        continue
                    t = data_time - align_time
                    if filter_mask is not None and filter_ct is not None and filter_dt is not None:
                        rm = (data_time >= filter_ct) & (data_time <= filter_dt)
                        if np.any(rm):
                            t = t[rm]
                            data_vals = data_vals[rm]
                        else:
                            continue
                    line, = self._ror_ax.plot(t, data_vals, color=color_hex,
                                        linestyle=CURVE_CONFIG[curve_name]['linestyle'],
                                        linewidth=CURVE_CONFIG[curve_name]['linewidth'],
                                        alpha=alpha)
                elif curve_name == '火力':
                    data_vals = slog['heater']
                    if data_vals is None:
                        continue
                    if self._hf_ax is None:
                        continue
                    t = slog['resampled_time'] - align_time
                    if filter_mask is not None:
                        t = t[filter_mask]
                        data_vals = data_vals[filter_mask]
                    line, = self._hf_ax.plot(t, data_vals, color=color_hex,
                                      linestyle=CURVE_CONFIG[curve_name]['linestyle'],
                                      linewidth=CURVE_CONFIG[curve_name]['linewidth'],
                                      alpha=alpha)
                elif curve_name == '风门':
                    data_vals = slog['fan']
                    if data_vals is None:
                        continue
                    if self._hf_ax is None:
                        continue
                    t = slog['resampled_time'] - align_time
                    if filter_mask is not None:
                        t = t[filter_mask]
                        data_vals = data_vals[filter_mask]
                    line, = self._hf_ax.plot(t, data_vals, color=color_hex,
                                      linestyle=CURVE_CONFIG[curve_name]['linestyle'],
                                      linewidth=CURVE_CONFIG[curve_name]['linewidth'],
                                      alpha=alpha)
                else:
                    # 豆温/风温 → 主坐标轴
                    if slog['resampled_time'] is None:
                        continue
                    t = slog['resampled_time'] - align_time

                    if curve_name == '豆温':
                        data_vals = slog['smooth_temp1']
                    elif curve_name == '风温':
                        data_vals = slog['smooth_temp2']
                    else:
                        continue

                    if data_vals is None or len(data_vals) == 0:
                        continue

                    if filter_mask is not None:
                        t = t[filter_mask]
                        data_vals = data_vals[filter_mask]

                    line, = ax.plot(t, data_vals, color=color_hex,
                                    linestyle=CURVE_CONFIG[curve_name]['linestyle'],
                                    linewidth=CURVE_CONFIG[curve_name]['linewidth'],
                                    alpha=alpha,
                                    label=f"{slog['name']} {curve_name}" if visible else None)

                if visible:
                    all_lines.append(line)
                    all_labels.append(f"{slog['name']} {curve_name}")

            # 豆温事件标记（菱形点，与曲线同色）
            if '豆温' in active_curve_types and slog['smooth_temp1'] is not None and slog['events']:
                marker_x = []
                marker_y = []
                rt = slog['resampled_time']
                for ev in slog['events']:
                    if ev.get('type', '') in ('调整火力', '调整风门'):
                        continue
                    ev_time = ev.get('time', 0)
                    if filter_mask is not None and (ev_time < filter_ct or ev_time > filter_dt):
                        continue
                    idx = np.abs(rt - ev_time).argmin()
                    if idx < len(slog['smooth_temp1']):
                        marker_x.append(ev_time - align_time)
                        marker_y.append(slog['smooth_temp1'][idx])
                if marker_x:
                    ax.scatter(marker_x, marker_y, color=color_hex, s=50,
                              zorder=5, marker='o', alpha=alpha)

        # 设置x轴范围
        self._auto_set_xlim(ax)

        # x轴格式化为mm:ss
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: seconds_to_mmss(x)))

        # 图例（只显示可见曲线的标签）
        if all_lines:
            ax.legend(all_lines, all_labels, loc='upper right', fontsize=8)

        # 调整布局
        self.fig.tight_layout()
        # 留出标题空间
        self.fig.subplots_adjust(top=0.92)

        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_motion)
        self.canvas.mpl_connect('axes_leave_event', self._on_mouse_leave)

        self.canvas.draw()

    def _auto_set_xlim(self, ax):
        """自动设置x轴范围"""
        align_event = self.align_var.get()
        x_min = float('inf')
        x_max = float('-inf')
        has_data = False

        for slog in self.slogs:
            if slog['resampled_time'] is None:
                continue
            align_time = slog['alignment'].get(align_event, 0.0)
            shifted = slog['resampled_time'] - align_time
            if len(shifted) > 0:
                if self.exclude_outside_var.get():
                    ct = slog['alignment'].get('入豆', None)
                    dt = None
                    for ev in slog['events']:
                        if ev.get('type', '') == '烘焙结束':
                            dt = ev.get('time', None)
                            break
                    if ct is not None and dt is not None and dt > ct:
                        ct_aligned = ct - align_time
                        dt_aligned = dt - align_time
                        mask = (shifted >= ct_aligned) & (shifted <= dt_aligned)
                        if np.any(mask):
                            shifted = shifted[mask]
                        else:
                            continue
                x_min = min(x_min, shifted[0])
                x_max = max(x_max, shifted[-1])
                has_data = True

        if has_data and x_min < x_max:
            margin = (x_max - x_min) * 0.05 or 10
            ax.set_xlim(x_min - margin, x_max + margin)

    def _on_mouse_motion(self, event):
        """鼠标移动：显示光标所在位置的坐标轴值"""
        if not event.inaxes or not self.slogs:
            self.cursor_label.config(text="")
            return
        parts = [f"时间: {seconds_to_mmss(event.xdata)}"]
        try:
            # 主坐标轴温度读数
            main_val = self._main_ax.transData.inverted().transform(
                (event.x, event.y))[1]
            parts.append(f"温度: {main_val:.0f}℃")
        except Exception:
            parts.append(f"温度: {event.ydata:.0f}℃")
        # ROR轴读数
        if self._ror_ax is not None:
            try:
                inv = self._ror_ax.transData.inverted()
                ror_val = inv.transform((event.x, event.y))[1]
                parts.append(f"ROR: {ror_val:.0f}℃/min")
            except Exception:
                pass
        # 火力/风门轴读数
        if self._hf_ax is not None:
            try:
                inv = self._hf_ax.transData.inverted()
                hf_val = inv.transform((event.x, event.y))[1]
                parts.append(f"HF: {hf_val:.0f}%")
            except Exception:
                pass
        self.cursor_label.config(text=' | '.join(parts))

    def _on_mouse_leave(self, event):
        """鼠标离开图表区域"""
        self.cursor_label.config(text="")


# ====== 独立运行入口 ======

def main():
    """独立运行入口"""
    root = tk.Tk()
    root.withdraw()
    file_paths = sys.argv[1:] if len(sys.argv) > 1 else None
    app = SlogComparer(root, file_paths=file_paths)
    app.protocol("WM_DELETE_WINDOW", lambda: (root.quit(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
