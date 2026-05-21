"""
统计面板 - 温度曲线和ROR分析（嵌入版本）

功能：
1. 显示豆温(temp1full)和风温(temp2)曲线（同一坐标系）
2. 计算并显示豆温的ROR曲线（同一坐标系）
3. 支持重采样和平滑处理
4. 支持参数调整（采样间隔、平滑窗口等）
5. 鼠标追踪功能：显示精确度数
"""

import tkinter as tk
from tkinter import ttk
import numpy as np
from scipy.signal import savgol_filter
from scipy.interpolate import interp1d
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib
from matplotlib.patches import Rectangle
from matplotlib.transforms import blended_transform_factory
import warnings
import os
import json
import math
warnings.filterwarnings('ignore')

# --- ROR 非均匀 Y 轴配置 ---
ROR_COMPRESSION_FACTOR = 12.0                # 负半区压缩倍率（正半区占75%，负半区占25%）
ROR_CUSTOM_TICKS = [-120, -100, -80, -60, -40, -20,
                    0, 5, 10, 15, 20, 25, 30]  # 固定刻度位置
ROR_YLIM = (-120, 30)                        # Y 轴范围


def ror_forward(x):
    """FuncScale 正向变换：正半区不变，负半区压缩 FACTOR 倍"""
    return np.where(x >= 0, x, x / ROR_COMPRESSION_FACTOR)


def ror_inverse(x):
    """FuncScale 逆向变换：恢复原始数据值"""
    return np.where(x >= 0, x, x * ROR_COMPRESSION_FACTOR)


