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

        # 创建Matplotlib图形
        self.fig = Figure(figsize=(14, 8), dpi=150)
        self.canvas = FigureCanvasTkAgg(self.fig, chart_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # 状态栏
        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=5, pady=(0, 5))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")

        # 连接鼠标事件
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
        self.canvas.mpl_connect('axes_leave_event', self._hide_cursor_elements)

    def create_controls(self, parent):
        """在 parent 中创建纵向排列的控制参数"""
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

        self.show_hf_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="显示火力/风门", variable=self.show_hf_var,
                       command=self.recalculate).pack(anchor="w", pady=1)

        self.show_event_markers_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="显示事件标记", variable=self.show_event_markers_var,
                       command=self.recalculate).pack(anchor="w", pady=1)

        self.show_phase_bar_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="显示阶段条", variable=self.show_phase_bar_var,
                       command=self.recalculate).pack(anchor="w", pady=1)

        self.exclude_outside_var = tk.BooleanVar(value=False)
        self.exclude_outside_var.trace_add('write', self._on_exclude_outside_changed)
        ttk.Checkbutton(options_frame, text="排除阶段外数据", variable=self.exclude_outside_var,
                       command=self.recalculate).pack(anchor="w", pady=1)

    def set_results(self, results):
        """设置结果数据并更新图表"""
        self.results = results
        if self.results:
            self.process_data()
            self.plot_charts()

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

        Args:
            time: 时间数组（等间隔）
            values: 值数组
            window_seconds: 窗口大小（秒）
            polyorder: 多项式阶数

        Returns:
            平滑后的值数组
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
                    smoothed = savgol_filter(values, window_points, polyorder)
                    return smoothed
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

        self.status_var.set(f"处理完成：{len(self.timestamps)}个有效点，{len(self.resampled_time)}个重采样点")

    def plot_charts(self):
        """绘制图表（三个曲线在同一坐标系）"""
        self.fig.clear()
        if len(self.timestamps) < 2:
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, '数据不足，无法绘制图表',
                   horizontalalignment='center', verticalalignment='center',
                   transform=ax.transAxes, fontsize=14)
            self._blit_bg = None
            self.canvas.draw()
            return

        # 排除阶段外数据：过滤并重基，使入豆位于 x=0
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
                self.resampled_time = self.resampled_time[mask] - charge_t  # 重基
                self.smooth_temp1 = self.smooth_temp1[mask]
                self.smooth_temp2 = self.smooth_temp2[mask]
                if self.ror_time is not None and len(self.ror_time) > 0:
                    ror_mask = (self.ror_time >= charge_t) & (self.ror_time <= drop_t)
                    if np.any(ror_mask):
                        self.ror_time = self.ror_time[ror_mask] - charge_t  # 重基
                        self.ror_values = self.ror_values[ror_mask]
                # 重基 events — 从原始 events 重基，避免多次 toggle 累积偏移
                self.events = [{**ev, 'time': ev.get('time', 0) - charge_t} for ev in self._original_events]
                # 确保 events 时间在数据范围内，使过滤后的数据集自洽
                t_min = self.resampled_time[0]
                t_max = self.resampled_time[-1]
                for ev in self.events:
                    ev['time'] = max(t_min, min(ev['time'], t_max))

        # 创建单个子图
        ax = self.fig.add_subplot(111)
        self.main_ax = ax  # 保存主坐标轴引用

        # 设置颜色
        temp1_color = 'tab:blue'
        temp2_color = 'tab:orange'
        ror_color = 'tab:red'

        # 转换时间为mm:ss格式用于横坐标
        def seconds_to_mmss(seconds):
            """将秒转换为mm:ss格式"""
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes:02d}:{secs:02d}"

        # 1. 绘制豆温曲线（平滑）
        line1, = ax.plot(self.resampled_time, self.smooth_temp1,
                        color=temp1_color, linewidth=2, label='豆温（平滑）')
        line1_raw = None
        if self.show_raw_var.get():
            line1_raw, = ax.plot(self.timestamps, self.temp1_values,
                               color=temp1_color, linewidth=0.8, alpha=0.25, label='豆温（原始）')

        # 2. 绘制风温曲线（平滑）
        line2, = ax.plot(self.resampled_time, self.smooth_temp2,
                        color=temp2_color, linewidth=2, label='风温（平滑）')
        line2_raw = None
        if self.show_raw_var.get():
            line2_raw, = ax.plot(self.timestamps, self.temp2_values,
                               color='darkorange', linewidth=0.8, alpha=0.25, label='风温（原始）')

        # 3. 绘制ROR曲线（如果有数据）
        self.ror_axis = None  # 存储ROR轴的引用
        if len(self.ror_values) > 0:
            # 创建第二个Y轴用于ROR
            self.ror_axis = ax.twinx()
            line3, = self.ror_axis.plot(self.ror_time, self.ror_values,
                            color=ror_color, linewidth=2, label='ROR')
            self.ror_axis.set_ylabel('ROR (℃/min)', color=ror_color)

            # 非均匀 Y 轴：展开正半区（0~30），压缩负半区（0~-120）
            self.ror_axis.set_yscale('function', functions=(ror_forward, ror_inverse))
            self.ror_axis.set_ylim(*ROR_YLIM)
            self.ror_axis.yaxis.set_major_locator(
                matplotlib.ticker.FixedLocator(ROR_CUSTOM_TICKS))
            self.ror_axis.yaxis.set_minor_locator(
                matplotlib.ticker.NullLocator())

            self.ror_axis.tick_params(axis='y', labelcolor=ror_color)
            self.ror_axis.axhline(y=0, color='gray', linestyle='--', alpha=0.5)

            # 合并图例
            lines = [line1, line2, line3]
            labels = [line.get_label() for line in lines]
            if line1_raw is not None:
                lines.append(line1_raw)
                labels.append(line1_raw.get_label())
            if line2_raw is not None:
                lines.append(line2_raw)
                labels.append(line2_raw.get_label())
        else:
            lines = [line1, line2]
            labels = [line.get_label() for line in lines]
            if line1_raw is not None:
                lines.append(line1_raw)
                labels.append(line1_raw.get_label())
            if line2_raw is not None:
                lines.append(line2_raw)
                labels.append(line2_raw.get_label())

        # 4. 绘制火力/风门曲线（第三条Y轴）
        self.hf_axis = None
        heater_data = None
        fan_data = None
        if self.show_hf_var.get():
            heater_data, fan_data = self.build_heater_fan_data()

        if heater_data is not None and fan_data is not None:
            self.hf_axis = ax.twinx()
            # 有ROR轴时偏移到右侧，无ROR轴时保持默认位置
            hf_pos = 1.05 if self.ror_axis is not None else 1.00
            self.hf_axis.spines['right'].set_position(('axes', hf_pos))

            line4, = self.hf_axis.plot(self.resampled_time, heater_data,
                                       color='green', linewidth=2, linestyle='-', label='火力')
            line5, = self.hf_axis.plot(self.resampled_time, fan_data,
                                       color='purple', linewidth=2, linestyle='--', label='风门')

            self.hf_axis.set_ylabel('火力/风门 (%)', color='green')
            self.hf_axis.set_ylim(0, 200)
            self.hf_axis.tick_params(axis='y', labelcolor='green')

            # 添加到图例
            lines.extend([line4, line5])
            labels.extend(['火力', '风门'])

        # 设置横坐标为mm:ss格式
        if len(self.resampled_time) > 0:
            # 选择一些刻度位置
            time_range = self.resampled_time[-1] - self.resampled_time[0]
            if time_range > 0:
                num_ticks = min(10, len(self.resampled_time))
                tick_indices = np.linspace(0, len(self.resampled_time)-1, num_ticks, dtype=int)
                tick_times = self.resampled_time[tick_indices]
                tick_labels = [seconds_to_mmss(t) for t in tick_times]

                ax.set_xticks(tick_times)
                ax.set_xticklabels(tick_labels, rotation=45)

        # 设置标签和标题
        ax.set_xlabel('时间 (mm:ss)')
        ax.set_ylabel('温度 (℃)', color=temp1_color)
        ax.tick_params(axis='y', labelcolor=temp1_color)
        ax.grid(True, alpha=0.3)
        ax.set_title('温度曲线和ROR分析', y=1.18)

        # 添加图例
        ax.legend(lines, labels, loc='upper right')

        # 5. 在豆温曲线上标记事件点
        self.event_marker_data = []
        if self.show_event_markers_var.get():
            markers = self.get_event_markers()
            if markers:
                xs = [m[0] for m in markers]
                ys = [m[1] for m in markers]
                ax.scatter(xs, ys, color='black', s=50, zorder=5, marker='o')
                self.event_marker_data = markers  # 用于鼠标悬浮检测

        # 6. 绘制阶段划分条（在坐标系上方，图表名称下方）
        if self.show_phase_bar_var.get():
            bounds = self.find_phase_boundaries()
            if bounds is not None:
                t_charge, t_p1, t_fcs, t_drop = bounds
                # 横条范围：入豆 → 烘焙结束
                bar_x_start = t_charge
                bar_x_end = t_drop
                bar_width = bar_x_end - bar_x_start

                if bar_width > 0:
                    # 各段时间和百分比
                    p1_dur = t_p1 - t_charge
                    p2_dur = (t_fcs - t_p1) if t_fcs is not None else 0
                    p3_dur = t_drop - (t_fcs if t_fcs is not None else t_p1)
                    p1_pct = p1_dur / bar_width * 100
                    p2_pct = p2_dur / bar_width * 100
                    p3_pct = 100 - p1_pct - p2_pct

                    # 计算各阶段平均ROR（基于阶段温差/时间，而非平均瞬时ROR）
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

                    # 获取指定时间点的平滑温度
                    def _temp_at(t):
                        if self.resampled_time is None or self.smooth_temp1 is None or t is None:
                            return None
                        idx = np.searchsorted(self.resampled_time, t)
                        if idx < len(self.smooth_temp1):
                            return float(self.smooth_temp1[idx])
                        return None

                    # 找回升点（入豆后豆温最低点）= 回温点
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

                    # 第三阶段温差
                    t_fcs_val = _temp_at(t_fcs) if t_fcs else None
                    t_drop_val = _temp_at(t_drop)
                    p3_dt = (t_drop_val - t_fcs_val) if (t_fcs_val is not None and t_drop_val is not None) else None

                    # 构建标签
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
                        # 脱水期绿、美拉德期黄、发展期咖啡色（低饱和度）
                        (t_charge, t_p1, '#81C784', p1_label),
                        (t_p1, t_fcs if t_fcs is not None else t_drop, '#FFD54F', p2_label),
                        (t_fcs if t_fcs is not None else t_drop, t_drop, '#A0522D', p3_label),
                    ]

                    # 在坐标系上方绘制（y>1.0为axes坐标上方区域）
                    bar_bottom = 1.02  # axes坐标，略高于坐标系顶部
                    bar_height = 0.09

                    # 阶段条背景面板
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
                        # 绘制色块
                        rect = Rectangle((seg_start, bar_bottom), seg_end - seg_start, bar_height,
                                        facecolor=color, alpha=0.85, edgecolor='none',
                                        transform=transform, zorder=1, clip_on=False)
                        ax.add_patch(rect)

                        # 标签文字
                        mid = (seg_start + seg_end) / 2
                        ax.text(mid, bar_bottom + bar_height / 2, label,
                               transform=blended_transform_factory(ax.transData, ax.transAxes),
                               ha='center', va='center', fontsize=6.5, fontweight='bold')

                    # 分界虚线（浅黄色）
                    for boundary in [t_p1]:
                        if t_charge < boundary < t_drop:
                            ax.axvline(x=boundary, color='lightyellow', linestyle='--', linewidth=2, alpha=0.8)
                    if t_fcs is not None and t_charge < t_fcs < t_drop:
                        ax.axvline(x=t_fcs, color='lightyellow', linestyle='--', linewidth=2, alpha=0.8)

        # 调整布局（为阶段条腾出上方空间）
        self.fig.tight_layout()
        adj = {}
        if self.show_phase_bar_var.get():
            # 自适应top：根据图高保证阶段条有足够空间，至少1.2英寸
            fig_h = self.fig.get_figheight()
            adj['top'] = max(0.70, 1.0 - 1.4 / fig_h)
        if adj:
            self.fig.subplots_adjust(**adj)

        # 初始化鼠标追踪元素（一次性创建，后续复用）
        self._create_cursor_artists(ax)

        self._setup_blit()

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