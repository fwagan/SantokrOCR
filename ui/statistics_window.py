"""
统计窗口 - 温度曲线和ROR分析

功能：
1. 显示豆温(temp1full)和风温(temp2)曲线
2. 计算并显示豆温的ROR曲线
3. 支持重采样和平滑处理
4. 支持参数调整（采样间隔、平滑窗口等）
"""

import tkinter as tk
from tkinter import ttk
import numpy as np
from scipy.signal import savgol_filter
from scipy.interpolate import interp1d
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
import matplotlib
import warnings
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


class StatisticsWindow(tk.Toplevel):
    """统计窗口"""

    def __init__(self, parent, results):
        """
        初始化统计窗口

        Args:
            parent: 父窗口
            results: 结果数据列表
        """
        super().__init__(parent)
        self.parent = parent
        self.results = results

        self.title("温度曲线统计")
        self.geometry("1200x800")

        # 配置参数
        self.sampling_interval = 1.0  # 重采样间隔（秒）
        self.smooth_window = 15       # 平滑窗口大小（秒）
        self.smooth_polyorder = 3     # 平滑多项式阶数
        self.ror_interval = 5.0       # ROR计算步长（秒）

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

        # 创建UI
        self.create_ui()

        # 处理数据
        self.process_data()

        # 绘制图表
        self.plot_charts()

    def create_ui(self):
        """创建用户界面"""
        # 主框架
        main_frame = ttk.Frame(self)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 控制面板（顶部）
        control_frame = ttk.LabelFrame(main_frame, text="控制参数")
        control_frame.pack(fill="x", pady=(0, 10))

        # 参数设置行
        param_row = ttk.Frame(control_frame)
        param_row.pack(fill="x", padx=10, pady=10)

        # 选项行
        options_row = ttk.Frame(control_frame)
        options_row.pack(fill="x", padx=10, pady=(0, 10))

        self.show_raw_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_row, text="显示原曲线", variable=self.show_raw_var,
                       command=self.recalculate).pack(side="left")

        # 重采样间隔
        ttk.Label(param_row, text="重采样间隔(秒):").pack(side="left", padx=(0, 5))
        self.interval_var = tk.DoubleVar(value=self.sampling_interval)
        interval_spin = ttk.Spinbox(param_row, from_=0.1, to=10.0, increment=0.1,
                                   textvariable=self.interval_var, width=8)
        interval_spin.pack(side="left", padx=(0, 20))

        # 平滑窗口
        ttk.Label(param_row, text="平滑窗口(秒):").pack(side="left", padx=(0, 5))
        self.window_var = tk.IntVar(value=self.smooth_window)
        window_spin = ttk.Spinbox(param_row, from_=3, to=60, increment=1,
                                 textvariable=self.window_var, width=8)
        window_spin.pack(side="left", padx=(0, 20))

        # 多项式阶数
        ttk.Label(param_row, text="多项式阶数:").pack(side="left", padx=(0, 5))
        self.polyorder_var = tk.IntVar(value=self.smooth_polyorder)
        polyorder_spin = ttk.Spinbox(param_row, from_=1, to=5, increment=1,
                                    textvariable=self.polyorder_var, width=8)
        polyorder_spin.pack(side="left", padx=(0, 20))

        # ROR计算步长
        ttk.Label(param_row, text="ROR步长(秒):").pack(side="left", padx=(0, 5))
        self.ror_interval_var = tk.DoubleVar(value=self.ror_interval)
        ror_spin = ttk.Spinbox(param_row, from_=1, to=30, increment=1,
                              textvariable=self.ror_interval_var, width=8)
        ror_spin.pack(side="left", padx=(0, 20))

        # 按钮
        ttk.Button(param_row, text="重新计算", command=self.recalculate).pack(side="left", padx=(20, 0))
        ttk.Button(param_row, text="导出数据", command=self.export_data).pack(side="left", padx=10)
        ttk.Button(param_row, text="保存图表", command=self.save_chart).pack(side="left", padx=10)

        # 图表框架
        chart_frame = ttk.Frame(main_frame)
        chart_frame.pack(fill="both", expand=True)

        # 创建Matplotlib图形
        self.fig = Figure(figsize=(12, 8), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, chart_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # 状态栏
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill="x", pady=(10, 0))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")

        # 绑定事件
        interval_spin.bind('<Return>', lambda e: self.recalculate())
        window_spin.bind('<Return>', lambda e: self.recalculate())
        polyorder_spin.bind('<Return>', lambda e: self.recalculate())
        ror_spin.bind('<Return>', lambda e: self.recalculate())

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

        self.status_var.set(f"处理完成：{len(self.timestamps)}个有效点，{len(self.resampled_time)}个重采样点")

    def plot_charts(self):
        """绘制图表"""
        self.fig.clear()

        if len(self.timestamps) < 2:
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, '数据不足，无法绘制图表',
                   horizontalalignment='center', verticalalignment='center',
                   transform=ax.transAxes, fontsize=14)
            self.canvas.draw()
            return

        # 创建3个子图
        ax1 = self.fig.add_subplot(311)  # 豆温
        ax2 = self.fig.add_subplot(312)  # 风温
        ax3 = self.fig.add_subplot(313)  # ROR

        # 设置颜色
        temp1_color = 'tab:blue'
        temp2_color = 'tab:orange'
        ror_color = 'tab:red'

        # 1. 豆温曲线
        ax1.plot(self.resampled_time, self.smooth_temp1, color=temp1_color, linewidth=2, label='豆温（平滑）')
        if self.show_raw_var.get():
            ax1.plot(self.timestamps, self.temp1_values, color=temp1_color,
                    linewidth=0.8, alpha=0.25, label='豆温（原始）')
        ax1.set_ylabel('温度 (℃)', color=temp1_color)
        ax1.tick_params(axis='y', labelcolor=temp1_color)
        ax1.grid(True, alpha=0.3)
        ax1.legend(loc='upper left')
        ax1.set_title('豆温曲线')

        # 2. 风温曲线
        ax2.plot(self.resampled_time, self.smooth_temp2, color=temp2_color, linewidth=2, label='风温（平滑）')
        if self.show_raw_var.get():
            ax2.plot(self.timestamps, self.temp2_values, color='darkorange',
                    linewidth=0.8, alpha=0.25, label='风温（原始）')
        ax2.set_ylabel('温度 (℃)', color=temp2_color)
        ax2.tick_params(axis='y', labelcolor=temp2_color)
        ax2.grid(True, alpha=0.3)
        ax2.legend(loc='upper left')
        ax2.set_title('风温曲线')

        # 3. ROR曲线
        if len(self.ror_values) > 0:
            ax3.plot(self.ror_time, self.ror_values, color=ror_color, linewidth=2, label='ROR')
            ax3.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
            ax3.set_ylabel('ROR (℃/min)', color=ror_color)

            # 非均匀 Y 轴：展开正半区（0~30），压缩负半区（0~-120）
            ax3.set_yscale('function', functions=(ror_forward, ror_inverse))
            ax3.set_ylim(*ROR_YLIM)
            ax3.yaxis.set_major_locator(
                matplotlib.ticker.FixedLocator(ROR_CUSTOM_TICKS))
            ax3.yaxis.set_minor_locator(
                matplotlib.ticker.NullLocator())

            ax3.tick_params(axis='y', labelcolor=ror_color)
            ax3.grid(True, alpha=0.3)
            ax3.legend(loc='upper left')
            ax3.set_title('豆温升温速率 (ROR)')
            ax3.set_xlabel('时间 (秒)')

        # 调整布局
        self.fig.tight_layout()
        self.canvas.draw()

    def recalculate(self, event=None):
        """重新计算并更新图表"""
        self.process_data()
        self.plot_charts()

    def export_data(self):
        """导出处理后的数据到CSV"""
        if self.resampled_time is None:
            return

        import pandas as pd
        from tkinter import filedialog

        # 创建DataFrame
        data = {
            'time_seconds': self.resampled_time,
            'temp1_smooth': self.smooth_temp1,
            'temp2_smooth': self.smooth_temp2
        }

        # 添加ROR数据（注意长度可能不同）
        if len(self.ror_values) > 0:
            # 创建与resampled_time相同长度的数组，用NaN填充
            ror_full = np.full_like(self.resampled_time, np.nan)
            # 找到ROR时间点对应的索引
            for i, t in enumerate(self.ror_time):
                idx = np.abs(self.resampled_time - t).argmin()
                if idx < len(ror_full):
                    ror_full[idx] = self.ror_values[i]
            data['ror'] = ror_full

        df = pd.DataFrame(data)

        # 选择保存路径
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )

        if file_path:
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            self.status_var.set(f"数据已导出到: {file_path}")

    def save_chart(self):
        """保存图表为图片"""
        from tkinter import filedialog

        file_path = filedialog.asksaveasfilename(
            defaultextextension=".png",
            filetypes=[("PNG files", "*.png"), ("PDF files", "*.pdf"), ("All files", "*.*")]
        )

        if file_path:
            self.fig.savefig(file_path, dpi=300, bbox_inches='tight')
            self.status_var.set(f"图表已保存到: {file_path}")


if __name__ == "__main__":
    # 测试代码
    root = tk.Tk()
    root.title("统计窗口测试")

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
            'temp2': f"{temp2:.1f}",
            'quality': 'high'
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
            'temp2': '????',
            'quality': 'low'
        }
        test_results.append(result)

    window = StatisticsWindow(root, test_results)
    root.mainloop()