# 设置中文字体
def setup_chinese_font():
    """设置中文字体支持"""
    try:
        # 尝试使用系统字体
        font_paths = [
            "C:/Windows/Fonts/simhei.ttf",  # 黑体
            "C:/Windows/Fonts/msyh.ttc",    # 微软雅黑
            "C:/Windows/Fonts/simsun.ttc",  # 宋体
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                matplotlib.font_manager.fontManager.addfont(font_path)
                font_name = matplotlib.font_manager.FontProperties(fname=font_path).get_name()
                plt.rcParams['font.sans-serif'] = [font_name]
                plt.rcParams['axes.unicode_minus'] = False
                print(f"使用中文字体: {font_name}")
                return True

        # 如果找不到中文字体，使用默认设置
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun']
        plt.rcParams['axes.unicode_minus'] = False
        return True
    except Exception as e:
        print(f"设置中文字体失败: {e}")
        return False

# 初始化中文字体
setup_chinese_font()


class StatisticsPanel(ttk.Frame):
    """统计面板（嵌入版本）"""

    def __init__(self, parent, results=None):
        """
        初始化统计面板

        Args:
            parent: 父窗口
            results: 结果数据列表（可选）
        """
        super().__init__(parent)
        self.parent = parent
        self.results = results if results is not None else []

        # 配置参数
        self.sampling_interval = 1.0  # 重采样间隔（秒）
        self.smooth_window = 15       # 平滑窗口大小（秒）
        self.smooth_polyorder = 3     # 平滑多项式阶数
        self.ror_interval = 15.0      # ROR计算步长（秒）

        # 数据存储
        self.timestamps = None
        self.temp1_values = None
        self.temp2_values = None
        self.time_str_labels = None

        # 重采样后的数据
        self.resampled_time = None
        self.resampled_temp1 = None
        self.resampled_temp2 = None

        # 平滑后的数据
        self.smooth_temp1 = None
        self.smooth_temp2 = None

        # ROR数据
        self.ror_time = None
        self.ror_values = None

        # 实时更新节流
        self._update_interval = 0.25  # 处理线程的采样间隔，用于计算每1秒的帧数

        # 图表状态
        self._chart_built = False
        self._line_temp1 = None
        self._line_temp1_raw = None
        self._line_temp2 = None
        self._line_temp2_raw = None
        self._line_ror = None
        self._line_heater = None
        self._line_fan = None
        self._event_scatter = None

        # 鼠标追踪相关
        self.cursor_line = None
        self.cursor_text = None
        self.cursor_annotations = []
        self._blit_bg = None
        self._blit_draw_cid = None

        # ROR轴引用
        self.ror_axis = None
        # 事件标记数据（用于鼠标悬浮检测）
        self.event_marker_data = []
        # 主坐标轴引用（用于鼠标追踪）
        self.main_ax = None

        # 事件数据（用于.alog导出）
        self.events = []
        self._original_events = []
        self.heater_initial = 50.0
        self.fan_initial = 80.0

        # 创建UI
        self.create_ui()

        # 如果有数据，处理并绘制
        if self.results:
            self.process_data()
            self.plot_charts()

    def create_ui(self):
        """创建用户界面（仅图表 + 状态栏）"""
        # 图表框架
        chart_frame = ttk.Frame(self)
        chart_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        chart_frame.pack_propagate(False)  # 阻止FigureCanvasTkAgg收缩父容器
        self.pack_propagate(False)  # 阻止自身被收缩

        # 创建Matplotlib图形
        self.fig = Figure(figsize=(14, 8), dpi=150)
        self.canvas = FigureCanvasTkAgg(self.fig, chart_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # 初始空白图表（等待数据）
        ax = self.fig.add_subplot(111)
        ax.text(0.5, 0.5, '等待实时数据...',
               horizontalalignment='center', verticalalignment='center',
               transform=ax.transAxes, fontsize=14, color='#888888')
        ax.set_xticks([])
        ax.set_yticks([])
        self.fig.tight_layout()
        self.canvas.draw()

        # 状态栏
        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=5, pady=(0, 5))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")

        # 连接鼠标事件
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
        self.canvas.mpl_connect('axes_leave_event', self._hide_cursor_elements)

    def create_controls(self, parent, realtime_mode=False):
        """在 parent 中创建纵向排列的控制参数

        Args:
            parent: 父容器
            realtime_mode: True=仅显示"显示原曲线"checkbox，隐藏离线功能
        """
        # 控制面板
        control_frame = ttk.LabelFrame(parent, text="控制参数")
        control_frame.pack(fill="x", padx=5, pady=5)

        # ===== 参数设置（纵向） =====
        param_frame = ttk.Frame(control_frame)
        param_frame.pack(fill="x", padx=8, pady=(6, 0))

        def add_spinrow(label, var_cls, default, from_, to, increment, fmt='float'):
            row = ttk.Frame(param_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=16, anchor="w").pack(side="left")
            var = var_cls(value=default)
            w = 8 if fmt == 'float' else 6
            spin = ttk.Spinbox(row, from_=from_, to=to, increment=increment,
                               textvariable=var, width=w)
            spin.pack(side="right")
            spin.bind('<Return>', lambda e: self.recalculate())
            return var, spin

        self.interval_var, _ = add_spinrow("重采样间隔(秒):", tk.DoubleVar, self.sampling_interval, 0.1, 10.0, 0.1)
        self.window_var, _ = add_spinrow("平滑窗口(秒):", tk.IntVar, self.smooth_window, 3, 60, 1)
        self.polyorder_var, _ = add_spinrow("多项式阶数:", tk.IntVar, self.smooth_polyorder, 1, 5, 1)
        self.ror_interval_var, _ = add_spinrow("ROR步长(秒):", tk.DoubleVar, self.ror_interval, 1, 30, 1)

        # 按钮行
        btn_frame = ttk.Frame(control_frame)
        btn_frame.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(btn_frame, text="计算曲线", command=self.recalculate).pack(side="left", padx=1)
        ttk.Button(btn_frame, text="导出数据", command=self.export_data).pack(side="left", padx=1)
        ttk.Button(btn_frame, text="保存图表", command=self.save_chart).pack(side="left", padx=1)

        # ===== Checkbox 区 =====
        options_frame = ttk.Frame(control_frame)
        options_frame.pack(fill="x", padx=8, pady=(0, 6))

        self.show_raw_var = tk.BooleanVar(value=False)
        self.show_raw_checkbtn = ttk.Checkbutton(options_frame, text="显示原曲线", variable=self.show_raw_var,
                       command=self.recalculate)
        self.show_raw_checkbtn.pack(anchor="w", pady=1)

        self.show_hf_var = tk.BooleanVar(value=not realtime_mode)
        self.show_event_markers_var = tk.BooleanVar(value=not realtime_mode)
        self.show_phase_bar_var = tk.BooleanVar(value=not realtime_mode)
        self.exclude_outside_var = tk.BooleanVar(value=False)

        if not realtime_mode:
            ttk.Checkbutton(options_frame, text="显示火力/风门", variable=self.show_hf_var,
                           command=self.recalculate).pack(anchor="w", pady=1)
            ttk.Checkbutton(options_frame, text="显示事件标记", variable=self.show_event_markers_var,
                           command=self.recalculate).pack(anchor="w", pady=1)
            ttk.Checkbutton(options_frame, text="显示阶段条", variable=self.show_phase_bar_var,
                           command=self.recalculate).pack(anchor="w", pady=1)
            self.exclude_outside_var.trace_add('write', self._on_exclude_outside_changed)
            ttk.Checkbutton(options_frame, text="排除阶段外数据", variable=self.exclude_outside_var,
                           command=self.recalculate).pack(anchor="w", pady=1)

    def set_results(self, results):
        """设置结果数据并更新图表"""
        self.results = results
        if self.results:
            self.process_data()
            self.plot_charts()

    def set_update_interval(self, interval_sec):
        """设置处理线程的采样间隔，用于计算图表更新节流（ceil(1s / interval) 帧更新一次）"""
        self._update_interval = max(0.01, interval_sec)

    def append_data(self, result_dict):
        """增量追加单条结果并节流更新图表（用于实时识别）

        图表未建立前每条都尝试构建；建立后每约1秒增量更新。
        """
        self.results.append(result_dict)

        if not hasattr(self, '_live_update_count'):
            self._live_update_count = 0

        self._live_update_count += 1

        # 基于采样间隔计算每次更新的帧数：ceil(1.0 / interval)，保证 1 秒左右更新一次
        frames_per_update = max(1, math.ceil(1.0 / self._update_interval))
        threshold = 1 if not self._chart_built else frames_per_update

        if self._live_update_count >= threshold:
            self._live_update_count = 0
            self.process_data()
            if not self._chart_built:
                self._full_redraw()
            else:
                self._incremental_update()

    def set_events(self, events, heater_initial=50.0, fan_initial=80.0):
        """设置事件数据（用于.alog导出）"""
        self.events = events or []
        self._original_events = events or []
        self.heater_initial = heater_initial
        self.fan_initial = fan_initial
        # 如果已有数据，重绘图表以显示火力/风门曲线
        if hasattr(self, 'resampled_time') and self.resampled_time is not None and len(self.resampled_time) > 0:
            self.plot_charts()

    def extract_valid_data(self):
        """
        从results中提取有效数据

        Returns:
            (timestamps, temp1_values, temp2_values, time_str_labels)
        """
        timestamps = []
        temp1_values = []
        temp2_values = []
        time_str_labels = []

        for result in self.results:
            # 跳过非法数据
            if result.get('temp1_full') == '????' or result.get('temp2') == '????':
                continue

            try:
                timestamp = float(result['timestamp'])
                # 忽略计时起点之前的时间戳（负值，无效数据）
                if timestamp < 0:
                    continue
                temp1 = float(result['temp1_full'])
                temp2 = float(result['temp2'])
                time_str = result.get('time_str', '')

                timestamps.append(timestamp)
                temp1_values.append(temp1)
                temp2_values.append(temp2)
                time_str_labels.append(time_str)
            except (ValueError, KeyError):
                continue

        return (np.array(timestamps), np.array(temp1_values),
                np.array(temp2_values), time_str_labels)

    def resample_data(self, timestamps, values, sampling_interval):
        """
        等间隔重采样

        Args:
            timestamps: 原始时间戳数组
            values: 原始值数组
            sampling_interval: 采样间隔（秒）

        Returns:
            (resampled_time, resampled_values)
        """
        if len(timestamps) < 2:
            return timestamps, values

        # 创建等间隔时间序列
        start_time = np.min(timestamps)
        end_time = np.max(timestamps)
        resampled_time = np.arange(start_time, end_time + sampling_interval, sampling_interval)

        # 线性插值
        interp_func = interp1d(timestamps, values, kind='linear', bounds_error=False, fill_value='extrapolate')
        resampled_values = interp_func(resampled_time)

        return resampled_time, resampled_values

    def smooth_data(self, time, values, window_seconds, polyorder):
        """
        使用Savitzky-Golay滤波平滑数据

        右边界外推：实时场景下Savgol需要(窗口半径)个未来数据才能形成对称窗口。
        在调用Savgol之前，用最近一个窗口的数据做线性回归外推rt半径个点，
        使所有真实数据点拥有完整对称窗口，平滑后再丢弃外推部分。

        Args:
            time: 时间数组（等间隔）
            values: 值数组
            window_seconds: 窗口大小（秒）
            polyorder: 多项式阶数

        Returns:
            平滑后的值数组（与原数据等长）
        """
        if len(values) < window_seconds:
            return values

        # 计算窗口点数（假设等间隔）
        if len(time) > 1:
            dt = time[1] - time[0]
            window_points = int(window_seconds / dt)
            window_points = window_points if window_points % 2 == 1 else window_points + 1
            window_points = max(polyorder + 1, window_points)

            if window_points <= len(values):
                try:
                    radius = (window_points - 1) // 2
                    if radius > 0 and len(values) >= window_points:
                        # 用最近一个窗口的数据做线性回归外推 radius 个点
                        y_fit = values[-window_points:]
                        x_fit = np.arange(window_points)
                        coeffs = np.polyfit(x_fit, y_fit, 1)
                        x_extrap = np.arange(window_points, window_points + radius)
                        y_extrap = coeffs[0] * x_extrap + coeffs[1]
                        extended = np.concatenate([values, y_extrap])
                    else:
                        extended = values

                    smoothed = savgol_filter(extended, window_points, polyorder)
                    return smoothed[:len(values)]
                except:
                    pass

        return values

    def compute_ror(self, time, temperature, sampling_interval, ror_interval):
        """
        计算ROR（升温速率）

        采用后向窗口：第t秒的ROR = 从第(t-ror_interval)秒到第t秒的平均升温速率。
        例如 ror_interval=10 时，第10秒的ROR = (T[10] - T[0]) / 10 * 60。

        Args:
            time: 时间数组（等间隔）
            temperature: 温度数组（平滑后）
            sampling_interval: 采样间隔（秒）
            ror_interval: ROR计算窗口宽度（秒）

        Returns:
            (ror_time, ror_values) - ROR时间点和值（℃/min）
        """
        if len(temperature) < 2:
            return np.array([]), np.array([])

        # 窗口对应的点数
        step = max(1, int(round(ror_interval / sampling_interval)))
        if step >= len(temperature):
            return np.array([]), np.array([])

        dt = step * sampling_interval  # 窗口宽度（秒）
        # 后向差分：T[i] - T[i-step]
        dT = temperature[step:] - temperature[:-step]
        ror_values = (dT / dt) * 60.0  # 转换为℃/min

        # 时间对齐到窗口尾部：第t秒的ROR代表[t-step, t]区间
        ror_time = time[step:]

        return ror_time, ror_values

    def build_heater_fan_data(self):
        """
        从事件数据构建火力和风门的时间序列

        Returns:
            (heater, fan) 与 resampled_time 等长的 numpy 数组，或 (None, None)
        """
        if self.resampled_time is None or len(self.resampled_time) == 0:
            return None, None

        heater = np.full_like(self.resampled_time, float(self.heater_initial))
        fan = np.full_like(self.resampled_time, float(self.fan_initial))

        # 按时间排序事件
        sorted_events = sorted(self.events, key=lambda x: x.get('time', 0))

        for ev in sorted_events:
            ev_time = ev.get('time', 0)
            ev_type = ev.get('type', '')
            ev_value = ev.get('value')
            if ev_value is None:
                continue

            # 找到该事件生效的索引位置
            idx = np.searchsorted(self.resampled_time, ev_time)
            if idx >= len(self.resampled_time):
                continue

            if ev_type == '调整火力':
                heater[idx:] = float(ev_value)
            elif ev_type == '调整风门':
                fan[idx:] = float(ev_value)

        return heater, fan

    def get_event_markers(self):
        """
        获取非火力/风门的事件标记

        Returns:
            [(x_time, y_temp, event_name), ...] 列表
        """
        markers = []
        if self.resampled_time is None or self.smooth_temp1 is None:
            return markers

        for ev in self.events:
            ev_type = ev.get('type', '')
            # 排除火力/风门调整事件
            if ev_type in ('调整火力', '调整风门'):
                continue
            ev_time = ev.get('time', 0)
            # 在豆温曲线上找到对应温度
            idx = np.abs(self.resampled_time - ev_time).argmin()
            if idx < len(self.smooth_temp1):
                temp = self.smooth_temp1[idx]
                markers.append((ev_time, temp, ev_type))

        return markers

    def find_phase_boundaries(self):
        """
        计算各阶段分界点

        三段划分：
          1. 入豆 → 豆温第一次超过150°C（入豆后先降后升）
          2. 第一段结束 → 一爆开始
          3. 一爆开始 → 烘焙结束

        Returns:
            (t_charge, t_p1, t_fcs, t_drop) 各时间点(秒)，或 None
        """
        # 提取关键事件时间
        charge_time = None
        fcs_time = None   # 一爆开始 First Crack Start
        drop_time = None  # 烘焙结束

        for ev in self.events:
            ev_type = ev.get('type', '')
            ev_time = ev.get('time', 0)
            if ev_type == '入豆':
                charge_time = ev_time
            elif ev_type == '一爆开始':
                fcs_time = ev_time
            elif ev_type == '烘焙结束':
                drop_time = ev_time

        if charge_time is None or drop_time is None:
            return None

        # 第一段结束：入豆后，豆温第一次超过150°C（先降后升）
        phase1_end_time = None
        if self.resampled_time is not None and self.smooth_temp1 is not None:
            charge_idx = np.searchsorted(self.resampled_time, charge_time)
            if charge_idx < len(self.resampled_time):
                below_150 = False
                for i in range(charge_idx, len(self.resampled_time)):
                    if not below_150 and self.smooth_temp1[i] < 150:
                        below_150 = True
                    elif below_150 and self.smooth_temp1[i] >= 150:
                        phase1_end_time = self.resampled_time[i]
                        break

        if phase1_end_time is None:
            # 兜底：使用一爆时间或入豆后60秒
            if fcs_time is not None:
                phase1_end_time = fcs_time
            else:
                phase1_end_time = charge_time + 60

        return (charge_time, phase1_end_time, fcs_time, drop_time)

    def process_data(self):
        """处理数据：提取、重采样、平滑、计算ROR"""
        # 提取有效数据
        self.timestamps, self.temp1_values, self.temp2_values, self.time_str_labels = \
            self.extract_valid_data()

        if len(self.timestamps) < 2:
            self.status_var.set("错误：有效数据不足")
            return

        # 更新参数
        self.sampling_interval = self.interval_var.get()
        self.smooth_window = self.window_var.get()
        self.smooth_polyorder = self.polyorder_var.get()
        self.ror_interval = self.ror_interval_var.get()

        # 重采样
        self.resampled_time, self.resampled_temp1 = self.resample_data(
            self.timestamps, self.temp1_values, self.sampling_interval)
        _, self.resampled_temp2 = self.resample_data(
            self.timestamps, self.temp2_values, self.sampling_interval)

        # 平滑
        self.smooth_temp1 = self.smooth_data(
            self.resampled_time, self.resampled_temp1,
            self.smooth_window, self.smooth_polyorder)
        self.smooth_temp2 = self.smooth_data(
            self.resampled_time, self.resampled_temp2,
            self.smooth_window, self.smooth_polyorder)

        # 计算ROR
        self.ror_time, self.ror_values = self.compute_ror(
            self.resampled_time, self.smooth_temp1, self.sampling_interval, self.ror_interval)

        # 重置 events 为原始版本（每次重算时还原，确保 toggle 无累积偏移）
        self.events = list(self._original_events)

        # 排除阶段外数据：过滤并重基（从 plot_charts 移入，保持数据与视图分离）
        if hasattr(self, 'exclude_outside_var') and self.exclude_outside_var.get():
            charge_t = None
            drop_t = None
            for ev in self.events:
                if ev.get('type', '') == '入豆':
                    charge_t = ev.get('time', 0.0)
                elif ev.get('type', '') == '烘焙结束':
                    drop_t = ev.get('time', 0.0)
            if charge_t is not None and drop_t is not None and drop_t > charge_t:
                mask = (self.resampled_time >= charge_t) & (self.resampled_time <= drop_t)
                self.resampled_time = self.resampled_time[mask] - charge_t
                self.smooth_temp1 = self.smooth_temp1[mask]
                self.smooth_temp2 = self.smooth_temp2[mask]
                if self.ror_time is not None and len(self.ror_time) > 0:
                    ror_mask = (self.ror_time >= charge_t) & (self.ror_time <= drop_t)
                    if np.any(ror_mask):
                        self.ror_time = self.ror_time[ror_mask] - charge_t
                        self.ror_values = self.ror_values[ror_mask]
                self.events = [{**ev, 'time': ev.get('time', 0) - charge_t} for ev in self._original_events]
                t_min = self.resampled_time[0]
                t_max = self.resampled_time[-1]
                for ev in self.events:
                    ev['time'] = max(t_min, min(ev['time'], t_max))

        self.status_var.set(f"处理完成：{len(self.timestamps)}个有效点，{len(self.resampled_time)}个重采样点")

    # ── 图表绘制（全量 / 增量） ──

    def plot_charts(self):
        """绘制图表 — 全量重建（向后兼容，用于手动 recalculate）"""
        self._full_redraw()

    def _full_redraw(self):
        """全量重建图表：清空 figure，重新创建所有 artist"""
        self.fig.clear()
        self._chart_built = False

        if len(self.timestamps) < 2:
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, '数据不足，无法绘制图表',
                   horizontalalignment='center', verticalalignment='center',
                   transform=ax.transAxes, fontsize=14)
            self._blit_bg = None
            self.canvas.draw()
            return

        # 创建单个子图
        ax = self.fig.add_subplot(111)
        self.main_ax = ax

        temp1_color = 'tab:blue'
        temp2_color = 'tab:orange'
        ror_color = 'tab:red'

        # 1 & 2. 创建 line 对象并保存引用
        self._line_temp1, = ax.plot([], [], color=temp1_color, linewidth=2, label='豆温（平滑）')
        self._line_temp1.set_data(self.resampled_time, self.smooth_temp1)

        self._line_temp1_raw = None
        if self.show_raw_var.get():
            self._line_temp1_raw, = ax.plot([], [], color=temp1_color, linewidth=0.8, alpha=0.25, label='豆温（原始）')
            self._line_temp1_raw.set_data(self.timestamps, self.temp1_values)

        self._line_temp2, = ax.plot([], [], color=temp2_color, linewidth=2, label='风温（平滑）')
        self._line_temp2.set_data(self.resampled_time, self.smooth_temp2)

        self._line_temp2_raw = None
        if self.show_raw_var.get():
            self._line_temp2_raw, = ax.plot([], [], color='darkorange', linewidth=0.8, alpha=0.25, label='风温（原始）')
            self._line_temp2_raw.set_data(self.timestamps, self.temp2_values)

        # 3. ROR 曲线
        self.ror_axis = None
        self._line_ror = None
        if len(self.ror_values) > 0:
            self.ror_axis = ax.twinx()
            self._line_ror, = self.ror_axis.plot([], [], color=ror_color, linewidth=2, label='ROR')
            self._line_ror.set_data(self.ror_time, self.ror_values)
            self.ror_axis.set_ylabel('ROR (℃/min)', color=ror_color)
            self.ror_axis.set_yscale('function', functions=(ror_forward, ror_inverse))
            self.ror_axis.set_ylim(*ROR_YLIM)
            self.ror_axis.yaxis.set_major_locator(matplotlib.ticker.FixedLocator(ROR_CUSTOM_TICKS))
            self.ror_axis.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
            self.ror_axis.tick_params(axis='y', labelcolor=ror_color)
            self.ror_axis.axhline(y=0, color='gray', linestyle='--', alpha=0.5)

        # 4. 火力/风门曲线
        self.hf_axis = None
        self._line_heater = None
        self._line_fan = None
        heater_data, fan_data = None, None
        if self.show_hf_var.get():
            heater_data, fan_data = self.build_heater_fan_data()

        if heater_data is not None and fan_data is not None:
            self.hf_axis = ax.twinx()
            hf_pos = 1.05 if self.ror_axis is not None else 1.00
            self.hf_axis.spines['right'].set_position(('axes', hf_pos))
            self._line_heater, = self.hf_axis.plot([], [], color='green', linewidth=2, linestyle='-', label='火力')
            self._line_fan, = self.hf_axis.plot([], [], color='purple', linewidth=2, linestyle='--', label='风门')
            self._line_heater.set_data(self.resampled_time, heater_data)
            self._line_fan.set_data(self.resampled_time, fan_data)
            self.hf_axis.set_ylabel('火力/风门 (%)', color='green')
            self.hf_axis.set_ylim(0, 200)
            self.hf_axis.tick_params(axis='y', labelcolor='green')

        # 构建图例 line 列表
        all_lines = [self._line_temp1, self._line_temp2]
        if self._line_ror is not None:
            all_lines.append(self._line_ror)
        if self._line_temp1_raw is not None:
            all_lines.append(self._line_temp1_raw)
        if self._line_temp2_raw is not None:
            all_lines.append(self._line_temp2_raw)
        if self._line_heater is not None:
            all_lines.extend([self._line_heater, self._line_fan])

        # X 轴刻度：固定每30秒一个标点
        ax.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(30))
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
            lambda s, _: f"{int(s // 60):02d}:{int(s % 60):02d}"))
        ax.tick_params(axis='x', rotation=45)

        ax.set_xlabel('时间 (mm:ss)')
        ax.set_ylabel('温度 (℃)', color=temp1_color)
        ax.tick_params(axis='y', labelcolor=temp1_color)
        ax.grid(True, alpha=0.3)
        ax.set_title('温度曲线和ROR分析', y=1.18)
        ax.set_ylim(100, 230)
        ax.set_xlim(0, 480)
        ax.legend(all_lines, [l.get_label() for l in all_lines], loc='upper right')

        # 5. 事件标记
        self._event_scatter = None
        self.event_marker_data = []
        if self.show_event_markers_var.get():
            markers = self.get_event_markers()
            if markers:
                xs = [m[0] for m in markers]
                ys = [m[1] for m in markers]
                self._event_scatter = ax.scatter(xs, ys, color='black', s=50, zorder=5, marker='o')
                self.event_marker_data = markers

        # 6. 阶段条
        self._draw_phase_bar(ax)

        self.fig.tight_layout()
        adj = {}
        if self.show_phase_bar_var.get():
            fig_h = self.fig.get_figheight()
            adj['top'] = max(0.70, 1.0 - 1.4 / fig_h)
        if adj:
            self.fig.subplots_adjust(**adj)

        self._create_cursor_artists(ax)
        self._setup_blit()
        self._chart_built = True

    def _incremental_update(self):
        """增量更新：仅更新 line 数据 + 轴范围 + 刻度，不重建 figure 结构"""
        if not getattr(self, '_chart_built', False) or self.main_ax is None:
            self._full_redraw()
            return

        # 如果 ROR/HF 数据首次出现（之前的全量构建没创建对应 axis），回退全量重建
        if (self._line_ror is None and len(self.ror_values) > 0) or \
           (self._line_heater is None and self.show_hf_var.get()):
            self._full_redraw()
            return

        if len(self.timestamps) < 2:
            return

        ax = self.main_ax

        # 更新 line 数据
        self._line_temp1.set_data(self.resampled_time, self.smooth_temp1)
        self._line_temp2.set_data(self.resampled_time, self.smooth_temp2)
        if self._line_temp1_raw is not None:
            self._line_temp1_raw.set_data(self.timestamps, self.temp1_values)
        if self._line_temp2_raw is not None:
            self._line_temp2_raw.set_data(self.timestamps, self.temp2_values)
        if self._line_ror is not None:
            self._line_ror.set_data(self.ror_time, self.ror_values)
        if self._line_heater is not None:
            h_data, f_data = self.build_heater_fan_data()
            if h_data is not None:
                self._line_heater.set_data(self.resampled_time, h_data)
                self._line_fan.set_data(self.resampled_time, f_data)

        # X 轴刻度由 MultipleLocator(30) 自动管理，无需手动更新

        # 更新轴范围：只扩展不收缩，避免坐标轴抖动
        ax.relim()
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        dl = ax.dataLim
        ax.set_xlim(min(xlim[0], dl.x0), max(xlim[1], dl.x1))
        ax.set_ylim(min(ylim[0], dl.y0), max(ylim[1], dl.y1))
        if self.ror_axis is not None:
            self.ror_axis.relim()
            self.ror_axis.autoscale_view(scalex=True, scaley=False)

        # 清除旧的阶段条 + 事件标记 + 图例，重绘
        self._clear_decorations(ax)
        self._draw_phase_bar(ax)
        if self.show_event_markers_var.get():
            markers = self.get_event_markers()
            if markers:
                xs = [m[0] for m in markers]
                ys = [m[1] for m in markers]
                self._event_scatter = ax.scatter(xs, ys, color='black', s=50, zorder=5, marker='o')
                self.event_marker_data = markers

        self._blit_bg = None  # 失效 blit 缓存
        self.canvas.draw()

    def _clear_decorations(self, ax):
        """清除阶段条、事件标记、图例等可重新生成的装饰元素"""
        for p in list(ax.patches):
            p.remove()
        for t in list(ax.texts):
            if t not in (self.cursor_info,):
                t.remove()
        for c in list(ax.lines):
            if c not in (self._line_temp1, self._line_temp2,
                         self._line_temp1_raw, self._line_temp2_raw,
                         self.cursor_line):
                c.remove()
        # 清除旧的 scatter
        for coll in list(ax.collections):
            coll.remove()
        if ax.get_legend() is not None:
            ax.get_legend().remove()
        # 更新图例
        all_lines = [self._line_temp1, self._line_temp2]
        if self._line_ror is not None:
            all_lines.append(self._line_ror)
        if self._line_temp1_raw is not None:
            all_lines.append(self._line_temp1_raw)
        if self._line_temp2_raw is not None:
            all_lines.append(self._line_temp2_raw)
        if self._line_heater is not None:
            all_lines.extend([self._line_heater, self._line_fan])
        ax.legend(all_lines, [l.get_label() for l in all_lines], loc='upper right')

    def _draw_phase_bar(self, ax):
        """绘制阶段划分条（从 _full_redraw 提取，增量时复用）"""
        if not self.show_phase_bar_var.get():
            return
        bounds = self.find_phase_boundaries()
        if bounds is None:
            return
        t_charge, t_p1, t_fcs, t_drop = bounds
        bar_x_start = t_charge
        bar_x_end = t_drop
        bar_width = bar_x_end - bar_x_start
        if bar_width <= 0:
            return

        p1_dur = t_p1 - t_charge
        p2_dur = (t_fcs - t_p1) if t_fcs is not None else 0
        p3_dur = t_drop - (t_fcs if t_fcs is not None else t_p1)
        p1_pct = p1_dur / bar_width * 100
        p2_pct = p2_dur / bar_width * 100
        p3_pct = 100 - p1_pct - p2_pct

        def _temp_at(t):
            if self.resampled_time is None or self.smooth_temp1 is None or t is None:
                return None
            idx = np.searchsorted(self.resampled_time, t)
            if idx < len(self.smooth_temp1):
                return float(self.smooth_temp1[idx])
            return None

        def _phase_avg_ror(start_t, end_t):
            if start_t is None or end_t is None or end_t <= start_t:
                return None
            t_start = _temp_at(start_t)
            t_end = _temp_at(end_t)
            if t_start is None or t_end is None:
                return None
            dur_min = (end_t - start_t) / 60
            if dur_min <= 0:
                return None
            return (t_end - t_start) / dur_min

        revert_time = t_charge
        if self.resampled_time is not None and self.smooth_temp1 is not None:
            ci = np.searchsorted(self.resampled_time, t_charge)
            pi = np.searchsorted(self.resampled_time, t_p1)
            if ci < pi < len(self.smooth_temp1):
                seg_min_idx = ci + np.argmin(self.smooth_temp1[ci:pi])
                revert_time = self.resampled_time[seg_min_idx]

        p1_avg_ror = _phase_avg_ror(revert_time, t_p1)
        p2_avg_ror = _phase_avg_ror(t_p1, t_fcs) if t_fcs else None
        p3_avg_ror = _phase_avg_ror(t_fcs if t_fcs is not None else t_p1, t_drop)
        t_fcs_val = _temp_at(t_fcs) if t_fcs else None
        t_drop_val = _temp_at(t_drop)
        p3_dt = (t_drop_val - t_fcs_val) if (t_fcs_val is not None and t_drop_val is not None) else None

        p1_label = f"脱水期\n{int(p1_dur//60)}:{int(p1_dur%60):02d} ({p1_pct:.0f}%)"
        if p1_avg_ror is not None:
            p1_label += f"\n升温段ROR:{p1_avg_ror:.1f}"

        p2_label = ""
        if t_fcs:
            p2_label = f"美拉德期\n{int(p2_dur//60)}:{int(p2_dur%60):02d} ({p2_pct:.0f}%)"
            if p2_avg_ror is not None:
                p2_label += f"\n平均ROR:{p2_avg_ror:.1f}"

        p3_label = f"发展期\n{int(p3_dur//60)}:{int(p3_dur%60):02d} ({p3_pct:.0f}%)"
        p3_extra = []
        if p3_dt is not None:
            p3_extra.append(f"ΔT:{p3_dt:.1f}℃")
        if p3_avg_ror is not None:
            p3_extra.append(f"ROR:{p3_avg_ror:.1f}")
        if p3_extra:
            p3_label += "\n" + " ".join(p3_extra)

        segments = [
            (t_charge, t_p1, '#81C784', p1_label),
            (t_p1, t_fcs if t_fcs is not None else t_drop, '#FFD54F', p2_label),
            (t_fcs if t_fcs is not None else t_drop, t_drop, '#A0522D', p3_label),
        ]

        bar_bottom = 1.02
        bar_height = 0.09
        transform = blended_transform_factory(ax.transData, ax.transAxes)

        bg_panel = Rectangle(
            (bar_x_start, bar_bottom - 0.01), bar_width, bar_height + 0.02,
            facecolor='#E8E8E8', edgecolor='#CCCCCC', linewidth=0.5,
            transform=transform, zorder=0, clip_on=False
        )
        ax.add_patch(bg_panel)

        for seg_start, seg_end, color, label in segments:
            if seg_end is None or seg_end <= seg_start:
                continue
            rect = Rectangle((seg_start, bar_bottom), seg_end - seg_start, bar_height,
                           facecolor=color, alpha=0.85, edgecolor='none',
                           transform=transform, zorder=1, clip_on=False)
            ax.add_patch(rect)
            mid = (seg_start + seg_end) / 2
            ax.text(mid, bar_bottom + bar_height / 2, label,
                   transform=blended_transform_factory(ax.transData, ax.transAxes),
                   ha='center', va='center', fontsize=6.5, fontweight='bold')

        for boundary in [t_p1]:
            if t_charge < boundary < t_drop:
                ax.axvline(x=boundary, color='lightyellow', linestyle='--', linewidth=2, alpha=0.8)
        if t_fcs is not None and t_charge < t_fcs < t_drop:
            ax.axvline(x=t_fcs, color='lightyellow', linestyle='--', linewidth=2, alpha=0.8)

    def _create_cursor_artists(self, ax):
        """创建可复用的鼠标追踪元素（每次重绘图表后调用）"""
        # 清理旧元素
        for art in [self.cursor_line, self.cursor_info] if hasattr(self, 'cursor_info') else [self.cursor_line]:
            if art is not None:
                try:
                    art.remove()
                except Exception:
                    pass

        # 垂直线 — 一直复用
        self.cursor_line = ax.axvline(
            x=0, color='gray', linestyle='--', alpha=0.7, linewidth=1,
            visible=False, zorder=20
        )
        self.cursor_line.set_animated(True)

        # 固定信息框 — 左上角，显示时间/豆温/风温/ROR等
        self.cursor_info = ax.text(
            0.02, 0.98, '',
            transform=ax.transAxes,
            verticalalignment='top', horizontalalignment='left',
            fontsize=9,
            visible=False, zorder=20, clip_on=False,
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='#CCCCCC')
        )
        self.cursor_info.set_animated(True)

    def _hide_cursor_elements(self, event=None):
        """隐藏鼠标追踪元素（复用对象隐藏）"""
        for art in [self.cursor_line, self.cursor_info]:
            if art is not None:
                try:
                    art.set_visible(False)
                except Exception:
                    pass

    def _on_blit_draw(self, event):
        """draw_event回调：保存背景用于blit"""
        self._blit_bg = self.canvas.copy_from_bbox(self.fig.bbox)

    def _setup_blit(self):
        """初始化blit并捕获初始背景"""
        if self._blit_draw_cid:
            try:
                self.canvas.mpl_disconnect(self._blit_draw_cid)
            except Exception:
                pass
        self._blit_draw_cid = self.canvas.mpl_connect('draw_event', self._on_blit_draw)
        # 用tkinter <Configure>监听canvas尺寸变化（比mpl resize_event更可靠）
        self.canvas.get_tk_widget().bind('<Configure>', self._on_canvas_resize, add='+')
        self._blit_bg = None
        # 全量绘制（触发draw_event自动保存背景）
        self.canvas.draw()

    def _on_canvas_resize(self, event):
        """tkinter画布尺寸变化回调：按当前尺寸重新布局+失效blit缓存"""
        self._blit_bg = None
        try:
            self.fig.tight_layout()
            # 重新计算布局参数（用当前图表高度，而非旧值）
            adj = {}
            if self.show_phase_bar_var.get():
                fig_h = self.fig.get_figheight()
                adj['top'] = max(0.70, 1.0 - 1.4 / fig_h)
            if adj:
                self.fig.subplots_adjust(**adj)
            self.canvas.draw_idle()
        except Exception:
            pass

    def on_mouse_move(self, event):
        """鼠标移动事件处理"""
        if not event.inaxes:
            return
        if self.cursor_line is None or self.cursor_info is None:
            return

        ax = event.inaxes
        xdata = event.xdata

        # 1. 垂直线 — 复用，只更新x坐标
        self.cursor_line.set_xdata([xdata, xdata])
        self.cursor_line.set_visible(True)

        # 2. 构建固定信息框文字（不跟随鼠标）
        time_str = self.format_time(xdata)
        info_parts = [f"时间: {time_str}"]

        if self.resampled_time is not None and len(self.resampled_time) > 0:
            idx = np.abs(self.resampled_time - xdata).argmin()

            if self.smooth_temp1 is not None and idx < len(self.smooth_temp1):
                info_parts.append(f"豆温: {self.smooth_temp1[idx]:.1f}℃")

            if self.smooth_temp2 is not None and idx < len(self.smooth_temp2):
                info_parts.append(f"风温: {self.smooth_temp2[idx]:.1f}℃")

            if self.ror_time is not None and len(self.ror_time) > 0 and self.ror_axis is not None:
                ror_idx = np.abs(self.ror_time - xdata).argmin()
                if ror_idx < len(self.ror_values):
                    info_parts.append(f"ROR: {self.ror_values[ror_idx]:.1f}℃/min")

            if self.hf_axis is not None and self.show_hf_var.get():
                h_data, f_data = self.build_heater_fan_data()
                if h_data is not None and idx < len(h_data):
                    info_parts.append(f"火力: {h_data[idx]:.0f}%  风门: {f_data[idx]:.0f}%")

        self.cursor_info.set_text('\n'.join(info_parts))
        self.cursor_info.set_visible(True)

        # 3. Blit — 只画垂直线和固定信息框
        cur_ax = self.main_ax if self.main_ax is not None else ax
        if self._blit_bg is not None:
            self.canvas.restore_region(self._blit_bg)
            cur_ax.draw_artist(self.cursor_line)
            cur_ax.draw_artist(self.cursor_info)
            self.canvas.blit(self.fig.bbox)
        else:
            self.canvas.draw_idle()

    def on_mouse_leave(self, event):
        """鼠标离开图表区域事件处理"""
        self._hide_cursor_elements()
        if self._blit_bg is not None:
            self.canvas.restore_region(self._blit_bg)
            self.canvas.blit(self.fig.bbox)
        else:
            self.canvas.draw_idle()

    def format_time(self, seconds):
        """格式化时间为mm:ss格式"""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{minutes:02d}:{secs:02d}.{millis:03d}"

    def _on_exclude_outside_changed(self, *args):
        """排除阶段外数据勾选时，自动取消并禁用显示原曲线"""
        if self.exclude_outside_var.get():
            self.show_raw_var.set(False)
            self.show_raw_checkbtn.configure(state="disabled")
        else:
            self.show_raw_checkbtn.configure(state="normal")

    def recalculate(self, event=None):
        """重新计算并更新图表"""
        self.process_data()
        self.plot_charts()

    def export_data(self):
        """导出数据为.slog格式（JSON，包含results和events）"""
        if not self.results:
            from tkinter import messagebox
            messagebox.showwarning("警告", "没有可导出的数据")
            return

        from tkinter import filedialog

        # 构建导出数据
        export = {
            'version': 1,
            'results': self.results,
            'events': self.events,
            'heater_initial': self.heater_initial,
            'fan_initial': self.fan_initial,
        }

        file_path = filedialog.asksaveasfilename(
            defaultextension=".slog",
            filetypes=[("Slog files", "*.slog"), ("All files", "*.*")]
        )

        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(export, f, indent=2, ensure_ascii=False)
            self.status_var.set(f"数据已导出到: {file_path}")


    def save_chart(self):
        """保存图表为图片"""
        from tkinter import filedialog

        file_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG files", "*.png"), ("PDF files", "*.pdf"), ("All files", "*.*")]
        )

        if file_path:
            self.fig.savefig(file_path, dpi=300, bbox_inches='tight')
            self.status_var.set(f"图表已保存到: {file_path}")


