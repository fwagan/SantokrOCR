"""
统计面板 - 温度曲线和ROR分析（PySide6 Qt 嵌入版本）
"""

import json
import os
import warnings

import matplotlib
import matplotlib.ticker
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.transforms import blended_transform_factory
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from scipy.interpolate import interp1d
from scipy.signal import savgol_filter

warnings.filterwarnings('ignore')

# --- ROR 非均匀 Y 轴配置 ---
ROR_COMPRESSION_FACTOR = 12.0
ROR_CUSTOM_TICKS = [-120, -100, -80, -60, -40, -20,
                    0, 5, 10, 15, 20, 25, 30]
ROR_YLIM = (-120, 30)


def ror_forward(x):
    return np.where(x >= 0, x, x / ROR_COMPRESSION_FACTOR)


def ror_inverse(x):
    return np.where(x >= 0, x, x * ROR_COMPRESSION_FACTOR)


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
                matplotlib.rcParams['font.sans-serif'] = [font_name]
                matplotlib.rcParams['axes.unicode_minus'] = False
                return True
        matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun']
        matplotlib.rcParams['axes.unicode_minus'] = False
        return True
    except Exception:
        return False


setup_chinese_font()


class _FigureCanvas(FigureCanvasQTAgg):
    """FigureCanvas 子类，添加 resize 信号用于 blit 缓存失效"""
    resized = Signal()

    def __init__(self, fig, parent=None):
        super().__init__(fig)
        self.setParent(parent)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class StatisticsPanel(QWidget):
    """统计面板（Qt 嵌入版本）"""

    statusChanged = Signal(str)

    def __init__(self, parent=None, results=None):
        super().__init__(parent)

        self._results = results if results is not None else []

        # 配置参数
        self.sampling_interval = 1.0
        self.smooth_window = 15
        self.smooth_polyorder = 3
        self.ror_interval = 15.0

        # 数据存储
        self.timestamps = None
        self.temp1_values = None
        self.temp2_values = None
        self.time_str_labels = None

        self.resampled_time = None
        self.resampled_temp1 = None
        self.resampled_temp2 = None

        self.smooth_temp1 = None
        self.smooth_temp2 = None

        self.ror_time = None
        self.ror_values = None

        # 鼠标追踪
        self.cursor_line = None
        self.cursor_info = None
        self._blit_bg = None
        self._blit_draw_cid = None

        self.ror_axis = None
        self.event_marker_data = []
        self.main_ax = None
        self.hf_axis = None

        # 事件数据
        self._events = []
        self._original_events = []
        self._heater_initial = 50.0
        self._fan_initial = 80.0

        # 控件引用（create_controls 时赋值）
        self.interval_edit = None
        self.window_edit = None
        self.polyorder_edit = None
        self.ror_interval_edit = None
        self.show_raw_cb = None
        self.show_hf_cb = None
        self.show_event_markers_cb = None
        self.show_phase_bar_cb = None
        self.exclude_outside_cb = None

        self._create_ui()

        if self._results:
            self.process_data()
            self.plot_charts()

    # ============== UI ==============

    def _create_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.fig = Figure(figsize=(14, 8), dpi=150)
        self.canvas = _FigureCanvas(self.fig, self)
        self.canvas.resized.connect(self._on_canvas_resized)
        layout.addWidget(self.canvas, stretch=1)

        # 状态栏
        status_frame = QFrame()
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(5, 2, 5, 2)
        self.status_label = QLabel("就绪")
        status_layout.addWidget(self.status_label)
        layout.addWidget(status_frame)

        # 鼠标事件
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
        self.canvas.mpl_connect('axes_leave_event', self._hide_cursor_elements)

    def create_controls(self, parent):
        """在 parent（需有 QVBoxLayout）中创建控制参数"""
        group = QGroupBox("控制参数", parent)
        layout = QVBoxLayout(group)

        self.interval_edit = QLineEdit()
        self.interval_edit.setText(str(self.sampling_interval))
        self.interval_edit.editingFinished.connect(self.recalculate)

        self.window_edit = QLineEdit()
        self.window_edit.setText(str(self.smooth_window))
        self.window_edit.editingFinished.connect(self.recalculate)

        self.polyorder_edit = QLineEdit()
        self.polyorder_edit.setText(str(self.smooth_polyorder))
        self.polyorder_edit.editingFinished.connect(self.recalculate)

        self.ror_interval_edit = QLineEdit()
        self.ror_interval_edit.setText(str(self.ror_interval))
        self.ror_interval_edit.editingFinished.connect(self.recalculate)

        # QGridLayout 两行两列，列宽自适应内容
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(6)

        grid.addWidget(QLabel("重采样间隔(秒):"), 0, 0)
        grid.addWidget(self.interval_edit, 0, 1)
        grid.addWidget(QLabel("平滑窗口(秒):"), 0, 2)
        grid.addWidget(self.window_edit, 0, 3)

        grid.addWidget(QLabel("多项式阶数:"), 1, 0)
        grid.addWidget(self.polyorder_edit, 1, 1)
        grid.addWidget(QLabel("ROR步长(秒):"), 1, 2)
        grid.addWidget(self.ror_interval_edit, 1, 3)

        # 全部不拉伸，列宽由内容决定
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 0)
        layout.addLayout(grid)

        # 按钮行
        btn_layout = QHBoxLayout()
        recalc_btn = QPushButton("计算曲线")
        recalc_btn.clicked.connect(self.recalculate)
        export_btn = QPushButton("导出数据")
        export_btn.clicked.connect(self.export_data)
        save_btn = QPushButton("保存图表")
        save_btn.clicked.connect(self.save_chart)
        btn_layout.addWidget(recalc_btn)
        btn_layout.addWidget(export_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        # Checkboxes
        self.show_raw_cb = QCheckBox("显示原曲线")
        self.show_raw_cb.toggled.connect(self.recalculate)
        layout.addWidget(self.show_raw_cb)

        self.show_hf_cb = QCheckBox("显示火力/风门")
        self.show_hf_cb.setChecked(True)
        self.show_hf_cb.toggled.connect(self.recalculate)
        layout.addWidget(self.show_hf_cb)

        self.show_event_markers_cb = QCheckBox("显示事件标记")
        self.show_event_markers_cb.setChecked(True)
        self.show_event_markers_cb.toggled.connect(self.recalculate)
        layout.addWidget(self.show_event_markers_cb)

        self.show_phase_bar_cb = QCheckBox("显示阶段条")
        self.show_phase_bar_cb.setChecked(True)
        self.show_phase_bar_cb.toggled.connect(self.recalculate)
        layout.addWidget(self.show_phase_bar_cb)

        self.exclude_outside_cb = QCheckBox("排除阶段外数据")
        self.exclude_outside_cb.toggled.connect(self.recalculate)
        layout.addWidget(self.exclude_outside_cb)

        parent.layout().addWidget(group)

    # ============== 公开 API ==============

    def set_results(self, results):
        self._results = results
        if self._results:
            self.process_data()
            self.plot_charts()

    def set_events(self, events, heater_initial=50.0, fan_initial=80.0):
        self._events = events or []
        self._original_events = events or []
        self._heater_initial = heater_initial
        self._fan_initial = fan_initial
        if hasattr(self, 'resampled_time') and self.resampled_time is not None and len(self.resampled_time) > 0:
            self.plot_charts()

    def setStatus(self, text):
        self.status_label.setText(text)
        self.statusChanged.emit(text)

    @property
    def results(self):
        return self._results

    @property
    def events(self):
        return self._events

    @property
    def heater_initial(self):
        return self._heater_initial

    @property
    def fan_initial(self):
        return self._fan_initial

    def recalculate(self):
        self.process_data()
        self.plot_charts()

    # ============== 数据处理 ==============

    def extract_valid_data(self):
        timestamps = []
        temp1_values = []
        temp2_values = []
        time_str_labels = []
        for result in self._results:
            if '?' in str(result.get('temp1_full', '')) or '?' in str(result.get('temp2', '')):
                continue
            try:
                timestamp = float(result['timestamp'])
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
        if len(timestamps) < 2:
            return timestamps, values
        start_time = np.min(timestamps)
        end_time = np.max(timestamps)
        resampled_time = np.arange(start_time, end_time + sampling_interval, sampling_interval)
        interp_func = interp1d(timestamps, values, kind='linear', bounds_error=False, fill_value='extrapolate')
        resampled_values = interp_func(resampled_time)
        return resampled_time, resampled_values

    def smooth_data(self, time, values, window_seconds, polyorder):
        """使用Savitzky-Golay滤波平滑数据，右边界线性外推避免跳动"""
        if len(values) < window_seconds:
            return values
        if len(time) > 1:
            dt = time[1] - time[0]
            window_points = int(window_seconds / dt)
            window_points = window_points if window_points % 2 == 1 else window_points + 1
            window_points = max(polyorder + 1, window_points)
            if window_points <= len(values):
                try:
                    radius = (window_points - 1) // 2
                    if radius > 0 and len(values) >= window_points:
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
                except Exception:
                    pass
        return values

    def compute_ror(self, time, temperature, sampling_interval, ror_interval):
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

    def build_heater_fan_data(self):
        if self.resampled_time is None or len(self.resampled_time) == 0:
            return None, None
        heater = np.full_like(self.resampled_time, float(self._heater_initial))
        fan = np.full_like(self.resampled_time, float(self._fan_initial))
        sorted_events = sorted(self._events, key=lambda x: x.get('time', 0))
        for ev in sorted_events:
            ev_time = ev.get('time', 0)
            ev_type = ev.get('type', '')
            ev_value = ev.get('value')
            if ev_value is None:
                continue
            idx = np.searchsorted(self.resampled_time, ev_time)
            if idx >= len(self.resampled_time):
                continue
            if ev_type == '调整火力':
                heater[idx:] = float(ev_value)
            elif ev_type == '调整风门':
                fan[idx:] = float(ev_value)
        return heater, fan

    def get_event_markers(self):
        markers = []
        if self.resampled_time is None or self.smooth_temp1 is None:
            return markers
        for ev in self._events:
            ev_type = ev.get('type', '')
            if ev_type in ('调整火力', '调整风门'):
                continue
            ev_time = ev.get('time', 0)
            idx = np.abs(self.resampled_time - ev_time).argmin()
            if idx < len(self.smooth_temp1):
                temp = self.smooth_temp1[idx]
                markers.append((ev_time, temp, ev_type))
        return markers

    def find_phase_boundaries(self):
        charge_time = None
        fcs_time = None
        drop_time = None
        for ev in self._events:
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
            phase1_end_time = fcs_time if fcs_time is not None else charge_time + 60
        return (charge_time, phase1_end_time, fcs_time, drop_time)

    def process_data(self):
        self.timestamps, self.temp1_values, self.temp2_values, self.time_str_labels = \
            self.extract_valid_data()

        if len(self.timestamps) < 2:
            self.setStatus("错误：有效数据不足")
            return

        if self.interval_edit:
            try: self.sampling_interval = float(self.interval_edit.text())
            except ValueError: pass
        if self.window_edit:
            try: self.smooth_window = int(self.window_edit.text())
            except ValueError: pass
        if self.polyorder_edit:
            try: self.smooth_polyorder = int(self.polyorder_edit.text())
            except ValueError: pass
        if self.ror_interval_edit:
            try: self.ror_interval = float(self.ror_interval_edit.text())
            except ValueError: pass

        self.resampled_time, self.resampled_temp1 = self.resample_data(
            self.timestamps, self.temp1_values, self.sampling_interval)
        _, self.resampled_temp2 = self.resample_data(
            self.timestamps, self.temp2_values, self.sampling_interval)

        self.smooth_temp1 = self.smooth_data(
            self.resampled_time, self.resampled_temp1,
            self.smooth_window, self.smooth_polyorder)
        self.smooth_temp2 = self.smooth_data(
            self.resampled_time, self.resampled_temp2,
            self.smooth_window, self.smooth_polyorder)

        self.ror_time, self.ror_values = self.compute_ror(
            self.resampled_time, self.smooth_temp1, self.sampling_interval, self.ror_interval)

        self._events = list(self._original_events)

        self.setStatus(f"处理完成：{len(self.timestamps)}个有效点，{len(self.resampled_time)}个重采样点")

    # ============== 绘图 ==============

    def plot_charts(self):
        self.fig.clear()
        if len(self.timestamps) < 2:
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, '数据不足，无法绘制图表',
                   horizontalalignment='center', verticalalignment='center',
                   transform=ax.transAxes, fontsize=14)
            self._blit_bg = None
            self.canvas.draw()
            return

        exclude = self.exclude_outside_cb.isChecked() if self.exclude_outside_cb else False
        if exclude:
            charge_t = None
            drop_t = None
            for ev in self._events:
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
                self._events = [{**ev, 'time': ev.get('time', 0) - charge_t} for ev in self._original_events]
                t_min = self.resampled_time[0]
                t_max = self.resampled_time[-1]
                for ev in self._events:
                    ev['time'] = max(t_min, min(ev['time'], t_max))

        ax = self.fig.add_subplot(111)
        self.main_ax = ax

        temp1_color = 'tab:blue'
        temp2_color = 'tab:orange'
        ror_color = 'tab:red'

        def seconds_to_mmss(seconds):
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes:02d}:{secs:02d}"

        # 豆温曲线（平滑）
        line1, = ax.plot(self.resampled_time, self.smooth_temp1,
                        color=temp1_color, linewidth=2, label='豆温（平滑）')
        line1_raw = None
        show_raw = self.show_raw_cb.isChecked() if self.show_raw_cb else False
        if show_raw:
            line1_raw, = ax.plot(self.timestamps, self.temp1_values,
                               color=temp1_color, linewidth=0.8, alpha=0.25, label='豆温（原始）')

        # 风温曲线（平滑）
        line2, = ax.plot(self.resampled_time, self.smooth_temp2,
                        color=temp2_color, linewidth=2, label='风温（平滑）')
        line2_raw = None
        if show_raw:
            line2_raw, = ax.plot(self.timestamps, self.temp2_values,
                               color='darkorange', linewidth=0.8, alpha=0.25, label='风温（原始）')

        # ROR 曲线
        self.ror_axis = None
        if len(self.ror_values) > 0:
            self.ror_axis = ax.twinx()
            line3, = self.ror_axis.plot(self.ror_time, self.ror_values,
                            color=ror_color, linewidth=2, label='ROR')
            self.ror_axis.set_ylabel('ROR (℃/min)', color=ror_color)
            self.ror_axis.set_yscale('function', functions=(ror_forward, ror_inverse))
            self.ror_axis.set_ylim(*ROR_YLIM)
            self.ror_axis.yaxis.set_major_locator(
                matplotlib.ticker.FixedLocator(ROR_CUSTOM_TICKS))
            self.ror_axis.yaxis.set_minor_locator(
                matplotlib.ticker.NullLocator())
            self.ror_axis.tick_params(axis='y', labelcolor=ror_color)
            self.ror_axis.axhline(y=0, color='gray', linestyle='--', alpha=0.5)

            lines = [line1, line2, line3]
            labels = [line.get_label() for line in lines]
            if line1_raw is not None:
                lines.append(line1_raw); labels.append(line1_raw.get_label())
            if line2_raw is not None:
                lines.append(line2_raw); labels.append(line2_raw.get_label())
        else:
            lines = [line1, line2]
            labels = [line.get_label() for line in lines]
            if line1_raw is not None:
                lines.append(line1_raw); labels.append(line1_raw.get_label())
            if line2_raw is not None:
                lines.append(line2_raw); labels.append(line2_raw.get_label())

        # 火力/风门
        self.hf_axis = None
        show_hf = self.show_hf_cb.isChecked() if self.show_hf_cb else False
        if show_hf:
            heater_data, fan_data = self.build_heater_fan_data()
            if heater_data is not None and fan_data is not None:
                self.hf_axis = ax.twinx()
                hf_pos = 1.05 if self.ror_axis is not None else 1.00
                self.hf_axis.spines['right'].set_position(('axes', hf_pos))
                line4, = self.hf_axis.plot(self.resampled_time, heater_data,
                                           color='green', linewidth=2, linestyle='-', label='火力')
                line5, = self.hf_axis.plot(self.resampled_time, fan_data,
                                           color='purple', linewidth=2, linestyle='--', label='风门')
                self.hf_axis.set_ylabel('火力/风门 (%)', color='green')
                self.hf_axis.set_ylim(0, 200)
                self.hf_axis.tick_params(axis='y', labelcolor='green')
                lines.extend([line4, line5])
                labels.extend(['火力', '风门'])

        # X 轴时间格式
        if len(self.resampled_time) > 0:
            time_range = self.resampled_time[-1] - self.resampled_time[0]
            if time_range > 0:
                num_ticks = min(10, len(self.resampled_time))
                tick_indices = np.linspace(0, len(self.resampled_time)-1, num_ticks, dtype=int)
                tick_times = self.resampled_time[tick_indices]
                tick_labels = [seconds_to_mmss(t) for t in tick_times]
                ax.set_xticks(tick_times)
                ax.set_xticklabels(tick_labels, rotation=45)

        if len(self.resampled_time) > 1:
            min_end = self.resampled_time[0] + 480  # 至少显示 8 分钟
            ax.set_xlim(self.resampled_time[0], max(self.resampled_time[-1], min_end))
        ax.set_xlabel('时间 (mm:ss)')
        ax.set_ylabel('温度 (℃)', color=temp1_color)
        ax.tick_params(axis='y', labelcolor=temp1_color)
        ax.grid(True, alpha=0.3)
        ax.set_title('温度曲线和ROR分析', y=1.18)
        ax.legend(lines, labels, loc='upper right')

        # 事件标记
        self.event_marker_data = []
        show_markers = self.show_event_markers_cb.isChecked() if self.show_event_markers_cb else False
        if show_markers:
            markers = self.get_event_markers()
            if markers:
                xs = [m[0] for m in markers]
                ys = [m[1] for m in markers]
                ax.scatter(xs, ys, color='black', s=50, zorder=5, marker='o')
                self.event_marker_data = markers

        # 阶段条
        show_phase = self.show_phase_bar_cb.isChecked() if self.show_phase_bar_cb else False
        if show_phase:
            bounds = self.find_phase_boundaries()
            if bounds is not None:
                t_charge, t_p1, t_fcs, t_drop = bounds
                bar_x_start = t_charge
                bar_x_end = t_drop
                bar_width = bar_x_end - bar_x_start
                if bar_width > 0:
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

        self.fig.tight_layout()
        adj = {}
        if show_phase:
            fig_h = self.fig.get_figheight()
            adj['top'] = max(0.70, 1.0 - 1.4 / fig_h)
        if adj:
            self.fig.subplots_adjust(**adj)

        self._create_cursor_artists(ax)
        self._setup_blit()

    def _create_cursor_artists(self, ax):
        for art in [self.cursor_line, self.cursor_info] if hasattr(self, 'cursor_info') else [self.cursor_line]:
            if art is not None:
                try:
                    art.remove()
                except Exception:
                    pass
        self.cursor_line = ax.axvline(
            x=0, color='gray', linestyle='--', alpha=0.7, linewidth=1,
            visible=False, zorder=20
        )
        self.cursor_line.set_animated(True)
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
        for art in [self.cursor_line, self.cursor_info]:
            if art is not None:
                try:
                    art.set_visible(False)
                except Exception:
                    pass

    def _on_blit_draw(self, event):
        self._blit_bg = self.canvas.copy_from_bbox(self.fig.bbox)

    def _setup_blit(self):
        if self._blit_draw_cid:
            try:
                self.canvas.mpl_disconnect(self._blit_draw_cid)
            except Exception:
                pass
        self._blit_draw_cid = self.canvas.mpl_connect('draw_event', self._on_blit_draw)
        self._blit_bg = None
        self.canvas.draw()

    def _on_canvas_resized(self):
        """画布尺寸变化时重排并失效 blit 缓存"""
        self._blit_bg = None
        try:
            self.fig.tight_layout()
            show_phase = self.show_phase_bar_cb.isChecked() if self.show_phase_bar_cb else False
            adj = {}
            if show_phase:
                fig_h = self.fig.get_figheight()
                adj['top'] = max(0.70, 1.0 - 1.4 / fig_h)
            if adj:
                self.fig.subplots_adjust(**adj)
            self.canvas.draw_idle()
        except Exception:
            pass

    def on_mouse_move(self, event):
        if not event.inaxes:
            return
        ax = event.inaxes
        xdata = event.xdata

        self.cursor_line.set_xdata([xdata, xdata])
        self.cursor_line.set_visible(True)

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
            show_hf = self.show_hf_cb.isChecked() if self.show_hf_cb else False
            if self.hf_axis is not None and show_hf:
                h_data, f_data = self.build_heater_fan_data()
                if h_data is not None and idx < len(h_data):
                    info_parts.append(f"火力: {h_data[idx]:.0f}%  风门: {f_data[idx]:.0f}%")

        self.cursor_info.set_text('\n'.join(info_parts))
        self.cursor_info.set_visible(True)

        cur_ax = self.main_ax if self.main_ax is not None else ax
        if self._blit_bg is not None:
            self.canvas.restore_region(self._blit_bg)
            cur_ax.draw_artist(self.cursor_line)
            cur_ax.draw_artist(self.cursor_info)
            self.canvas.blit(self.fig.bbox)
        else:
            self.canvas.draw_idle()

    def format_time(self, seconds):
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{minutes:02d}:{secs:02d}.{millis:03d}"

    # ============== 导出 ==============

    def export_data(self):
        if not self._results:
            QMessageBox.warning(self, "警告", "没有可导出的数据")
            return

        export = {
            'version': 1,
            'results': self._results,
            'events': self._events,
            'heater_initial': self._heater_initial,
            'fan_initial': self._fan_initial,
        }

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出数据", "",
            "Slog files (*.slog);;All files (*.*)"
        )
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(export, f, indent=2, ensure_ascii=False)
            self.setStatus(f"数据已导出到: {file_path}")

    def save_chart(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存图表", "",
            "PNG files (*.png);;PDF files (*.pdf);;All files (*.*)"
        )
        if file_path:
            self.fig.savefig(file_path, dpi=300, bbox_inches='tight')
            self.setStatus(f"图表已保存到: {file_path}")


if __name__ == "__main__":
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    w = QWidget()
    w.setWindowTitle("统计面板测试")
    w.resize(1200, 800)
    layout = QVBoxLayout(w)

    test_results = []
    for i in range(100):
        timestamp = i * 0.5
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

    panel = StatisticsPanel(w, test_results)
    layout.addWidget(panel)
    w.show()
    sys.exit(app.exec())
