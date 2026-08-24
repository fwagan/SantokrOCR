"""
实时识别窗口 — 摄像头实时数字识别与曲线绘制

布局：
- 顶部控制栏：数据源选择、ROI选择、采样间隔、旋转角度
- 主体 PanedWindow：左 预览画布 + 右 Notebook（Tab1 实时曲线 + Tab2 数据表格）
- 中部按钮栏：开始/暂停/停止
- 状态栏
"""

import logging
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from copy import deepcopy
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

from core.camera_capture import CameraProcessingThread
from core.checkpoint import build_checkpoints
from core.ipc_server import IpcServer, load_ipc_config
from core.modbus_config import (
    load_modbus_config,
    resolve_device_port,
    save_modbus_config,
)
from core.modbus_reader import ModbusReader
from core.realtime_cache import RealTimeProcessCache
from core.video_extractor import VideoDigitExtractor
from data.serializers.slog import SlogSerializer
from data.sqlite.bean_repo import SqliteBeanRepository
from data.sqlite.event_repo import SqliteEventRepository
from data.sqlite.result_repo import SqliteResultRepository
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.session_writer import SessionWriter
from data.types import EventType
from ui.data_table import DataTable
from ui.frame_viewer import FrameViewer
from ui.ideal_curve_dialog import IdealCurveDialog
from ui.modbus_config_dialog import ModbusConfigDialog
from ui.slog_comparer import compute_ror, extract_valid_data, resample_data, smooth_data
from ui.statistics_panel import StatisticsPanel
from utils.cache_manager import get_cache_manager
from utils.file_system import FileOperations, Paths
from utils.numeric import find_nearest_temperature
from utils.screen_utils import center_window
from web.backend.config import WebConfigError, main_app_base
from web.backend.launcher import ensure_web_running

logger = logging.getLogger(__name__)

# ── 常量 ──
_PREVIEW_POLL_INTERVAL = 30       # 预览帧轮询间隔（ms）
_PREVIEW_DISCONNECT_THRESHOLD = 9  # 连续读取失败次数上限，超过则判定摄像头断开
_PREVIEW_READ_TIMEOUT = 0.5       # 帧读取超时阈值（秒）
_PREVIEW_RETRY_INTERVAL = 0.1     # 读失败后的重试等待间隔（秒）
_DEFAULT_SAMPLE_INTERVAL = 0.25    # 默认采样间隔（秒）
_EXTRA_RECORD_SECONDS = 5.0       # 额外记录时长（秒）：滚动窗口大小 & 烘焙结束后延迟记录时长
# 探测重试间隔：未识别到设备时后台退避等待，避免紧循环高频占用串口/浪费 CPU
_PROBE_RETRY_INTERVAL = 2.0
_UI_QUEUE_DRAIN_BATCH = 20   # 单次 drain 回调最多处理的 UI 任务条数（防模态阻塞后积压导致硬冻结）