if __name__ == "__main__":
    # 测试代码
    root = tk.Tk()
    root.title("统计面板测试")
    root.geometry("1200x800")

    # 创建测试数据
    test_results = []
    for i in range(100):
        timestamp = i * 0.5  # 每0.5秒一个点
        temp1 = 170 + i * 0.1 + np.random.normal(0, 0.05)
        temp2 = 180 + i * 0.05 + np.random.normal(0, 0.03)

        result = {
            'frame': i,
            'timestamp': timestamp,
            'time_str': f"{int(timestamp//60):02d}:{int(timestamp%60):02d}:{int((timestamp%1)*1000):03d}",
            'timer': '00:00:00',
            'temp1_full': f"{temp1:.1f}",
            'temp1_normal': '123',
            'temp1_faulty_digit': 0,
            'temp2': f"{temp2:.1f}"
        }
        test_results.append(result)

    # 添加一些非法数据
    for i in range(5):
        result = {
            'frame': 100 + i,
            'timestamp': 50 + i * 0.5,
            'time_str': f"{int((50+i*0.5)//60):02d}:{int((50+i*0.5)%60):02d}:{int(((50+i*0.5)%1)*1000):03d}",
            'timer': '00:00:00',
            'temp1_full': '????',
            'temp1_normal': '????',
            'temp1_faulty_digit': -1,
            'temp2': '????'
        }
        test_results.append(result)

    panel = StatisticsPanel(root, test_results)
    panel.pack(fill="both", expand=True)

    root.mainloop()