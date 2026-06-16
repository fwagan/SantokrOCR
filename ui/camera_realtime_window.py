"""
实时识别窗口 — 摄像头实时数字识别与曲线绘制

布局：
- 顶部控制栏：数据源选择、ROI选择、采样间隔、旋转角度
- 主体 PanedWindow：左 预览画布 + 右 Notebook（Tab1 实时曲线 + Tab2 数据表格）
- 中部按钮栏：开始/暂停/停止
- 状态栏
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog
import cv2
import time
import os
import threading
from typing import Optional
import numpy as np
from PIL import Image, ImageTk

from core.video_extractor import VideoDigitExtractor
from core.camera_capture import CameraProcessingThread
from core.realtime_cache import RealTimeProcessCache
from ui.data_table import DataTable
from ui.statistics_panel import StatisticsPanel
from ui.frame_viewer import FrameViewer
from ui.slog_comparer import extract_valid_data, resample_data, smooth_data, compute_ror
from utils.screen_utils import center_window
from utils.cache_manager import get_cache_manager
from utils.file_system import Paths, FileOperations
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.result_repo import SqliteResultRepository
from data.sqlite.event_repo import SqliteEventRepository
from data.sqlite.bean_repo import SqliteBeanRepository
from data.sqlite.session_writer import SessionWriter
from data.serializers.slog import SlogSerializer
from ui.ideal_curve_dialog import IdealCurveDialog


class CameraRealtimeWindow(tk.Toplevel):
    """实时识别窗口"""

    def __init__(self, parent):
        super().__init__(parent)

        self.parent = parent

        # 数据源
        self.rois = None
        self.results = []
        self.extractor = VideoDigitExtractor()

        # 线程
        self.processing_thread = None

        # 帧缓存
        self._cache = RealTimeProcessCache()


        # 持久缓存（摄像头ROI持久化，防止摄像头断开/窗口关闭后丢失）
        self._cache_manager = get_cache_manager()

        # 数据库
        self._session_repo = SqliteSessionRepository()
        self._result_repo = SqliteResultRepository()
        self._event_repo = SqliteEventRepository()

        # 理想曲线
        self.ideal_data = None

        # 预览
        self._preview_after_id = None
        self._preview_img_id = None
        self._preview_tk_image = None
        self._no_data_text_id = None
        self._retry_text_id = None
        self._preview_thread = None
        self._preview_thread_running = False
        self._preview_frame = None
        self._preview_frame_event = threading.Event()
        self._preview_lost = False
        self._preview_fail_count = 0

        # 窗口设置（自适应屏幕90%，不超过3200x1900）
        self.title("实时识别 - 摄像头")
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = min(3200, int(sw * 0.9))
        h = min(1900, int(sh * 0.9))
        self.minsize(1200, 800)
        center_window(self, w, h)
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.bind("<Escape>", lambda e: self._on_closing())

        # 创建UI
        self._create_ui()

        # 强制布局计算
        self.update_idletasks()
        # 延迟到窗口映射后按真实DPI设fig尺寸并重绘
        self.after_idle(self._fit_chart)

        # 启动预览
        self._start_preview()

        # 自动检测并选择第一个可用摄像头
        self._auto_select_first_camera()

        # 后台清理旧缓存会话
        threading.Thread(target=RealTimeProcessCache.cleanup_old_sessions,
                         args=(5,), daemon=True).start()

    def _fit_chart(self):
        """窗口映射后按显示器真实DPI设fig尺寸并重绘"""
        try:
            cw = self.stats_panel.canvas.get_tk_widget()
            w, h = cw.winfo_width(), cw.winfo_height()
            if w < 10 or h < 10:
                self.after(50, self._fit_chart)
                return
            dpi = cw.winfo_fpixels('1i')
            self.stats_panel.fig.set_size_inches(w / dpi, h / dpi, forward=False)
            self.stats_panel.fig.set_dpi(dpi)
            self.stats_panel.fig.tight_layout()
            self.stats_panel.canvas.draw()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # UI创建
    # ═══════════════════════════════════════════════════════════

    def _create_ui(self):
        """创建完整UI布局"""
        # ── 顶部控制栏 ──
        top_bar = ttk.Frame(self, padding=8)
        top_bar.pack(fill="x")

        # 数据源选择
        ttk.Label(top_bar, text="数据源:").pack(side="left", padx=(0, 4))
        ttk.Button(top_bar, text="刷新数据源", command=self._detect_cameras).pack(side="left", padx=(0, 4))
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(top_bar, textvariable=self.source_var,
                                          state="readonly", width=30)
        self.source_combo.pack(side="left", padx=4)
        self.source_combo.bind("<<ComboboxSelected>>", self._on_source_changed)

        # 选择ROI按钮（临时：点击时保存 cProfile dump）
        ttk.Button(top_bar, text="选择ROI", command=self._select_roi).pack(side="left", padx=8)
        self.roi_status_var = tk.StringVar(value="未配置")
        ttk.Label(top_bar, textvariable=self.roi_status_var, width=16, relief="sunken", padding=3).pack(side="left", padx=4)

        # 采样间隔
        ttk.Label(top_bar, text="采样间隔(s):").pack(side="left", padx=(16, 4))
        self.interval_var = tk.StringVar(value="0.25")
        ttk.Entry(top_bar, textvariable=self.interval_var, width=6).pack(side="left", padx=4)

        # 旋转角度
        ttk.Label(top_bar, text="旋转角度:").pack(side="left", padx=(12, 4))
        self.rotation_var = tk.StringVar(value="5")
        ttk.Entry(top_bar, textvariable=self.rotation_var, width=5).pack(side="left", padx=4)

        # 操作按钮（旋转角度右侧）
        self.start_btn = ttk.Button(top_bar, text="开始实时识别", command=self._start_realtime, state="disabled")
        self.start_btn.pack(side="left", padx=(12, 4))

        self.pause_btn = ttk.Button(top_bar, text="暂停", command=self._pause_realtime, state="disabled")
        self.pause_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(top_bar, text="停止", command=self._stop_realtime, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.clear_btn = ttk.Button(top_bar, text="清空已识别数据", command=self._clear_all_data)
        self.clear_btn.pack(side="left", padx=4)

        ttk.Button(top_bar, text="导出数据", command=self._export_data).pack(side="left", padx=(4, 0))
        self.export_session_btn = ttk.Button(top_bar, text="导出会话", command=self._export_session, state="disabled")
        self.export_session_btn.pack(side="left", padx=4)

        self.save_db_btn = ttk.Button(top_bar, text="保存会话到数据库", command=self._save_to_database, state="disabled")
        self.save_db_btn.pack(side="left", padx=4)

        # ── 主体：预览 + 右侧Notebook ──
        main = ttk.PanedWindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=4)

        # 左侧：预览画布 + 实时状态
        preview_frame = ttk.LabelFrame(main, text="摄像头预览", padding=4)
        self.preview_canvas = tk.Canvas(preview_frame, bg="#222222", highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True)

        self._realtime_status_frame = self._create_realtime_status(preview_frame)
        self._realtime_status_frame.pack(side="bottom", fill="x", padx=4, pady=4)
        main.add(preview_frame, weight=45)

        # 右侧：Notebook（曲线 + 数据表格）
        right_frame = ttk.Frame(main)
        main.add(right_frame, weight=55)

        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill="both", expand=True)

        # Tab 1: 理想曲线
        ideal_tab = ttk.Frame(self.notebook)
        self.notebook.add(ideal_tab, text="理想曲线")
        self._create_ideal_curve_tab(ideal_tab)

        # Tab 2: 实时曲线
        curve_tab = ttk.Frame(self.notebook)
        curve_tab.pack_propagate(False)  # 阻止FigureCanvasTkAgg塌缩父容器
        self.notebook.add(curve_tab, text="实时曲线")

        self.stats_panel = StatisticsPanel(curve_tab, is_realtime=True, results=[], figsize=(7, 5))
        self.stats_panel.pack(side="top", fill="both", expand=True)

        # 曲线控制 dock bottom（实时模式：仅显示原曲线checkbox）
        ctrl_row = ttk.Frame(curve_tab)
        self.stats_panel.create_controls(ctrl_row, realtime_mode=True)
        ctrl_row.pack(side="bottom", fill="x", pady=(4, 0))

        # Tab 3: 数据表格
        table_tab = ttk.Frame(self.notebook)
        self.notebook.add(table_tab, text="数据表格")

        self.data_table = DataTable(table_tab)
        self.data_table.pack(fill="both", expand=True)
        self.data_table.set_view_frame_callback(self._on_view_frame)

        # ── 状态栏 ──
        status_bar = ttk.Frame(self, relief="sunken", borderwidth=1)
        status_bar.pack(side="bottom", fill="x", padx=8, pady=(0, 4))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_bar, textvariable=self.status_var, padding=(8, 4)).pack(side="left")

        self.time_var = tk.StringVar(value="运行时长: 00:00")
        ttk.Label(status_bar, textvariable=self.time_var, padding=(8, 4)).pack(side="right")

    # ═══════════════════════════════════════════════════════════
    # 实时状态栏
    # ═══════════════════════════════════════════════════════════

    def _create_realtime_status(self, parent):
        """创建底部实时状态条：豆温(蓝) 风温(橙) ROR(红) 加大字号"""
        status_frame = ttk.Frame(parent)

        # 配置三列等宽
        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)
        status_frame.columnconfigure(2, weight=1)

        title_font = tkfont.Font(size=12, weight="bold")
        value_font = tkfont.Font(size=48, weight="bold")

        # 豆温（蓝色 #4488ff）
        f0 = ttk.Frame(status_frame)
        f0.grid(row=0, column=0, sticky="nsew", padx=4, pady=2)
        ttk.Label(f0, text="豆温(℃)", font=title_font,
                  foreground="#4488ff", anchor="center").pack(pady=(6, 0))
        self._bean_temp_var = tk.StringVar(value="--.-")
        bean_lbl = ttk.Label(f0, textvariable=self._bean_temp_var,
                             font=value_font, foreground="#4488ff", anchor="center")
        bean_lbl.pack(expand=True, fill="both")

        # 风温（橙色 #ff8844）
        f1 = ttk.Frame(status_frame)
        f1.grid(row=0, column=1, sticky="nsew", padx=4, pady=2)
        ttk.Label(f1, text="风温(℃)", font=title_font,
                  foreground="#ff8844", anchor="center").pack(pady=(6, 0))
        self._air_temp_var = tk.StringVar(value="--.-")
        air_lbl = ttk.Label(f1, textvariable=self._air_temp_var,
                            font=value_font, foreground="#ff8844", anchor="center")
        air_lbl.pack(expand=True, fill="both")

        # ROR（红色 #ff4444）
        f2 = ttk.Frame(status_frame)
        f2.grid(row=0, column=2, sticky="nsew", padx=4, pady=2)
        ttk.Label(f2, text="ROR(℃/min)", font=title_font,
                  foreground="#ff4444", anchor="center").pack(pady=(6, 0))
        self._ror_var = tk.StringVar(value="--.-")
        ror_lbl = ttk.Label(f2, textvariable=self._ror_var,
                            font=value_font, foreground="#ff4444", anchor="center")
        ror_lbl.pack(expand=True, fill="both")

        return status_frame

    def _update_realtime_status(self, result):
        """更新底部实时状态：豆温、风温、ROR（异常/识别失败时保留上次有效值）"""
        # 豆温
        temp1 = result.get('temp1_full', '')
        if temp1 and '?' not in temp1 and result.get('abnormal_category') != 'temperature_diff':
            try:
                v = float(temp1)
                self._bean_temp_var.set(f"{v:.1f}")
            except ValueError:
                pass

        # 风温
        temp2 = result.get('temp2', '')
        if temp2 and '?' not in temp2:
            try:
                v = float(temp2)
                self._air_temp_var.set(f"{v:.1f}")
            except ValueError:
                pass

        # ROR（从 StatisticsPanel 已有的计算结果取最新值）
        ror = None
        if self.stats_panel.ror_values is not None and len(self.stats_panel.ror_values) > 0:
            ror = self.stats_panel.ror_values[-1]
        if ror is not None:
            self._ror_var.set(f"{ror:+.1f}")
        else:
            self._ror_var.set("--.-")

    def _reset_status_display(self):
        """重置实时状态栏显示为初始值"""
        self._bean_temp_var.set("--.-")
        self._air_temp_var.set("--.-")
        self._ror_var.set("--.-")

    # ═══════════════════════════════════════════════════════════
    # 理想曲线
    # ═══════════════════════════════════════════════════════════

    def _create_ideal_curve_tab(self, parent):
        """创建理想曲线Tab UI"""
        # 选择行
        file_frame = ttk.Frame(parent, padding=8)
        file_frame.pack(fill="x")
        ttk.Button(file_frame, text="选择.slog文件", command=self._select_ideal_slog).pack(side="left", padx=(0, 4))
        ttk.Button(file_frame, text="从数据库选择", command=self._select_ideal_session).pack(side="left", padx=(0, 8))
        self.ideal_file_label = ttk.Label(file_frame, text="未选择")
        self.ideal_file_label.pack(side="left")

        # 显示选项
        opt_frame = ttk.LabelFrame(parent, text="显示选项", padding=8)
        opt_frame.pack(fill="x", padx=8, pady=4)
        self.ideal_bean_var = tk.BooleanVar(value=True)
        self.ideal_ror_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="显示豆温曲线", variable=self.ideal_bean_var,
                        command=self._on_ideal_curve_changed).pack(anchor="w")
        ttk.Checkbutton(opt_frame, text="显示ROR曲线", variable=self.ideal_ror_var,
                        command=self._on_ideal_curve_changed).pack(anchor="w")

        # 信息区域
        info_frame = ttk.LabelFrame(parent, text="理想曲线信息", padding=8)
        info_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.ideal_info_text = tk.Text(info_frame, height=12, state="disabled", wrap="word")
        self.ideal_info_text.pack(fill="both", expand=True)

        # 清除按钮
        btn_frame = ttk.Frame(parent, padding=8)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="清除理想曲线", command=self._clear_ideal_slog).pack(side="left")

    def _select_ideal_slog(self):
        """打开文件选择对话框加载理想曲线"""
        path = filedialog.askopenfilename(
            title="选择理想曲线文件",
            filetypes=[("Slog文件", "*.slog"), ("所有文件", "*.*")],
            parent=self
        )
        if not path:
            return
        self._load_ideal_slog(path)

    def _select_ideal_session(self):
        """打开对话框从数据库选择理想曲线"""
        session_id = IdealCurveDialog(
            self, self._session_repo, SqliteBeanRepository()
        ).result
        if session_id:
            self._load_ideal_session(session_id)

    def _load_ideal_slog(self, path):
        """加载并处理.slog文件作为理想曲线"""
        try:
            data = SlogSerializer.read(path)
        except FileNotFoundError:
            messagebox.showerror("错误", f"文件不存在:\n{path}", parent=self)
            return
        except ValueError as e:
            messagebox.showerror("错误", f"无法加载文件:\n{path}\n{e}", parent=self)
            return

        results = data.get('results', [])
        events = data.get('events', [])

        ideal_data = self._build_ideal_data(
            results, events,
            data.get('heater_initial', 60.0),
            data.get('fan_initial', 50.0),
            source_name=os.path.basename(path),
        )
        if ideal_data is None:
            return

        ideal_data['path'] = path
        self.ideal_data = ideal_data

        # 更新UI
        self.ideal_file_label.config(text=os.path.basename(path))
        self._update_ideal_info()
        self._apply_ideal_curve()

    def _load_ideal_session(self, session_id: str):
        """从数据库加载会话作为理想曲线"""
        session = self._session_repo.load(session_id)
        if not session:
            messagebox.showerror("错误", f"未找到会话: {session_id}", parent=self)
            return

        results = self._result_repo.load(session_id) or []
        events = self._event_repo.load(session_id) or []

        display_name = self._session_repo.get_display_name(session_id)
        ideal_data = self._build_ideal_data(
            results, events,
            session.get('heater_initial', 60.0),
            session.get('fan_initial', 50.0),
            source_name=display_name,
        )
        if ideal_data is None:
            return

        ideal_data['session_id'] = session_id
        self.ideal_data = ideal_data

        # 更新UI
        self.ideal_file_label.config(text=display_name)
        self._update_ideal_info()
        self._apply_ideal_curve()

    def _build_ideal_data(self, results, events, heater_initial, fan_initial,
                          source_name='') -> Optional[dict]:
        """从原始结果和事件构建 ideal_data 字典

        核心处理流程：提取→重采样→平滑→ROR→对齐事件→构建dict。
        被 _load_ideal_slog 和 _load_ideal_session 共用。

        Returns:
            ideal_data dict，数据不足时返回 None
        """
        if not results:
            messagebox.showwarning("警告", "没有有效数据", parent=self)
            return None

        timestamps, temp1, temp2 = extract_valid_data(results)
        if len(timestamps) < 2:
            messagebox.showwarning("警告", "有效数据不足", parent=self)
            return None

        sampling_interval = 1.0
        smooth_window = 15
        smooth_polyorder = 3
        ror_interval = 15.0

        resampled_time, resampled_temp1 = resample_data(timestamps, temp1, sampling_interval)
        _, resampled_temp2 = resample_data(timestamps, temp2, sampling_interval)
        smooth_temp1 = smooth_data(resampled_time, resampled_temp1, smooth_window, smooth_polyorder)
        smooth_temp2 = smooth_data(resampled_time, resampled_temp2, smooth_window, smooth_polyorder)
        ror_time, ror_values = compute_ror(resampled_time, smooth_temp1, sampling_interval, ror_interval)

        # 提取对齐事件时间
        alignment = {}
        for ev_type in ['入豆', '回温', '一爆开始']:
            t_val = 0.0
            for ev in events:
                if ev.get('type') == ev_type:
                    t_val = ev.get('time', 0.0)
                    break
            alignment[ev_type] = t_val

        # 查找阶段边界
        charge_time = alignment.get('入豆', None)
        end_time = None
        for ev in events:
            if ev.get('type') == '烘焙结束':
                end_time = ev.get('time', None)
                break

        return {
            'name': source_name,
            'resampled_time': resampled_time,
            'smooth_temp1': smooth_temp1,
            'smooth_temp2': smooth_temp2,
            'ror_time': ror_time,
            'ror_values': ror_values,
            'events': events,
            'alignment': alignment,
            'charge_time': charge_time if charge_time else 0.0,
            'end_time': end_time,
            'heater_initial': heater_initial,
            'fan_initial': fan_initial,
        }

    def _apply_ideal_curve(self):
        """将 self.ideal_data 传递给统计面板"""
        if self.ideal_data is None:
            return
        self.stats_panel.set_ideal_curve(
            self.ideal_data,
            show_bean=self.ideal_bean_var.get(),
            show_ror=self.ideal_ror_var.get()
        )

    def _clear_ideal_slog(self):
        """清除理想曲线"""
        self.ideal_data = None
        self.ideal_file_label.config(text="未选择")
        self.ideal_info_text.config(state="normal")
        self.ideal_info_text.delete("1.0", "end")
        self.ideal_info_text.config(state="disabled")
        self.stats_panel.clear_ideal_curve()

    def _on_ideal_curve_changed(self):
        """理想曲线显示选项变更"""
        if self.ideal_data is not None:
            self.stats_panel.set_ideal_curve(
                self.ideal_data,
                show_bean=self.ideal_bean_var.get(),
                show_ror=self.ideal_ror_var.get()
            )

    def _update_ideal_info(self):
        """更新理想曲线信息显示"""
        if self.ideal_data is None:
            return
        data = self.ideal_data

        # 找到回温时间作为基准点（0:00）
        turning_time = None
        for ev in data['events']:
            if ev.get('type') == '回温':
                turning_time = ev.get('time', 0)
                break

        # 格式化相对时间（基于回温点）
        def format_relative_time(ev_time):
            if turning_time is None:
                # 没有回温点，回退到绝对时间
                return f"{int(ev_time//60):02d}:{int(ev_time%60):02d}"
            diff = ev_time - turning_time
            abs_min = int(abs(diff) // 60)
            abs_sec = int(abs(diff) % 60)
            if diff < 0:
                return f"-{abs_min:02d}:{abs_sec:02d}"
            else:
                return f"{abs_min:02d}:{abs_sec:02d}"

        # 查找事件时间的温度
        def find_temperature(ev_time):
            rt = data['resampled_time']
            st1 = data['smooth_temp1']
            if rt is not None and st1 is not None and len(rt) > 0:
                idx = np.abs(rt - ev_time).argmin()
                if idx < len(st1):
                    return f"{st1[idx]:.1f}℃"
            return ''

        lines = [
            f"文件: {data['name']}",
            f"数据点: {len(data['resampled_time'])}",
            f"时长: {data['resampled_time'][-1] - data['resampled_time'][0]:.1f}秒",
            f"初始火力: {data.get('heater_initial', '?')}%  初始风门: {data.get('fan_initial', '?')}%",
        ]

        # 按时间排序事件
        sorted_events = sorted(data['events'], key=lambda ev: ev.get('time', 0))

        # 事件信息（时间基于回温点计算）
        for ev in sorted_events:
            ev_type = ev.get('type', '')
            ev_time = ev.get('time', 0)
            temp_str = find_temperature(ev_time)
            rel_time_str = format_relative_time(ev_time)

            if ev_type in ('调整火力', '调整风门'):
                lines.append(
                    f"  {ev_type}: {temp_str} @ {rel_time_str} → {ev.get('value', '?')}%"
                )
            else:
                lines.append(
                    f"  {ev_type}: {temp_str} @ {rel_time_str}"
                )

        # 温度范围
        if data['smooth_temp1'] is not None and len(data['smooth_temp1']) > 0:
            lines.append(f"豆温范围: {float(min(data['smooth_temp1'])):.1f} ~ {float(max(data['smooth_temp1'])):.1f}℃")
        if data['smooth_temp2'] is not None and len(data['smooth_temp2']) > 0:
            lines.append(f"风温范围: {float(min(data['smooth_temp2'])):.1f} ~ {float(max(data['smooth_temp2'])):.1f}℃")

        self.ideal_info_text.config(state="normal")
        self.ideal_info_text.delete("1.0", "end")
        self.ideal_info_text.insert("1.0", "\n".join(lines))
        self.ideal_info_text.config(state="disabled")

    # ═══════════════════════════════════════════════════════════
    # 数据源管理
    # ═══════════════════════════════════════════════════════════

    def _auto_select_first_camera(self):
        """自动检测并选中第一个可用摄像头"""
        self._detect_cameras()
        available = self.source_combo["values"]
        if available:
            self.source_var.set(available[0])
            self._on_source_changed()

    def _detect_cameras(self):
        """检测可用摄像头（DSHOW 索引从 0 起连续，首个失败即停止）"""
        available = []
        self._source_map = {}
        for i in range(10):
            try:
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    break
                ret, _ = cap.read()
                cap.release()
                if not ret:
                    break
                label = f"摄像头 {i}"
                available.append(label)
                self._source_map[label] = i
            except Exception:
                break

        if not available:
            self.source_combo["values"] = ["无可用数据源"]
            self.source_var.set("无可用数据源")
            self.source_combo.configure(state="disabled")
            # 不清除ROI — 缓存后摄像头重连可恢复
            self.start_btn.config(state="disabled")
        else:
            self.source_combo.configure(state="readonly")
            self.source_combo["values"] = available
            sel = self.source_var.get()
            if sel not in available:
                # 旧摄像头已不可用，缓存其ROI
                if hasattr(self, '_current_source') and self.rois:
                    self._save_camera_rois()
                self.source_var.set("")
                self.rois = None
                self.roi_status_var.set("未配置")
                self.start_btn.config(state="disabled")

    def _on_source_changed(self, event=None):
        """数据源切换 — 保存旧摄像头ROI，加载新摄像头的缓存ROI"""
        sel = self.source_var.get()
        if not sel:
            return

        # 保存当前ROI到旧摄像头的缓存
        if hasattr(self, '_current_source') and self.rois:
            self._save_camera_rois()

        self._current_source = self._source_map[sel]

        # 尝试加载新摄像头的缓存ROI
        loaded = self._load_camera_rois()
        if loaded:
            self.rois = loaded
            self.roi_status_var.set("已配置")
            self.start_btn.config(state="normal")
        else:
            self.rois = None
            self.roi_status_var.set("未配置")
            self.start_btn.config(state="disabled")

        self._start_preview()

    def _get_source_label(self):
        """返回当前数据源的友好名称"""
        sel = self.source_var.get()
        return sel if sel else str(self._current_source)

    def _save_camera_rois(self):
        """缓存当前摄像头ROI到磁盘（按摄像头索引持久化）"""
        if hasattr(self, '_current_source') and self.rois:
            self._cache_manager.save_camera_rois(self._current_source, self.rois)

    def _load_camera_rois(self):
        """从磁盘加载当前摄像头的缓存ROI"""
        if hasattr(self, '_current_source'):
            return self._cache_manager.load_camera_rois(self._current_source)
        return None

    # ═══════════════════════════════════════════════════════════
    # 预览循环
    # ═══════════════════════════════════════════════════════════

    def _show_no_data(self):
        """在预览画布上显示"无数据"提示"""
        self._stop_preview()
        cw = self.preview_canvas.winfo_width()
        ch = self.preview_canvas.winfo_height()
        if cw < 10 or ch < 10:
            self._preview_after_id = self.after(200, self._show_no_data)
            return
        self.preview_canvas.delete("all")
        self._no_data_text_id = self.preview_canvas.create_text(
            cw // 2, ch // 2, text="无数据",
            fill="#666666", font=("", 24), anchor="center"
        )

    def _start_preview(self):
        """启动/重启摄像头预览（后台线程读取，UI线程轮询显示）"""
        self._stop_preview()

        # 清除"无数据"和重试文字
        if self._no_data_text_id:
            self.preview_canvas.delete(self._no_data_text_id)
            self._no_data_text_id = None
        self._clear_retry_message()

        if not hasattr(self, '_current_source'):
            self._show_no_data()
            return

        self._preview_lost = False
        self._preview_frame = None
        self._preview_frame_event.clear()
        self._preview_thread_running = True
        self._preview_thread = threading.Thread(
            target=self._preview_read_thread,
            args=(self._current_source,),
            daemon=True
        )
        self._preview_thread.start()
        self._preview_after_id = self.after(30, self._preview_poll)

    def _stop_preview(self):
        """停止预览（等待后台线程释放摄像头）"""
        self._preview_thread_running = False
        if self._preview_after_id:
            self.after_cancel(self._preview_after_id)
            self._preview_after_id = None
        if self._preview_thread and self._preview_thread.is_alive():
            self._preview_thread.join(timeout=2.0)
        self._preview_thread = None
        self._preview_frame = None
        self._preview_frame_event.clear()

    def _preview_read_thread(self, source):
        """后台线程：持续读取摄像头帧，存入 self._preview_frame"""
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self._preview_lost = True
            self._preview_frame_event.set()
            return

        self._cap = cap  # 存为属性，支持从 UI 线程强制 release
        fail_count = 0
        try:
            while self._preview_thread_running:
                _t0 = time.time()
                ret, frame = cap.read()
                elapsed = time.time() - _t0

                if not self._preview_thread_running:
                    break

                if not ret or elapsed > 0.5:
                    fail_count += 1
                    self._preview_fail_count = fail_count
                    if fail_count >= 9:
                        self._preview_lost = True
                    self._preview_frame_event.set()
                    if fail_count >= 9:
                        break
                    time.sleep(0.1)
                    continue

                fail_count = 0
                self._preview_fail_count = 0
                self._preview_frame = frame
                self._preview_frame_event.set()
        finally:
            try:
                cap.release()
            except Exception:
                pass
            self._cap = None

    def _preview_poll(self):
        """UI线程（after回调）：轮询最新帧并显示"""
        if not self._preview_thread_running:
            return
        if self._preview_frame_event.is_set():
            self._preview_frame_event.clear()
            if self._preview_lost:
                self._on_camera_lost()
                return
            fail = self._preview_fail_count
            if fail > 0:
                self._show_retry_message(fail)
                self._preview_after_id = self.after(30, self._preview_poll)
                return
            self._clear_retry_message()
            frame = self._preview_frame
            if frame is None:
                self._preview_after_id = self.after(30, self._preview_poll)
                return
            self._display_preview_frame(frame)
            # ROI框叠加（画在canvas上，不修改帧）
            if self.rois:
                cw = self.preview_canvas.winfo_width()
                ch = self.preview_canvas.winfo_height()
                h, w = frame.shape[:2]
                scale = min(cw / w, ch / h)
                target_w = int(w * scale)
                target_h = int(h * scale)
                ox = (cw - target_w) // 2
                oy = (ch - target_h) // 2
                self.preview_canvas.delete("roi")
                for name, roi in self.rois.items():
                    color = self.extractor.get_roi_color(name)
                    hex_color = '#%02x%02x%02x' % (color[2], color[1], color[0])
                    self.preview_canvas.create_rectangle(
                        ox + roi['x'] * scale, oy + roi['y'] * scale,
                        ox + (roi['x'] + roi['width']) * scale,
                        oy + (roi['y'] + roi['height']) * scale,
                        outline=hex_color, width=2, tags="roi"
                    )
        self._preview_after_id = self.after(30, self._preview_poll)

    def _show_retry_message(self, count):
        """在预览画布上显示重试提示"""
        cw = self.preview_canvas.winfo_width()
        ch = self.preview_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        text = f"信号丢失，获取中...({count}/9)"
        if self._retry_text_id is None:
            # 第一次创建，先清"无数据"文字
            if self._no_data_text_id:
                self.preview_canvas.delete(self._no_data_text_id)
                self._no_data_text_id = None
            self._retry_text_id = self.preview_canvas.create_text(
                cw // 2, ch // 2, text=text,
                fill="#FFA500", font=("", 20), anchor="center"
            )
        else:
            self.preview_canvas.itemconfigure(self._retry_text_id, text=text)

    def _clear_retry_message(self):
        """清除重试提示文字"""
        if self._retry_text_id is not None:
            self.preview_canvas.delete(self._retry_text_id)
            self._retry_text_id = None

    def _on_camera_lost(self):
        """摄像头断开 — 停止预览+处理，缓存ROI供下次使用"""
        # 先停止识别线程
        if self.processing_thread and not self.processing_thread.is_stopped():
            self.processing_thread.stop()
        # 缓存ROI（摄像头断开、重连后自动恢复）
        if self.rois:
            self._save_camera_rois()
        self._stop_preview()
        self._show_no_data()
        self.source_combo.configure(state="disabled")
        self.source_combo["values"] = ["无可用数据源"]
        self.source_var.set("无可用数据源")
        # 不清除ROI — 下次摄像头可用时直接恢复
        self.start_btn.config(state="disabled")
        self.status_var.set("摄像头已断开")
        self._log("摄像头已断开")

    def _display_preview_frame(self, frame_bgr):
        """在预览画布上显示一帧（全部用OpenCV缩放，PIL仅做PhotoImage转换）"""
        cw = self.preview_canvas.winfo_width()
        ch = self.preview_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        # 清除"无数据"和重试文字
        if self._no_data_text_id:
            self.preview_canvas.delete(self._no_data_text_id)
            self._no_data_text_id = None
        self._clear_retry_message()

        h, w = frame_bgr.shape[:2]

        # 直接用OpenCV一次性缩放到目标尺寸（比PIL LANCZOS快得多）
        scale = min(cw / w, ch / h)
        target_w = int(w * scale)
        target_h = int(h * scale)
        frame_small = cv2.resize(frame_bgr, (target_w, target_h), interpolation=cv2.INTER_NEAREST)

        # BGR→RGB，PIL Image，PhotoImage（无额外缩放）
        frame_rgb = cv2.cvtColor(frame_small, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(frame_rgb)
        self._preview_tk_image = ImageTk.PhotoImage(pil)

        if self._preview_img_id is not None:
            self.preview_canvas.delete(self._preview_img_id)

        x = (cw - target_w) // 2
        y = (ch - target_h) // 2
        self._preview_img_id = self.preview_canvas.create_image(
            x, y, anchor="nw", image=self._preview_tk_image
        )

    # ═══════════════════════════════════════════════════════════
    # ROI选择
    # ═══════════════════════════════════════════════════════════

    def _select_roi(self):
        """从当前数据源捕获一帧用于ROI框选"""
        # 识别中：停止并清空数据
        if self.processing_thread and not self.processing_thread.is_stopped():
            if not messagebox.askyesno("确认", "选择ROI将停止当前识别并清空所有数据，是否继续？", parent=self):
                return
            self._stop_realtime()
            self.results.clear()
            self.data_table.clear()
            self.stats_panel.clear_data()
        elif self.results:
            # 停止后有残留数据
            if not messagebox.askyesno("确认", "选择ROI将清空当前数据，是否继续？", parent=self):
                return
            self.results.clear()
            self.data_table.clear()
            self.stats_panel.clear_data()

        # 取预览线程最新帧
        if self._preview_frame is None:
            messagebox.showerror("错误", "无可用预览帧", parent=self)
            return
        frame = self._preview_frame.copy()

        from ui.roi_selector import RoiSelector
        selector = RoiSelector(parent=self, frame=frame)
        rois = selector.get_results()
        if rois:
            self.rois = rois
            self._save_camera_rois()
            self.roi_status_var.set("已配置")
            self.start_btn.config(state="normal")
            self._log(f"ROI选择完成: {len(rois)}个区域")

    # ═══════════════════════════════════════════════════════════
    # 实时处理控制
    # ═══════════════════════════════════════════════════════════

    def _start_realtime(self):
        """开始实时识别"""
        if not self.rois:
            messagebox.showwarning("警告", "请先选择ROI", parent=self)
            return

        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "采样间隔必须大于0", parent=self)
            return

        # 检查残留数据（停止后重新开始）
        if self.results:
            if not messagebox.askyesno("新一轮识别", "开始新一轮识别将清空当前数据，是否继续？", parent=self):
                return
            self.results.clear()
            self.data_table.clear()
            self.stats_panel.clear_data()

        # 更新旋转角度
        try:
            self.extractor.rotation_angle = float(self.rotation_var.get())
        except ValueError:
            self.extractor.rotation_angle = 5
        self.extractor.digit_recognizer = None
        self.extractor._pipeline = None

        # 清空之前的结果
        self.results = []
        self.data_table.clear()
        self.stats_panel.set_results([])
        self.stats_panel.set_update_interval(interval)
        self._reset_status_display()

        # 启动帧缓存会话
        if self._cache.has_session():
            self._cache.clear()
        self._cache.start_writer()
        self.processing_thread = CameraProcessingThread(
            extractor=self.extractor,
            get_frame=lambda: self._preview_frame,
            rois=self.rois,
            interval=interval,
            cache=self._cache
        )

        # 连接信号
        self.processing_thread.result_signal.connect(self._on_result)
        self.processing_thread.status_signal.connect(self._on_status)
        self.processing_thread.finished_signal.connect(self._on_finished)

        # 启动
        self.processing_thread.start()

        # 更新UI
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.stop_btn.config(state="normal")
        self.export_session_btn.config(state="disabled")
        self.save_db_btn.config(state="disabled")
        self._start_time = time.time()
        self._update_elapsed_time()

        self._log(f"开始实时识别，数据源: {self._get_source_label()}，间隔: {interval}s")

    def _pause_realtime(self):
        """暂停/继续"""
        if not self.processing_thread:
            return
        if self.processing_thread.is_paused():
            self.processing_thread.resume()
            self.pause_btn.config(text="暂停")
            self._start_time = time.time() - self._paused_elapsed
        else:
            self.processing_thread.pause()
            self.pause_btn.config(text="继续")
            self._paused_elapsed = time.time() - self._start_time

    def _stop_realtime(self):
        """停止识别"""
        if self.processing_thread:
            self.processing_thread.stop()
        self._reset_buttons()

    def _clear_all_data(self):
        """清空所有已识别的数据（表格 + 图表 + events）"""
        if not self.results:
            return
        if not messagebox.askyesno("确认清空", "确定要清空所有已识别的数据吗？\n此操作不可撤销。", parent=self):
            return
        self.results.clear()
        self.data_table.clear()
        self.stats_panel.clear_data()
        self.save_db_btn.config(state="disabled")
        self.export_session_btn.config(state="disabled")
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.reset_temperature_tracking()
        self._reset_status_display()

    def _reset_buttons(self):
        """重置按钮状态"""
        self.start_btn.config(state="normal" if self.rois else "disabled")
        self.pause_btn.config(state="disabled", text="暂停")
        self.stop_btn.config(state="disabled")
        self.export_session_btn.config(state="normal")
        self.save_db_btn.config(state="normal")

    def _update_elapsed_time(self):
        """更新运行时长显示"""
        if self.processing_thread and not self.processing_thread.is_stopped():
            elapsed = time.time() - self._start_time if hasattr(self, '_start_time') else 0
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            self.time_var.set(f"运行时长: {mins:02d}:{secs:02d}")
            self.after(1000, self._update_elapsed_time)

    # ═══════════════════════════════════════════════════════════
    # 信号处理
    # ═══════════════════════════════════════════════════════════

    def _on_result(self, result):
        """处理识别结果（由后台线程触发，调度到主线程）"""
        self.after_idle(self._on_result_ui, result)

    def _on_result_ui(self, result):
        """在主线程中执行识别结果处理"""
        if not self.winfo_exists():
            return
        self.results.append(result)
        self.data_table.add_row(result)
        self.stats_panel.append_data(result)
        self._update_realtime_status(result)

    def _on_status(self, message):
        """处理状态更新（由后台线程触发，调度到主线程）"""
        self.after_idle(self._on_status_ui, message)

    def _on_status_ui(self, message):
        """在主线程中执行状态更新"""
        if not self.winfo_exists():
            return
        self.status_var.set(message)
        self._log(message)

    def _on_finished(self, success, message):
        """处理完成信号（由后台线程触发，调度到主线程）"""
        self.after_idle(self._on_finished_ui, success, message)

    def _on_finished_ui(self, success, message):
        """在UI线程中执行完成清理"""
        if not self.winfo_exists():
            return
        self._reset_buttons()
        self.status_var.set(message)
        self._log(message)
        self.processing_thread = None


    # ═══════════════════════════════════════════════════════════
    # 其他
    # ═══════════════════════════════════════════════════════════

    def _on_view_frame(self, frame_num, timestamp, data):
        """双击表格行：打开 FrameViewer（cache 模式）"""
        if self._cache and self._cache.cached_count() > 0:
            try:
                FrameViewer(
                    parent=self,
                    extractor=self.extractor,
                    video_path=None,
                    cache_dir=self._cache.session_dir(),
                    rois=self.rois,
                    frame_num=frame_num,
                    timestamp=timestamp,
                    interval=float(self.interval_var.get()),
                    results=data,
                    rotate_angle=float(self.rotation_var.get()),
                )
            except Exception as e:
                messagebox.showerror("错误", f"打开帧查看器失败: {e}", parent=self)
                import traceback
                traceback.print_exc()
            return
        messagebox.showwarning("警告", "没有可用的缓存帧数据", parent=self)

    def _export_session(self):
        """导出当前会话为 .srlog（ZIP 含帧截图 + 结果数据）"""
        if not self._cache or self._cache.cached_count() == 0:
            messagebox.showwarning("警告", "没有可导出的会话数据", parent=self)
            return

        default_name = os.path.basename(self._cache.session_dir().rstrip("/\\")) + ".srlog"
        path = filedialog.asksaveasfilename(
            title="导出会话",
            defaultextension=".srlog",
            initialfile=default_name,
            filetypes=[("会话文件", "*.srlog"), ("所有文件", "*.*")],
            parent=self
        )
        if not path:
            return

        try:
            events = self.stats_panel.events if hasattr(self.stats_panel, 'events') else []
            self._cache.export_as_srlog(
                output_path=path,
                results=self.results,
                rois=self.rois,
                interval=float(self.interval_var.get()),
                rotate_angle=float(self.rotation_var.get()),
                source=self._get_source_label(),
                events=events,
            )
            self._log(f"会话已导出: {path}")
            messagebox.showinfo("导出成功", f"会话已保存到:\n{path}", parent=self)
        except Exception as e:
            messagebox.showerror("导出失败", str(e), parent=self)
            import traceback
            traceback.print_exc()

    def _save_to_database(self):
        """保存当前会话到数据库（is_raw_data=True）"""
        if not self.results:
            messagebox.showwarning("警告", "没有数据可保存", parent=self)
            return

        from datetime import datetime
        from tkinter import simpledialog

        default_name = datetime.now().strftime('%Y%m%d_%H%M%S')
        name = simpledialog.askstring(
            "保存到数据库",
            "请输入本次烘焙的名称:",
            parent=self,
            initialvalue=default_name,
        )
        if not name:
            return

        # 准备数据
        from data.sqlite.session_repo import next_session_id
        sid = next_session_id(self._session_repo.db_path)
        events = self.stats_panel.events if hasattr(self.stats_panel, 'events') else []

        # 保存会话元信息
        session = {
            'session_id': sid,
            'is_raw_data': True,
            'notes': name,
            'heater_initial': (self.stats_panel.heater_initial
                               if hasattr(self.stats_panel, 'heater_initial') else 0),
            'fan_initial': (self.stats_panel.fan_initial
                            if hasattr(self.stats_panel, 'fan_initial') else 0),
        }
        # 原子写入（单个事务）
        writer = SessionWriter(session_repo=self._session_repo,
                               result_repo=self._result_repo,
                               event_repo=self._event_repo)
        try:
            writer.save_full(sid, session, self.results, events)
        except Exception as e:
            self._log(f"保存到数据库失败: {e}")
            messagebox.showerror("保存失败", f"数据库写入错误:\n{e}", parent=self)
            return

        # 保存帧截图
        try:
            cache_dir = self._cache.session_dir() if self._cache else None
            if cache_dir and os.path.isdir(cache_dir):
                target_dir = Paths.ensure_frame_captures(sid)
                count = FileOperations.copy_frames(cache_dir, target_dir)
                self._log(f"帧截图已保存: {target_dir} ({count} 帧)")
        except Exception as e:
            self._log(f"帧截图保存失败: {e}")
            messagebox.showwarning("保存完成", f"数据已保存，但帧截图写入失败:\n{e}", parent=self)

        self.save_db_btn.config(state="disabled")
        self._log(f"已保存到数据库: {sid}")
        messagebox.showinfo("保存成功", f"会话已保存到数据库\n{sid}: {name}", parent=self)

    def _export_data(self):
        """导出数据为.slog（含events）"""
        if not self.results:
            messagebox.showwarning("警告", "没有可导出的数据", parent=self)
            return

        path = filedialog.asksaveasfilename(
            title="导出数据",
            defaultextension=".slog",
            filetypes=[("Slog files", "*.slog"), ("All files", "*.*")],
            parent=self
        )
        if not path:
            return

        session = {
            'results': self.results,
            'events': self.stats_panel.events if hasattr(self.stats_panel, 'events') else [],
            'heater_initial': self.stats_panel.heater_initial if hasattr(self.stats_panel, 'heater_initial') else 0,
            'fan_initial': self.stats_panel.fan_initial if hasattr(self.stats_panel, 'fan_initial') else 0,
        }
        SlogSerializer.write(path, session)
        self._log(f"数据已导出: {path}")

    def _log(self, message):
        """记录日志（转发到父窗口的log方法）"""
        if hasattr(self.parent, 'log'):
            self.parent.log(f"[实时] {message}")

    def destroy(self):
        """重写 destroy：缓存ROI，释放摄像头"""
        # 窗口关闭前持久化ROI
        if self.rois:
            self._save_camera_rois()
        self._stop_preview()
        # 兜底：_stop_preview 超时后线程可能还卡在 cap.read() 中
        if hasattr(self, '_cap') and self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        if self._cache:
            self._cache.stop_writer()
        super().destroy()

    def _on_closing(self):
        """WM_DELETE_WINDOW 协议：处理线程确认 + 关闭"""
        if self.processing_thread and not self.processing_thread.is_stopped():
            if messagebox.askyesno("确认退出", "实时识别正在运行，确定退出吗？", parent=self):
                self.processing_thread.stop()
            else:
                return
        self.destroy()