class CameraRealtimeWindow(tk.Toplevel):
    """实时识别窗口"""

    def __init__(self, parent):
        super().__init__(parent)

        self._ui_queue = queue.Queue()
        self._ui_queue_polling = True
        self.after(50, self._drain_ui_queue)

        self.parent = parent

        # 数据源
        self.rois = None
        self.results = []
        self.extractor = VideoDigitExtractor()
        self._data_source = tk.StringVar(value="modbus")  # "camera" | "modbus"

        # ── 烘焙状态机（Web 事件标记） ──
        # "idle"          实时识别未开启
        # "waiting_charge" 实时识别已开启，等待入豆（维持滚动窗口，不记录）
        # "roasting"      已入豆，正常记录
        self._roast_state = "idle"
        self._interval = _DEFAULT_SAMPLE_INTERVAL  # 当前采样间隔（秒）
        self._rolling_window = []            # 等待入豆期间的滚动窗口（5s 数据）
        self._charged_pending = False        # cmd:start 已到达，等待下一采样帧作入豆帧
        self._charge_shift = 0.0             # 入豆后时间轴偏移（烘焙时间 = 原始 - shift）
        self._end_pending_after_id = None    # 烘焙结束后的自动停止定时器
        self._recent_event_keys = []         # M2：add_event 去重（(type, offset)，新会话清空）
        # Web 协作开关（默认关闭，保留原 Modbus 工作流；开启后由 web 主控烘焙会话）
        self._web_enabled = tk.BooleanVar(value=True)  # Web 协作默认勾选（Phase 3 联调确认）
        # IPC（Web 进程通信）
        self._ipc_server = None
        self._latest_result = None           # 最新采样帧（供 get_status 读取，各状态都更新）
        self._turnaround_offset = None       # 回温检测到的 offset（get_status 返回）

        # 线程
        self.processing_thread = None
        self._modbus_reader = None
        self._source = None  # 当前活跃的 TemperatureDataSource
        self._is_exporting = False  # 导出标志，用于阻止导出中关闭窗口
        self._paused_elapsed = 0.0

        # 帧缓存（仅摄像头模式）
        self._cache = RealTimeProcessCache()


        # 持久缓存（摄像头ROI持久化，防止摄像头断开/窗口关闭后丢失）
        self._cache_manager = get_cache_manager()

        # 数据库
        self._session_repo = SqliteSessionRepository()
        self._result_repo = SqliteResultRepository()
        self._event_repo = SqliteEventRepository()

        # 理想曲线
        self.ideal_data = None
        self.ideal_checkpoints = None          # checkpoint 静态列表（build_checkpoints 输出）
        self.ideal_curve_name = ''             # 当前曲线名（get_status 下发给前端比对）

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

        # Modbus 设备探测：仅在模式激活/配置关闭时触发，
        # 未识别到设备时后台持续重试，识别成功后停止；结果经 _enqueue_ui 回主线程
        self._modbus_cfg = {}
        self._modbus_probe_stop = threading.Event()   # 后台探测线程停止标志
        self._modbus_probe_thread = None              # 后台探测线程
        self._modbus_connected = False                # 最近一次探测是否识别到设备（门控开始按钮）

        # 窗口设置（自适应屏幕90%，不超过3200x1900）
        self.title("实时识别 - 有线传输")
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

        # 默认有线传输，仅在摄像头模式时启动预览
        if self._data_source.get() == "camera":
            self._start_preview()
            self._auto_select_first_camera()

        # 后台清理旧缓存会话
        threading.Thread(target=RealTimeProcessCache.cleanup_old_sessions,
                         args=(5,), daemon=True).start()

        # 启动 IPC server（接收 Web 进程命令）
        self._start_ipc_server()

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

        # 数据源切换（有线传输在前，摄像头在后）
        ttk.Label(top_bar, text="数据源:").pack(side="left", padx=(0, 4))
        self._modbus_rb = ttk.Radiobutton(top_bar, text="有线传输",
                                           variable=self._data_source, value="modbus",
                                           command=self._on_data_source_changed)
        self._modbus_rb.pack(side="left", padx=(0, 2))
        self._camera_rb = ttk.Radiobutton(top_bar, text="摄像头",
                                           variable=self._data_source, value="camera",
                                           command=self._on_data_source_changed)
        self._camera_rb.pack(side="left", padx=(0, 8))

        # ── 摄像头特有控件 ──
        self._camera_ctrl_frame = ttk.Frame(top_bar)
        self._camera_ctrl_frame.pack(side="left")

        self._camera_detect_btn = ttk.Button(self._camera_ctrl_frame, text="刷新数据源",
                                              command=self._detect_cameras)
        self._camera_detect_btn.pack(side="left", padx=(0, 2))
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(self._camera_ctrl_frame, textvariable=self.source_var,
                                          state="readonly", width=30)
        self.source_combo.pack(side="left", padx=2)
        self.source_combo.bind("<<ComboboxSelected>>", self._on_source_changed)

        ttk.Button(self._camera_ctrl_frame, text="选择ROI",
                   command=self._select_roi).pack(side="left", padx=8)
        self.roi_status_var = tk.StringVar(value="未配置")
        ttk.Label(self._camera_ctrl_frame, textvariable=self.roi_status_var,
                  width=16, relief="sunken", padding=3).pack(side="left", padx=4)

        # 旋转角度（摄像头特异）
        ttk.Label(self._camera_ctrl_frame, text="旋转角度:").pack(side="left", padx=(12, 4))
        self.rotation_var = tk.StringVar(value="5")
        ttk.Entry(self._camera_ctrl_frame, textvariable=self.rotation_var,
                  width=5).pack(side="left", padx=4)

        # ── Modbus 特有控件 ──
        self._modbus_ctrl_frame = ttk.Frame(top_bar)
        # 默认隐藏，切换到 Modbus 时显示
        self._modbus_ctrl_frame.pack_forget()

        self._modbus_config_btn = ttk.Button(self._modbus_ctrl_frame, text="⚙ 设备配置",
                                             command=self._open_modbus_config)
        self._modbus_config_btn.pack(side="left")

        # Web 协作开关（仅 Modbus 模式；默认勾选由 web 主控烘焙会话，关闭保留原工作流）
        self._web_check = ttk.Checkbutton(self._modbus_ctrl_frame, text="Web事件标记",
                                           variable=self._web_enabled)
        self._web_check.pack(side="left", padx=(8, 0))

        # ── 公共控件（两模式共有） ──
        common_frame = ttk.Frame(top_bar)
        common_frame.pack(side="left", padx=(8, 0))

        # 采样间隔
        ttk.Label(common_frame, text="采样间隔(s):").pack(side="left", padx=(0, 4))
        self.interval_var = tk.StringVar(value=str(_DEFAULT_SAMPLE_INTERVAL))  # UI 默认与常量同步（0.25s）
        ttk.Entry(common_frame, textvariable=self.interval_var, width=6).pack(side="left", padx=4)

        # 操作按钮（start 始终启用，未就绪点开始由各模式报错提示）
        self.start_btn = ttk.Button(common_frame, text="开始实时识别",
                                     command=self._start_realtime, state="normal")
        self.start_btn.pack(side="left", padx=(4, 2))

        # TODO: 暂停键无用，待删除（Web/Modbus 模式下暂停无意义，会话由 start/end 控制）
        self.pause_btn = ttk.Button(common_frame, text="暂停",
                                     command=self._pause_realtime, state="disabled")
        self.pause_btn.pack(side="left", padx=2)

        self.stop_btn = ttk.Button(common_frame, text="停止",
                                    command=self._stop_realtime, state="disabled")
        self.stop_btn.pack(side="left", padx=2)

        self.clear_btn = ttk.Button(common_frame, text="清空已识别数据",
                                     command=self._clear_all_data)
        self.clear_btn.pack(side="left", padx=2)

        self.export_session_btn = ttk.Button(common_frame, text="导出会话",
                                              command=self._export_session, state="disabled")
        self.export_session_btn.pack(side="left", padx=2)

        self.save_db_btn = ttk.Button(common_frame, text="保存会话到数据库",
                                       command=self._save_to_database, state="disabled")
        self.save_db_btn.pack(side="left", padx=2)

        # ── 主体：左侧预览/状态 + 右侧Notebook ──
        main = ttk.PanedWindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=4)
        self._main_pw = main

        # ── 左侧：容器帧（容纳摄像头预览/温度读取器 + 底部实时状态栏）──
        self._left_container = ttk.Frame(main)
        main.add(self._left_container, weight=45)

        # 摄像头预览画布（在 _left_container 内，默认可见）
        self._preview_container = ttk.LabelFrame(self._left_container, text="预览", padding=4)
        self.preview_canvas = tk.Canvas(self._preview_container, bg="#222222", highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True)
        self._preview_container.pack(fill="both", expand=True)

        # 温度读取器状态面板（在 _left_container 内，默认隐藏）
        self._modbus_status_panel = ttk.LabelFrame(self._left_container, text="温度读取器", padding=4)
        self._build_modbus_status_panel(self._modbus_status_panel)

        # 底部实时状态栏（豆温/风温/ROR，在 _left_container 底部，两模式共用）
        self._realtime_status_frame = self._create_realtime_status(self._left_container)
        self._realtime_status_frame.pack(side="bottom", fill="x", padx=4, pady=4)

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
        curve_tab.pack_propagate(False)
        self.notebook.add(curve_tab, text="实时曲线")

        self.stats_panel = StatisticsPanel(curve_tab, is_realtime=True, results=[], figsize=(7, 5))
        self.stats_panel.pack(side="top", fill="both", expand=True)

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

        # 初始状态
        self._on_data_source_changed()

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
    # 数据源切换
    # ═══════════════════════════════════════════════════════════

    def _on_data_source_changed(self):
        """数据源切换：摄像头 ↔ 有线传输，切换UI控件可见性"""
        if self._data_source.get() == "camera":
            # 顶部工具栏：显示摄像头控件
            self._camera_ctrl_frame.pack(side="left", before=self._modbus_ctrl_frame)
            self._modbus_ctrl_frame.pack_forget()

            # 左侧容器：显示预览画布，隐藏温度读取器
            self._modbus_status_panel.pack_forget()
            self._preview_container.pack(fill="both", expand=True)

            # DataTable 恢复 OCR 列
            self.data_table.show_ocr_columns()
            self.data_table.set_view_frame_callback(self._on_view_frame)
            self.export_session_btn.configure(state="normal" if self.results else "disabled")
            self.title("实时识别 - 摄像头")
            self._stop_modbus_probe()
            self._modbus_connected = False  # 切离 Modbus 时清空连接状态，避免切回后按钮提前放开
            self._start_preview()
        else:
            # 顶部工具栏：显示温度读取器控件
            self._modbus_ctrl_frame.pack(side="left", after=self._camera_ctrl_frame)
            self._camera_ctrl_frame.pack_forget()

            # 左侧容器：显示温度读取器，隐藏预览画布
            self._preview_container.pack_forget()
            self._modbus_status_panel.pack(fill="both", expand=True)

            # 停止摄像头预览
            self._stop_preview()
            if self._get_active_source() and not self._get_active_source().is_stopped():
                self._stop_realtime()
            self.data_table.hide_ocr_columns()
            self.data_table.set_view_frame_callback(None)
            self.export_session_btn.configure(state="disabled")
            self.title("实时识别 - 有线传输")
            self._modbus_cfg = load_modbus_config()
            self._update_modbus_status()
            self._start_modbus_probe()

    def _build_modbus_status_panel(self, parent):
        """创建温度读取器状态面板（两行通道状态）"""
        info_frame = ttk.Frame(parent, padding=24)
        info_frame.pack(fill="both", expand=True)

        # 豆温通道状态行
        self._ch1_frame = ttk.Frame(info_frame)
        self._ch1_frame.pack(fill="x", pady=(0, 12))
        self._ch1_indicator = ttk.Label(self._ch1_frame, text="●", font=("", 20))
        self._ch1_indicator.pack(side="left", padx=(0, 12))
        self._ch1_text = ttk.Label(self._ch1_frame, text="未启用 (豆温)",
                                    font=("", 14))
        self._ch1_text.pack(side="left")

        # 风温通道状态行
        self._ch2_frame = ttk.Frame(info_frame)
        self._ch2_frame.pack(fill="x")
        self._ch2_indicator = ttk.Label(self._ch2_frame, text="●", font=("", 20))
        self._ch2_indicator.pack(side="left", padx=(0, 12))
        self._ch2_text = ttk.Label(self._ch2_frame, text="未启用 (风温)",
                                    font=("", 14))
        self._ch2_text.pack(side="left")

    def _start_modbus_probe(self):
        """启动 Modbus 设备探测：后台线程探测，结果经 _enqueue_ui 回主线程

        仅在窗口进入 Modbus 模式 / 配置窗口关闭时调用。未识别到设备时
        后台线程持续重试（覆盖"先开窗口再插设备"），识别成功后自动退出。
        阻塞的串口探测放后台线程，
        避免 COM 口不可用时阻塞 UI 主线程导致窗口假死。
        """
        source = self._get_active_source()
        if source and not source.is_stopped():
            return  # 识别运行中：reader 独占串口，不启动探测
        self._stop_modbus_probe()
        self._modbus_probe_stop = threading.Event()
        # 探测结果未到前不允许开始识别（识别到设备前按钮保持禁用）
        self._update_start_button()
        self._modbus_probe_thread = threading.Thread(
            target=self._modbus_probe_loop, daemon=True, name="ModbusProbe")
        self._modbus_probe_thread.start()

    def _stop_modbus_probe(self):
        """停止 Modbus 设备探测（后台探测线程）"""
        stop = getattr(self, '_modbus_probe_stop', None)
        if stop is not None:
            stop.set()
        self._modbus_probe_thread = None

    def _modbus_probe_loop(self):
        """后台线程：探测 Modbus 设备，识别到后退出，未识别则持续重试

        结果通过 _enqueue_ui 调度到主线程；通道未启用或识别到设备后 break。
        """
        stop = self._modbus_probe_stop
        while not stop.is_set():
            try:
                cfg = self._modbus_cfg or {}
                ch = cfg.get('channels', {}).get('temp1', {})
                if not ch.get('enabled', False):
                    break  # 通道未启用，等配置窗口关闭时重新触发
                port = resolve_device_port(ch)
                if stop.is_set():
                    break  # 探测已被废弃（配置变更/窗口关闭），不再调度结果
                self._enqueue_ui(self._on_modbus_probe_result, port)
                if port is not None:
                    break  # 已识别到设备并成功入队，不再占用串口
            except Exception:
                logger.exception("Modbus 探测循环异常")
            stop.wait(_PROBE_RETRY_INTERVAL)

    def _on_modbus_probe_result(self, port):
        """主线程：应用探测结果，更新状态面板与开始按钮

        探测为单次快照、确认连接即停止，故不显示温度读数（无时效性）；
        实时温度由 ModbusReader 经 _update_modbus_status 读 _latest_result 显示。
        """
        if not self.winfo_exists() or self._data_source.get() != "modbus":
            return  # 窗口已销毁或已切离 Modbus（过期结果）
        cfg = self._modbus_cfg or {}
        ch = cfg.get('channels', {}).get('temp1', {})
        if not ch.get('enabled', False):
            # 通道已被禁用（过期结果）：不写配置、不显示已连接
            self._modbus_connected = False
            self._ch1_indicator.configure(foreground="#888888")
            self._ch1_text.configure(text="未启用 (豆温)")
        elif port:
            self._modbus_connected = True
            # 端口变化时自动更新配置（写回 YAML，供 reader 直接使用）
            if port != ch.get('port', ''):
                new_cfg = deepcopy(cfg)
                new_cfg.setdefault('channels', {}).setdefault('temp1', {})['port'] = port
                save_modbus_config(new_cfg)
                self._modbus_cfg = new_cfg
            self._ch1_indicator.configure(foreground="#44bb44")
            self._ch1_text.configure(text="已连接 (豆温)")
        else:
            self._modbus_connected = False
            self._ch1_indicator.configure(foreground="#ff4444")
            self._ch1_text.configure(text="未连接 (豆温)")
        self._update_start_button()

    def _update_start_button(self):
        """Modbus 模式：仅当通道启用且已识别到设备时才允许开始识别

        按钮可用 ⇒ 后台探测线程已退出（识别成功后 break）⇒ 串口必已释放，
        因此 reader 启动时不会与探测线程抢串口。
        """
        if self._data_source.get() != "modbus":
            return
        source = self._get_active_source()
        if source and not source.is_stopped():
            return  # 识别运行中，按钮状态由 _on_realtime_started 管理
        cfg = self._modbus_cfg or {}
        ch = cfg.get('channels', {}).get('temp1', {})
        if ch.get('enabled', False) and self._modbus_connected:
            self.start_btn.config(state="normal")
        else:
            self.start_btn.config(state="disabled")

    def _update_modbus_status(self):
        """更新温度读取器状态面板（双通道）"""
        _GREEN = "#44bb44"
        _RED = "#ff4444"
        _GRAY = "#888888"

        cfg = self._modbus_cfg or {}
        channels = cfg.get('channels', {})

        def update_channel(frame, indicator, text_label, key, label):
            """更新单个通道的状态显示"""
            ch = channels.get(key, {})
            enabled = ch.get('enabled', False)
            port = ch.get('port', '')

            if enabled and port:
                # 已启用 — 尝试获取最新温度（含等待入豆期间，此时 results 为空）
                temp_str = None
                if key == 'temp1' and self._latest_result:
                    last = self._latest_result
                    t = last.get('temp1_full', '')
                    if t and '?' not in t:
                        temp_str = t

                if temp_str is not None:
                    # 有数据: 已连接
                    indicator.configure(foreground=_GREEN)
                    text_label.configure(text=f"已连接 ({label}) {temp_str}℃")
                else:
                    # 已启用但无数据: 未连接
                    indicator.configure(foreground=_RED)
                    text_label.configure(text=f"未连接 ({label})")
            else:
                # 未启用
                indicator.configure(foreground=_GRAY)
                text_label.configure(text=f"未启用 ({label}) ???.?")

            frame.pack(fill="x", pady=(0, 12) if key == 'temp1' else 0)

        update_channel(self._ch1_frame, self._ch1_indicator, self._ch1_text,
                       'temp1', '豆温')
        update_channel(self._ch2_frame, self._ch2_indicator, self._ch2_text,
                       'temp2', '风温')

    def _open_modbus_config(self):
        """打开 Modbus 设备配置对话框（模态）"""
        # 对话框内部自带串口探测（扫描/验证），先暂停后台自动探测避免抢串口
        self._stop_modbus_probe()
        dlg = ModbusConfigDialog(self)
        self.wait_window(dlg)
        self._modbus_cfg = load_modbus_config()
        # 配置可能变化，重置连接状态后用新配置重新探测
        self._modbus_connected = False
        self._update_modbus_status()
        self._start_modbus_probe()
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

        checkpoints = build_checkpoints(ideal_data)
        if checkpoints is None:
            messagebox.showerror("无法加载理想曲线",
                                 "理想曲线缺少核心事件（入豆/回温），无法生成 checkpoint。",
                                 parent=self)
            return

        ideal_data['path'] = path
        self.ideal_data = ideal_data
        self.ideal_checkpoints = checkpoints
        self.ideal_curve_name = os.path.basename(path)

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

        checkpoints = build_checkpoints(ideal_data)
        if checkpoints is None:
            messagebox.showerror("无法加载理想曲线",
                                 "理想曲线缺少核心事件（入豆/回温），无法生成 checkpoint。",
                                 parent=self)
            return

        ideal_data['session_id'] = session_id
        self.ideal_data = ideal_data
        self.ideal_checkpoints = checkpoints
        self.ideal_curve_name = display_name

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
        self.ideal_checkpoints = None
        self.ideal_curve_name = ''
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
            temp = find_nearest_temperature(data['resampled_time'], data['smooth_temp1'], ev_time)
            if temp is not None:
                return f"{temp:.1f}℃"
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
        self._preview_after_id = self.after(_PREVIEW_POLL_INTERVAL, self._preview_poll)

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

                if not ret or elapsed > _PREVIEW_READ_TIMEOUT:
                    fail_count += 1
                    self._preview_fail_count = fail_count
                    if fail_count >= _PREVIEW_DISCONNECT_THRESHOLD:
                        self._preview_lost = True
                    self._preview_frame_event.set()
                    if fail_count >= _PREVIEW_DISCONNECT_THRESHOLD:
                        break
                    time.sleep(_PREVIEW_RETRY_INTERVAL)
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
                self._preview_after_id = self.after(_PREVIEW_POLL_INTERVAL, self._preview_poll)
                return
            self._clear_retry_message()
            frame = self._preview_frame
            if frame is None:
                self._preview_after_id = self.after(_PREVIEW_POLL_INTERVAL, self._preview_poll)
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
        self._preview_after_id = self.after(_PREVIEW_POLL_INTERVAL, self._preview_poll)

    def _show_retry_message(self, count):
        """在预览画布上显示重试提示"""
        cw = self.preview_canvas.winfo_width()
        ch = self.preview_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        text = f"信号丢失，获取中...({count}/{_PREVIEW_DISCONNECT_THRESHOLD})"
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

    def _get_active_source(self):
        """返回当前活跃的 TemperatureDataSource 实例"""
        if self._data_source.get() == "camera":
            return self.processing_thread
        else:
            return self._modbus_reader

    def _start_realtime(self):
        """开始实时识别（摄像头或 Modbus）"""
        if self._data_source.get() == "camera":
            self._start_realtime_camera()
        else:
            self._start_realtime_modbus()

    def _start_realtime_camera(self):
        """摄像头模式：启动 CameraProcessingThread"""
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

        # 检查残留数据
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
        self._reset_for_new_session(interval)

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
        self._source = self.processing_thread

        # 连接信号
        self._source.result_signal.connect(self._on_result)
        self._source.status_signal.connect(self._on_status)
        self._source.finished_signal.connect(self._on_finished)

        # 启动
        self._source.start()

        self._on_realtime_started(interval, self._get_source_label())

    def _start_realtime_modbus(self):
        """Modbus 模式：启动 ModbusReader"""
        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "采样间隔必须大于0", parent=self)
            return

        if self.results:
            if not messagebox.askyesno("新一轮识别", "开始新一轮识别将清空当前数据，是否继续？", parent=self):
                return
            self.results.clear()
            self.data_table.clear()
            self.stats_panel.clear_data()

        self._reset_for_new_session(interval)

        # 创建 ModbusReader
        cfg = self._modbus_cfg or load_modbus_config()
        ch = cfg.get('channels', {}).get('temp1', {})
        if not ch.get('enabled', False):
            messagebox.showwarning("警告", "温度读取器未配置，请先进行设备配置", parent=self)
            return

        self._modbus_reader = ModbusReader(
            temp1_config=ch,
            temp2_config=None,
            interval=interval,
        )
        self._source = self._modbus_reader

        self._source.result_signal.connect(self._on_result)
        self._source.status_signal.connect(self._on_status)
        self._source.finished_signal.connect(self._on_finished)

        self._source.start()

        # Web 协作开启：进入等待入豆，不记录，维持 5s 滚动窗口，等 cmd:start
        # Web 协作关闭：保持原 Modbus 工作流，立即正常记录
        self._roast_state = ("waiting_charge" if self._web_enabled.get() else "idle")

        # Web 事件标记勾选时自动启动 Web 进程（后台线程，不阻塞 UI；已运行则跳过）
        if self._web_enabled.get():
            self._ensure_web_started()

        self._on_realtime_started(interval, f"Modbus ({ch.get('port', '?')})")

    def _reset_for_new_session(self, interval):
        """清空数据 + 重置状态，供两种模式共用"""
        self._interval = interval
        self._reset_web_state()
        self.results = []
        self.data_table.clear()
        self.stats_panel.set_results([])
        self.stats_panel.set_update_interval(interval)
        self._reset_status_display()

    def _on_realtime_started(self, interval, source_label):
        """开始实时识别后的UI更新，供两种模式共用"""
        self._stop_modbus_probe()  # 释放 COM 口给 ModbusReader
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.stop_btn.config(state="normal")
        self.export_session_btn.config(state="disabled")
        self.save_db_btn.config(state="disabled")
        # 运行中禁用数据源切换与设备配置（reader 独占串口，探测/配置都会与之冲突）
        self._camera_rb.configure(state="disabled")
        self._modbus_rb.configure(state="disabled")
        self._modbus_config_btn.configure(state="disabled")
        self._start_time = time.time()
        self._update_elapsed_time()
        self._log(f"开始实时识别，数据源: {source_label}，间隔: {interval}s")

    def _pause_realtime(self):
        """暂停/继续"""
        source = self._get_active_source()
        if not source:
            return
        if source.is_paused():
            source.resume()
            self.pause_btn.config(text="暂停")
            self._start_time = time.time() - self._paused_elapsed
        else:
            source.pause()
            self.pause_btn.config(text="继续")
            self._paused_elapsed = time.time() - self._start_time

    def _stop_realtime(self):
        """停止识别"""
        source = self._get_active_source()
        if source:
            source.stop()
        self._reset_buttons()

    def _clear_all_data(self):
        """清空所有已识别的数据（表格 + 图表 + events）"""
        # Web 主控中（roasting）禁止清空，仅 waiting_charge/idle 可用
        if self._roast_state == "roasting":
            return
        if not self.results:
            return
        if not messagebox.askyesno("确认清空", "确定要清空所有已识别的数据吗？\n此操作不可撤销。", parent=self):
            return
        self.results.clear()
        self.data_table.clear()
        self.stats_panel.clear_data()
        self.save_db_btn.config(state="disabled")
        self.export_session_btn.config(state="disabled")
        self._reset_web_state()
        source = self._get_active_source()
        if source and not source.is_stopped():
            source.reset_temperature_tracking()
        self._reset_status_display()

    def _reset_buttons(self):
        """重置按钮状态"""
        if self._data_source.get() == "camera":
            self.start_btn.config(state="normal" if self.rois else "disabled")
        else:
            self._update_start_button()
        self.pause_btn.config(state="disabled", text="暂停")
        self.stop_btn.config(state="disabled")
        self.clear_btn.config(state="normal")   # 恢复清空按钮（Web 烘焙中被禁用，停止后恢复）
        # 导出仅摄像头模式可用
        if self._data_source.get() == "camera":
            self.export_session_btn.config(state="normal" if self.results else "disabled")
        else:
            self.export_session_btn.config(state="disabled")
        # 停止后恢复数据源切换与设备配置
        self._camera_rb.configure(state="normal")
        self._modbus_rb.configure(state="normal")
        self._modbus_config_btn.configure(state="normal")
        self.save_db_btn.config(state="normal" if self.results else "disabled")

    def _update_elapsed_time(self):
        """更新运行时长显示"""
        source = self._get_active_source()
        if source and not source.is_stopped():
            elapsed = time.time() - self._start_time if hasattr(self, '_start_time') else 0
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            self.time_var.set(f"运行时长: {mins:02d}:{secs:02d}")
            self.after(1000, self._update_elapsed_time)

    # ═══════════════════════════════════════════════════════════
    # 信号处理
    # ═══════════════════════════════════════════════════════════

    def _enqueue_ui(self, func, *args):
        """从后台线程安全地把一次 UI 调用排入主线程队列（替代 after_idle）"""
        q = self._ui_queue
        if q is None:
            return  # 窗口已销毁，丢弃
        q.put((func, args))

    def _drain_ui_queue(self):
        """主线程轮询：取出并执行后台线程排入的 UI 任务"""
        if not self._ui_queue_polling:
            return
        try:
            # 每次回调分配处理，未清空则立即续排，
            # 避免单个回调清空积压导致长时间不返回事件循环（模态弹窗阻塞后的硬冻结）。
            for _ in range(_UI_QUEUE_DRAIN_BATCH):
                func, args = self._ui_queue.get_nowait()
                try:
                    func(*args)
                except Exception:
                    self._log(f"UI 调度任务异常: {traceback.format_exc()}")
        except queue.Empty:
            pass
        if not self._ui_queue.empty():
            self.after(0, self._drain_ui_queue)
            return
        self.after(50, self._drain_ui_queue)

    def _on_result(self, result):
        """处理识别结果（由后台线程触发，调度到主线程）"""
        self._enqueue_ui(self._on_result_ui, result)

    def _on_result_ui(self, result):
        """在主线程中执行识别结果处理

        按烘焙状态机分派：
        - waiting_charge：不记录，维持滚动窗口；收到入豆命令后下一帧作入豆帧
        - roasting：记录到表格/曲线（时间轴映射到烘焙时间）
        - 其他（摄像头模式等）：原有直接记录行为
        """
        if not self.winfo_exists():
            return
        self._latest_result = result
        self._update_realtime_status(result)

        if self._roast_state == "waiting_charge":
            if self._charged_pending:
                self._process_charge(result)
            else:
                self._update_rolling_window(result)
            if self._data_source.get() == "modbus":
                self._update_modbus_status()
            return

        if self._roast_state == "roasting":
            r = self._remap_result(result, len(self.results))
            self.results.append(r)
            self.data_table.add_row(r)
            self.stats_panel.append_data(r)
            self._update_turnaround_cache()
            if self._data_source.get() == "modbus":
                self._update_modbus_status()
            return

        # 其他状态（摄像头模式）：原有直接记录行为
        self.results.append(result)
        self.data_table.add_row(result)
        self.stats_panel.append_data(result)
        if self._data_source.get() == "modbus":
            self._update_modbus_status()

    def _update_rolling_window(self, result):
        """等待入豆期间维护滚动窗口：追加当前帧，保留约 5s 数据"""
        self._rolling_window.append(result)
        max_frames = max(1, int(round(
            _EXTRA_RECORD_SECONDS / max(self._interval, 0.001))))
        if len(self._rolling_window) > max_frames:
            self._rolling_window = self._rolling_window[-max_frames:]

    def _process_charge(self, charge_result):
        """入豆处理：填充帧 + 窗口数据 + 入豆帧 → 完整烘焙时间轴 0~5s

        入豆帧为收到 cmd:start 后的下一次采样帧，位于烘焙时间轴 _EXTRA_RECORD_SECONDS。
        滚动窗口不足 _EXTRA_RECORD_SECONDS（入豆过早）时，前面空缺的时间轴用
        首个有效帧温度填充，保证时间轴始终从 0 开始、入豆帧恒在 _EXTRA_RECORD_SECONDS
        （edge case）。
        """
        window = list(self._rolling_window)
        self._rolling_window = []
        self._charged_pending = False

        # 烘焙时间轴偏移：入豆帧 → 额外记录时长，则 烘焙时间 = 原始时间 - 偏移
        charge_real = charge_result['timestamp']
        shift = charge_real - _EXTRA_RECORD_SECONDS
        self._charge_shift = shift

        # 组装帧：填充帧 + 窗口数据 + 入豆帧
        interval = max(self._interval, 0.001)
        full_window_frames = max(1, int(round(_EXTRA_RECORD_SECONDS / interval)))
        fill_count = full_window_frames - len(window)

        frames = []
        if fill_count > 0:
            # 填充温度取首个有效帧：窗口内第一个 temp1_full 干净（非 '?'）的帧，
            # 全部无效或窗口为空则用入豆帧。
            fill_src = charge_result
            for f in window:
                t1 = f.get('temp1_full')
                if t1 and '?' not in t1:
                    fill_src = f
                    break
            fill_temp1 = fill_src.get('temp1_full', '0.0')
            fill_temp2 = fill_src.get('temp2', '0.0')
            for i in range(fill_count):
                frames.append(
                    self._build_fill_frame(i * interval, shift, fill_temp1, fill_temp2))
        frames.extend(window)
        frames.append(charge_result)

        for i, r in enumerate(frames):
            nr = self._remap_result(r, i)
            self.results.append(nr)
            self.data_table.add_row(nr)
            self.stats_panel.append_data(nr)

        # 入豆事件（烘焙时间轴 _EXTRA_RECORD_SECONDS）
        self._add_event_from_web(EventType.CHARGE, _EXTRA_RECORD_SECONDS, None)
        self._roast_state = "roasting"
        # Web 模式下入豆后禁用停止/暂停/清空按钮：唯一停止路径是 cmd:end 自动停止
        # （避免命令校验与生效之间状态被"停止"改动）
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")
        self.clear_btn.config(state="disabled")
        self._log(f"[Web] 入豆 @ {_EXTRA_RECORD_SECONDS:.0f}s，窗口 {len(window)} 帧"
                  f"（填充 {fill_count} 帧）")
        if self._data_source.get() == "modbus":
            self._update_modbus_status()

    def _build_fill_frame(self, roast_ts, shift, temp1_full, temp2):
        """构造填充帧

        timestamp 用原始坐标系（+shift），使经过 _remap_result 后落在烘焙时间 roast_ts；
        用于窗口不足 _EXTRA_RECORD_SECONDS 时补齐时间轴。frame 由 _remap_result 计算。
        """
        raw_ts = round(roast_ts + shift, 3)
        return {
            'timestamp': raw_ts,
            'original_timestamp': raw_ts,
            'time_str': '',
            'temp1_full': temp1_full,
            'temp1_normal': '',
            'temp1_faulty_digit': -9,
            'temp2': temp2,
            'abnormal_category': None,
        }

    def _remap_result(self, result, frame):
        """将原始采样帧映射到烘焙时间轴（偏移 _charge_shift），返回新 dict

        帧号用连续索引（frame 参数），保证唯一——满足 result 表
        UNIQUE(session_id, frame) 约束。事件帧号独立按烘焙时间计算，不关联结果帧。
        """
        r = dict(result)
        ts = round(result['timestamp'] - self._charge_shift, 3)
        # 采样节奏抖动导致窗口首帧可能略负（如 -0.006），归零避免首帧时间为负
        if ts < 0:
            ts = 0.0
        r['timestamp'] = ts
        r['original_timestamp'] = round(result['timestamp'], 3)
        r['frame'] = frame
        r['time_str'] = (
            f"{int(ts // 60):02d}:{int(ts % 60):02d}:{int((ts % 1) * 1000):03d}"
        )
        return r

    def _compute_event_base(self):
        """事件基准 = 入豆事件在当前坐标系的时间

        Web 实时模式入豆恒在烘焙时间轴 _EXTRA_RECORD_SECONDS（stats_panel 的
        exclude_outside_var 在实时模式恒为 False：checkbox 未创建、无法切换，
        resampled_time 恒不重基）；用入豆事件当前坐标而非硬编码，防御未来重基
        逻辑改动。供 get_temp（offset→烘焙时间）与 _update_turnaround_cache 共享，
        防两路漂移。
        """
        base = _EXTRA_RECORD_SECONDS
        # list() 对 list 在 GIL 下原子复制，读到的是某个一致快照；
        # 主线程 process_data 可能同时 append/整表重赋值，取到旧快照属预期
        # （base 兜底 _EXTRA_RECORD_SECONDS，下一帧 status 查询即刷新）。
        events = list(self.stats_panel.events)
        for ev in events:
            if ev.get('type') == EventType.CHARGE:
                base = float(ev.get('time', _EXTRA_RECORD_SECONDS))
                break
        return base

    def _update_turnaround_cache(self):
        """检测 stats_panel 中的回温事件，更新 offset 缓存（供 get_status 返回）

        基准用事件列表中"入豆"事件的当前坐标，而非硬编码——
        stats_panel 勾选"排除阶段外数据"时会把事件重基到 0，硬编码会导致偏移偏小。
        """
        base = self._compute_event_base()
        for ev in reversed(self.stats_panel.events):
            if ev.get('type') == EventType.TURNAROUND:
                offset = round(float(ev.get('time', 0)) - base, 3)
                # 回温只可能在入豆之后发生，offset 必须 >= 0；
                # 过滤负值（如填充帧恒定温度被回温算法误判），避免 Web 端收到负数时间
                if offset >= 0:
                    self._turnaround_offset = offset
                    return
        self._turnaround_offset = None

    def _on_status(self, message):
        """处理状态更新（由后台线程触发，调度到主线程）"""
        self._enqueue_ui(self._on_status_ui, message)

    def _on_status_ui(self, message):
        """在主线程中执行状态更新"""
        if not self.winfo_exists():
            return
        self.status_var.set(message)
        self._log(message)

    def _on_finished(self, success, message):
        """处理完成信号（由后台线程触发，调度到主线程）"""
        self._enqueue_ui(self._on_finished_ui, success, message)

    def _on_finished_ui(self, success, message):
        """在UI线程中执行完成清理"""
        if not self.winfo_exists():
            return
        self._reset_buttons()
        self.status_var.set(message)
        self._log(message)
        if self._data_source.get() == "camera":
            self.processing_thread = None
        else:
            self._modbus_reader = None
            self._update_modbus_status()
            self._update_start_button()  # 设备未变，按上次探测结果恢复按钮，不重新探测
        self._source = None
        self._roast_state = "idle"
        self._reset_web_state()

    # ═══════════════════════════════════════════════════════════
    # IPC server（Web 进程通信）
    # ═══════════════════════════════════════════════════════════

    def _start_ipc_server(self):
        """启动 IPC server 后台线程，配置缺失/非法或绑定失败时提示用户"""
        if self._ipc_server is not None:
            return
        try:
            cfg = load_ipc_config()
        except WebConfigError as e:
            messagebox.showerror("IPC 服务配置错误",
                                 f"{e}\n\nWeb 事件标记功能不可用。",
                                 parent=self)
            return
        self._ipc_server = IpcServer(handler=self._ipc_handler,
                                     host=cfg['host'], port=cfg['port'])
        self._ipc_server.start(on_bind_error=self._on_ipc_bind_error)

    def _on_ipc_bind_error(self, exc):
        """IPC 端口绑定失败（在 IpcServer 线程中调用）"""
        # 先捕获 port 到局部变量，避免窗口关闭后 _ipc_server 已置 None 时空引用
        port = self._ipc_server.port if self._ipc_server is not None else '?'
        self._enqueue_ui(self._show_ipc_bind_error, port, exc)

    def _show_ipc_bind_error(self, port, exc):
        """主线程执行：弹窗提示 IPC 端口绑定失败"""
        messagebox.showerror(
            "IPC 服务启动失败",
            f"无法监听端口 {port}（可能被占用）：\n{exc}",
            parent=self)

    def _stop_ipc_server(self):
        if self._ipc_server is not None:
            self._ipc_server.stop()
            self._ipc_server = None

    # ═══════════════════════════════════════════════════════════
    # Web 进程自动启动（realtime + web 勾选时便利启动，不监控不重启）
    # ═══════════════════════════════════════════════════════════

    def _ensure_web_started(self) -> None:
        """后台拉起 Web 进程（不等待、不做结果检测——
        启动后 Web 窗口自会显示，用户自行确认状态；结果经独立 queue 回主线程记日志）"""
        q = queue.Queue()
        self.after(200, self._poll_web_msg, q)
        def worker() -> None:
            _, msg = ensure_web_running(main_app_base())
            q.put(msg)   # 线程安全，不直接操作 tkinter
        threading.Thread(target=worker, daemon=True,
                         name="WebAutoStart").start()

    def _poll_web_msg(self, q: queue.Queue) -> None:
        """主线程轮询本轮 Web 启动结果消息；取到即记日志并停止（每会话一轮）"""
        if not self.winfo_exists():
            return
        try:
            msg = q.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_web_msg, q)   # 消息未到，继续等
            return
        self._log(f"[Web] {msg}")

    # ── 命令分发 ──

    def _ipc_handler(self, cmd):
        """IPC 命令分发（在 IpcServer 后台线程中调用）

        返回响应 dict；这里只做分发与基础校验，UI 更新通过 _enqueue_ui 调度到主线程。
        """
        if not isinstance(cmd, dict):
            return {"ok": False, "error": "invalid command"}
        name = cmd.get('cmd')
        if name == 'get_checkpoints':
            return self._ipc_get_checkpoints()
        if name == 'get_status':
            return self._ipc_get_status()
        if name == 'get_temp':
            return self._ipc_get_temp(cmd)
        if name == 'start':
            return self._ipc_start(cmd)
        if name == 'add_event':
            return self._ipc_add_event(cmd)
        if name == 'add_value_event':
            return self._ipc_add_value_event(cmd)
        if name == 'end':
            return self._ipc_end(cmd)
        return {"ok": False, "error": f"unknown command: {name}"}

    def _ipc_get_status(self):
        """get_status：返回当前温度/状态/回温 offset"""
        latest = self._latest_result
        temp1 = temp2 = ror = None
        if latest is not None:
            t1 = latest.get('temp1_full', '')
            if t1 and '?' not in t1:
                try:
                    temp1 = round(float(t1), 1)
                except ValueError:
                    pass
            t2 = latest.get('temp2', '')
            # Modbus 预留通道恒为 "0.0"，视为无风温（避免 Web 端显示假 0℃）
            if t2 and '?' not in t2 and t2 != '0.0':
                try:
                    temp2 = round(float(t2), 1)
                except ValueError:
                    pass
        if self._roast_state == "roasting" and (
                self.stats_panel.ror_values is not None
                and len(self.stats_panel.ror_values) > 0):
            ror = round(float(self.stats_panel.ror_values[-1]), 1)
        return {
            'temp1': temp1,
            'temp2': temp2,
            'ror': ror,
            'state': self._roast_state,
            'turnaround_offset': self._turnaround_offset,
            'curve_name': self.ideal_curve_name,
        }

    def _ipc_get_checkpoints(self):
        """get_checkpoints：返回 checkpoint 静态列表（未加载理想曲线时为 null）"""
        return {'checkpoints': self.ideal_checkpoints}

    def _ipc_get_temp(self, cmd):
        """get_temp：返回 offset（相对入豆秒）时刻的重采样豆温估算

        offset 对应的具体时刻未必落在实际采样点上，用 stats_panel 的
        resampled_time/resampled_temp1（等间隔线性插值）经 np.interp 估算。
        数据不足/超范围返回 temp1=null（合法状态，前端显示 '--'），仅参数非法走 ok:false。
        """
        offset = cmd.get('offset')
        if offset is None:
            return {"ok": False, "error": "缺少 offset"}
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            return {"ok": False, "error": "offset 必须是数值"}
        if not np.isfinite(offset) or offset < 0:
            return {"ok": False, "error": "offset 必须是非负有限数值"}

        query_t = self._compute_event_base() + offset

        # resampled 数组由 process_data 整表重建后整体赋值（非原地改写），
        # 后台线程此处读取拿到一致快照；勿改为原地修改 resampled_time/resampled_temp1。
        # rt/rv 是两次独立属性读取，可能因线程交错来自不同代（长度不同）→
        # np.interp 抛 ValueError，故加长度一致性守卫，失败返回 null（前端 '--'）。
        rt = self.stats_panel.resampled_time
        rv = self.stats_panel.resampled_temp1
        if (rt is None or rv is None or len(rt) < 2
                or len(rv) == 0 or len(rt) != len(rv)):
            return {"ok": True, "temp1": None}

        # 超范围（查询点超出当前数据时间轴）对齐契约返回 null，而非钳制端点值
        if query_t < rt[0] or query_t > rt[-1]:
            return {"ok": True, "temp1": None}

        temp = float(np.interp(query_t, rt, rv))
        if not np.isfinite(temp):
            return {"ok": True, "temp1": None}
        return {"ok": True, "temp1": round(temp, 1)}

    def _ipc_start(self, cmd):
        """start：入豆。记录初始火力/风门，标记等待下一帧作入豆帧"""
        if self._roast_state == "roasting":
            # M1：已入豆（首次 start 已生效），目标已达成，视为成功（响应丢失重试场景）
            return {"ok": True}
        if self._roast_state != "waiting_charge":
            return {"ok": False, "error": f"当前状态不允许入豆: {self._roast_state}"}
        heater = cmd.get('heater_initial')
        fan = cmd.get('fan_initial')
        try:
            heater = float(heater) if heater is not None else None
            fan = float(fan) if fan is not None else None
        except (TypeError, ValueError):
            return {"ok": False, "error": "heater_initial/fan_initial 必须是数值"}
        # 纵深防御：数值范围 0-100（前端已校验，此处防任意 Web 客户端）
        for name, val in (('heater_initial', heater), ('fan_initial', fan)):
            if val is not None and not (0 <= val <= 100):
                return {"ok": False, "error": f"{name} 必须在 0-100 之间"}
        self._enqueue_ui(self._apply_charge_start, heater, fan)
        return {"ok": True}

    def _ipc_add_event(self, cmd):
        """add_event：标记一次性事件（一爆开始/结束、二爆开始/结束）"""
        return self._handle_ipc_event(cmd, is_value_event=False)

    def _ipc_add_value_event(self, cmd):
        """add_value_event：标记带数值事件（调整火力/调整风门）"""
        return self._handle_ipc_event(cmd, is_value_event=True)

    def _ipc_end(self, cmd):
        """end：烘焙结束。记录事件后继续记录 5 秒自动停止"""
        if self._roast_state != "roasting":
            return {"ok": False, "error": f"当前状态不允许结束: {self._roast_state}"}
        if self._end_pending_after_id is not None:
            # M1：end 已处理过，目标已达成，视为成功（前端重试场景）
            return {"ok": True}
        ev = cmd.get('event') or {}
        offset = ev.get('offset')
        if offset is None:
            return {"ok": False, "error": "缺少 offset"}
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            return {"ok": False, "error": "offset 必须是数值"}
        roast_time = _EXTRA_RECORD_SECONDS + offset
        self._enqueue_ui(self._apply_end_from_web, roast_time)
        return {"ok": True}

    def _handle_ipc_event(self, cmd, is_value_event):
        """add_event / add_value_event 公共处理"""
        if self._roast_state != "roasting":
            return {"ok": False, "error": f"当前状态不允许标记事件: {self._roast_state}"}
        ev = cmd.get('event') or {}
        ev_type = ev.get('type')
        offset = ev.get('offset')
        if offset is None:
            return {"ok": False, "error": "缺少 offset"}
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            return {"ok": False, "error": "offset 必须是数值"}

        valid_types = {EventType.FC_START, EventType.FC_END,
                       EventType.SC_START, EventType.SC_END}
        if is_value_event:
            valid_types |= {EventType.HEATER_ADJUST, EventType.FAN_ADJUST}
        if ev_type not in valid_types:
            return {"ok": False, "error": f"不支持的事件类型: {ev_type}"}

        value = ev.get('value')
        if is_value_event:
            if value is None:
                return {"ok": False, "error": "缺少 value"}
            try:
                value = float(value)
            except (TypeError, ValueError):
                return {"ok": False, "error": "value 必须是数值"}
            # 纵深防御：数值范围 0-100（前端已校验，此处防任意 Web 客户端）
            if not (0 <= value <= 100):
                return {"ok": False, "error": f"{ev_type}值必须在 0-100 之间"}
        else:
            value = None

        # M2 去重：同一 (type, offset) 视为重试重放，静默忽略
        # （offset 相对固定 T0 单调递增，合法事件不可能同 offset；重试重发同一冻结 payload 才会撞）
        key = (ev_type, offset)
        if key in self._recent_event_keys:
            return {"ok": True}
        self._recent_event_keys.append(key)
        if len(self._recent_event_keys) > 200:
            self._recent_event_keys = self._recent_event_keys[-100:]

        roast_time = _EXTRA_RECORD_SECONDS + offset
        self._enqueue_ui(self._add_event_from_web, ev_type, roast_time, value)
        return {"ok": True}

    # ── 主线程执行的操作（_enqueue_ui 调度） ──

    def _apply_charge_start(self, heater, fan):
        """cmd:start 的主线程处理：设置初始值，标记入豆待定"""
        if self._roast_state != "waiting_charge":
            return
        if heater is not None and fan is not None:
            self.stats_panel.set_heater_fan_initial(heater, fan)
        self._charged_pending = True
        self._log(f"[Web] 入豆请求已收到，等待下一采样帧作入豆帧")

    def _add_event_from_web(self, ev_type, roast_time, value):
        """在主线程添加一个来自 Web 端的事件"""
        if not self.winfo_exists():
            return
        # 事件 frame 按烘焙时间算相对帧号（不关联结果帧，modbus 模式下无实际用途）
        frame = int(round(roast_time / max(self._interval, 0.001)))
        event = {
            'type': ev_type,
            'frame': frame,
            'time': roast_time,
            'value': value,
        }
        self.stats_panel.add_event(event)
        self._log(f"[Web] 事件: {ev_type} @ {roast_time:.2f}s")

    def _apply_end_from_web(self, roast_time):
        """cmd:end 的主线程处理：记录事件，安排 5 秒后自动停止

        以 _end_pending_after_id 为锁，防止重复 end 产生重复事件。
        """
        if not self.winfo_exists():
            return
        if self._end_pending_after_id is not None:
            return  # 已处理过，忽略重复
        self._add_event_from_web(EventType.ROAST_END, roast_time, None)
        delay_ms = int(_EXTRA_RECORD_SECONDS * 1000)
        self._end_pending_after_id = self.after(delay_ms, self._auto_stop_after_end)
        self._log(f"[Web] 烘焙结束 @ {roast_time:.2f}s，"
                  f"{_EXTRA_RECORD_SECONDS:.0f} 秒后自动停止")

    def _auto_stop_after_end(self):
        """烘焙结束后继续记录 5 秒，自动停止"""
        self._end_pending_after_id = None
        self._stop_realtime()
        self._log("[Web] 已自动停止（烘焙结束）")

    def _reset_web_state(self):
        """重置 Web 端相关的临时状态"""
        self._rolling_window = []
        self._charged_pending = False
        self._charge_shift = 0.0
        self._turnaround_offset = None
        self._latest_result = None
        self._recent_event_keys.clear()      # 新会话：清掉上一会话的事件去重键
        if self._end_pending_after_id is not None:
            self.after_cancel(self._end_pending_after_id)
            self._end_pending_after_id = None

    # ═══════════════════════════════════════════════════════════
    # 其他
    # ═══════════════════════════════════════════════════════════

    def _on_view_frame(self, frame_num, timestamp, data):
        """双击表格行：打开 FrameViewer（仅摄像头模式有效）"""
        if self._data_source.get() != "camera":
            return
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
        """导出当前会话为 .srlog（ZIP 含帧截图 + 结果数据，仅摄像头模式）"""
        if self._data_source.get() != "camera":
            return
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

        # 在后台线程中执行导出，避免 UI 假死
        # 先获取所有 UI 状态的快照（后台线程不可访问 tkinter 变量）
        results = list(self.results)
        rois = dict(self.rois) if self.rois else {}
        events = list(self.stats_panel.events) if hasattr(self.stats_panel, 'events') else []
        interval = float(self.interval_var.get())
        rotate_angle = float(self.rotation_var.get())
        source_label = self._get_source_label()

        # 模态进度弹窗
        progress_win = tk.Toplevel(self)
        progress_win.title("导出会话")
        progress_win.transient(self)
        progress_win.grab_set()
        progress_win.resizable(False, False)

        ttk.Label(progress_win, text="正在导出会话，请稍候...").pack(padx=20, pady=(10, 5))
        progress_bar = ttk.Progressbar(progress_win, length=300, mode='determinate')
        progress_bar.pack(padx=20, pady=5)
        export_status_label = ttk.Label(progress_win, text="准备中...")
        export_status_label.pack(padx=20, pady=(0, 10))

        # 禁用关闭按钮，防止导出过程中用户误关闭
        progress_win.protocol("WM_DELETE_WINDOW", lambda: None)

        center_window(progress_win, 400, 120)

        export_result = [None]  # 用于在后台线程和主线程之间传递结果

        def _progress_callback(current, total):
            """从后台线程调用，调度到主线程更新 UI"""
            self.after(0, lambda: _update_progress(current, total))

        def _update_progress(current, total):
            if not self.winfo_exists():
                return
            if not progress_bar.winfo_exists():
                return
            if total > 0:
                progress_bar['maximum'] = total
                progress_bar['value'] = current
            export_status_label.config(text=f"正在打包帧... {current}/{total}")

        def _do_export():
            try:
                self._cache.export_as_srlog(
                    output_path=path,
                    results=results,
                    rois=rois,
                    interval=interval,
                    rotate_angle=rotate_angle,
                    source=source_label,
                    events=events,
                    progress_callback=_progress_callback,
                )
                export_result[0] = ("success", path)
            except Exception as e:
                export_result[0] = ("error", e)
                traceback.print_exc()

        def _poll_completion():
            if not self.winfo_exists():
                return
            if export_result[0] is None:
                self.after(100, _poll_completion)
                return

            progress_win.destroy()
            self._is_exporting = False

            status, data = export_result[0]
            if status == "success":
                self._log(f"会话已导出: {path}")
                messagebox.showinfo("导出成功", f"会话已保存到:\n{path}", parent=self)
            else:
                messagebox.showerror("导出失败", str(data), parent=self)

        self._is_exporting = True
        threading.Thread(target=_do_export, daemon=True).start()
        _poll_completion()

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

        # 保存帧截图（仅摄像头模式）
        if self._data_source.get() == "camera":
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

    def _log(self, message):
        """记录日志（转发到父窗口的log方法）"""
        if hasattr(self.parent, 'log'):
            self.parent.log(f"[实时] {message}")

    def destroy(self):
        """重写 destroy：缓存ROI，释放摄像头/Modbus/IPC"""
        # 停止后台线程 → 主线程 UI 调度轮询
        self._ui_queue_polling = False
        self._ui_queue = None
        # 停止数据源
        source = self._get_active_source()
        if source and not source.is_stopped():
            source.stop()
        # 预览线程清理
        self._stop_preview()
        self._stop_modbus_probe()
        if hasattr(self, '_cap') and self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        if self._cache:
            self._cache.stop_writer()
        # 停止 IPC server
        self._stop_ipc_server()
        # 窗口关闭前持久化ROI
        if self.rois:
            self._save_camera_rois()
        super().destroy()

    def _on_closing(self):
        """WM_DELETE_WINDOW 协议：处理线程确认 + 关闭"""
        if self._is_exporting:
            messagebox.showinfo("提示", "会话正在导出，请等待完成后再关闭窗口。", parent=self)
            return
        source = self._get_active_source()
        if source and not source.is_stopped():
            if messagebox.askyesno("确认退出", "实时识别正在运行，确定退出吗？", parent=self):
                source.stop()
            else:
                return
        self.destroy()
