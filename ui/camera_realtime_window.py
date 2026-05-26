"""
实时识别窗口 — 摄像头实时数字识别与曲线绘制

布局：
- 顶部控制栏：数据源选择、ROI选择、采样间隔、旋转角度
- 主体 PanedWindow：左 预览画布 + 右 Notebook（Tab1 实时曲线 + Tab2 数据表格）
- 中部按钮栏：开始/暂停/停止
- 状态栏
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import cv2
import time
import os
import json
import threading
from PIL import Image, ImageTk

from core.video_extractor import VideoDigitExtractor
from core.camera_capture import CameraProcessingThread
from ui.data_table import DataTable
from ui.statistics_panel import StatisticsPanel
from ui.slog_comparer import extract_valid_data, resample_data, smooth_data, compute_ror
from utils.screen_utils import center_window


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

        # ── 主体：预览 + 右侧Notebook ──
        main = ttk.PanedWindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=4)

        # 左侧：预览画布
        preview_frame = ttk.LabelFrame(main, text="摄像头预览", padding=4)
        self.preview_canvas = tk.Canvas(preview_frame, bg="#222222", highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True)
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

        self.stats_panel = StatisticsPanel(curve_tab, results=[], figsize=(7, 5), show_prediction=True)
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
    # 理想曲线
    # ═══════════════════════════════════════════════════════════

    def _create_ideal_curve_tab(self, parent):
        """创建理想曲线Tab UI"""
        # 文件选择行
        file_frame = ttk.Frame(parent, padding=8)
        file_frame.pack(fill="x")
        ttk.Button(file_frame, text="选择.slog文件", command=self._select_ideal_slog).pack(side="left", padx=(0, 8))
        self.ideal_file_label = ttk.Label(file_frame, text="未选择")
        self.ideal_file_label.pack(side="left")

        # 显示选项
        opt_frame = ttk.LabelFrame(parent, text="显示选项", padding=8)
        opt_frame.pack(fill="x", padx=8, pady=4)
        self.ideal_bean_var = tk.BooleanVar(value=True)
        self.ideal_ror_var = tk.BooleanVar(value=False)
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
            filetypes=[("Slog文件", "*.slog"), ("所有文件", "*.*")]
        )
        if not path:
            return
        self._load_ideal_slog(path)

    def _load_ideal_slog(self, path):
        """加载并处理.slog文件作为理想曲线"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("错误", f"无法加载文件:\n{path}\n{e}")
            return

        results = data.get('results', [])
        events = data.get('events', [])

        if not results:
            messagebox.showwarning("警告", "文件没有有效数据")
            return

        # 处理数据：提取→重采样→平滑→ROR
        timestamps, temp1, temp2 = extract_valid_data(results)
        if len(timestamps) < 2:
            messagebox.showwarning("警告", "有效数据不足")
            return

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

        self.ideal_data = {
            'path': path,
            'name': os.path.basename(path),
            'resampled_time': resampled_time,
            'smooth_temp1': smooth_temp1,
            'smooth_temp2': smooth_temp2,
            'ror_time': ror_time,
            'ror_values': ror_values,
            'events': events,
            'alignment': alignment,
            'charge_time': charge_time if charge_time else 0.0,
            'end_time': end_time,
        }

        # 更新UI
        self.ideal_file_label.config(text=os.path.basename(path))
        self._update_ideal_info()

        # 传递给统计面板
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
        lines = [
            f"文件: {data['name']}",
            f"数据点: {len(data['resampled_time'])}",
            f"时长: {data['resampled_time'][-1] - data['resampled_time'][0]:.1f}秒",
        ]
        # 事件信息
        for ev in data['events']:
            ev_type = ev.get('type', '')
            ev_time = ev.get('time', 0)
            if ev_type in ('入豆', '回温', '一爆开始', '烘焙结束'):
                lines.append(f"  {ev_type}: {int(ev_time//60):02d}:{int(ev_time%60):02d}")
            elif ev_type in ('调整火力', '调整风门'):
                lines.append(f"  {ev_type}: {int(ev_time//60):02d}:{int(ev_time%60):02d} → {ev.get('value', '?')}%")
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
            self.rois = None
            self.roi_status_var.set("未配置")
            self.start_btn.config(state="disabled")
        else:
            self.source_combo.configure(state="readonly")
            self.source_combo["values"] = available
            sel = self.source_var.get()
            if sel not in available:
                self.source_var.set("")
                self.rois = None
                self.roi_status_var.set("未配置")
                self.start_btn.config(state="disabled")

    def _on_source_changed(self, event=None):
        """数据源切换"""
        sel = self.source_var.get()
        if not sel:
            return
        self._current_source = self._source_map[sel]
        # 清除之前摄像头的ROI
        self.rois = None
        self.roi_status_var.set("未配置")
        self.start_btn.config(state="disabled")
        self._start_preview()

    def _get_source_label(self):
        """返回当前数据源的友好名称"""
        sel = self.source_var.get()
        return sel if sel else str(self._current_source)

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
        """停止预览（等待后台线程释放摄像头，避免后续cap打开时DSHOW冲突）"""
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

        fail_count = 0
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

        try:
            cap.release()
        except Exception:
            pass

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
                for name, (x, y, rw, rh) in self.rois.items():
                    color = self.extractor.get_roi_color(name)
                    hex_color = '#%02x%02x%02x' % (color[2], color[1], color[0])
                    self.preview_canvas.create_rectangle(
                        ox + x * scale, oy + y * scale,
                        ox + (x + rw) * scale, oy + (y + rh) * scale,
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
        """摄像头断开 — 停止预览+处理、清空数据源/ROI状态"""
        # 先停止识别线程
        if self.processing_thread and not self.processing_thread.is_stopped():
            self.processing_thread.stop()
        self._stop_preview()
        self._show_no_data()
        self.source_combo.configure(state="disabled")
        self.source_combo["values"] = ["无可用数据源"]
        self.source_var.set("无可用数据源")
        self.rois = None
        self.roi_status_var.set("未配置")
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
            if not messagebox.askyesno("确认", "选择ROI将停止当前识别并清空所有数据，是否继续？"):
                return
            self._stop_realtime()
            self.results.clear()
            self.data_table.clear()
            self.stats_panel.clear_data()
        elif self.results:
            # 停止后有残留数据
            if not messagebox.askyesno("确认", "选择ROI将清空当前数据，是否继续？"):
                return
            self.results.clear()
            self.data_table.clear()
            self.stats_panel.clear_data()

        # 取预览线程最新帧
        if self._preview_frame is None:
            messagebox.showerror("错误", "无可用预览帧")
            return
        frame = self._preview_frame.copy()

        from ui.roi_selector import RoiSelector
        selector = RoiSelector(parent=self, frame=frame)
        rois = selector.get_results()
        if rois:
            self.rois = rois
            self.roi_status_var.set("已配置")
            self.start_btn.config(state="normal")
            self._log(f"ROI选择完成: {len(rois)}个区域")

    # ═══════════════════════════════════════════════════════════
    # 实时处理控制
    # ═══════════════════════════════════════════════════════════

    def _start_realtime(self):
        """开始实时识别"""
        if not self.rois:
            messagebox.showwarning("警告", "请先选择ROI")
            return

        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "采样间隔必须大于0")
            return

        # 检查残留数据（停止后重新开始）
        if self.results:
            if not messagebox.askyesno("新一轮识别", "开始新一轮识别将清空当前数据，是否继续？"):
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

        self.processing_thread = CameraProcessingThread(
            extractor=self.extractor,
            get_frame=lambda: self._preview_frame,
            rois=self.rois,
            interval=interval
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
        if not messagebox.askyesno("确认清空", "确定要清空所有已识别的数据吗？\n此操作不可撤销。"):
            return
        self.results.clear()
        self.data_table.clear()
        self.stats_panel.clear_data()

    def _reset_buttons(self):
        """重置按钮状态"""
        self.start_btn.config(state="normal" if self.rois else "disabled")
        self.pause_btn.config(state="disabled", text="暂停")
        self.stop_btn.config(state="disabled")

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
        """处理识别结果"""
        self.results.append(result)
        # 只在最近50条内追加到表格
        self.data_table.add_row(result)
        # 更新实时曲线
        self.stats_panel.append_data(result)

    def _on_status(self, message):
        """处理状态更新"""
        self.status_var.set(message)
        self._log(message)

    def _on_finished(self, success, message):
        """处理完成信号（由识别线程触发，调度到UI线程）"""
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
        """双击表格行：显示缓存的失败帧用于调试"""
        if self.processing_thread:
            failed = self.processing_thread.get_failed_frames()
            if failed:
                self._show_failed_frames_debug(failed)
            else:
                messagebox.showinfo("提示", "没有缓存的失败帧")
        else:
            messagebox.showinfo("提示", "实时识别未运行")

    def _show_failed_frames_debug(self, failed_frames):
        """显示缓存失败帧的调试窗口"""
        win = tk.Toplevel(self)
        win.title("失败帧调试 - 最近10帧")
        win.geometry("1400x900")
        center_window(win, 1400, 900)

        # 使用Notebook切换不同失败帧
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        for frame_num, frame_bgr, result in failed_frames:
            tab = ttk.Frame(nb)
            nb.add(tab, text=f"帧{frame_num}")

            # 左侧：完整帧（带ROI标注）
            left = ttk.Frame(tab)
            left.pack(side="left", fill="both", expand=True, padx=4, pady=4)

            frame_display = frame_bgr.copy()
            if self.rois:
                for name, (x, y, w, h) in self.rois.items():
                    color = self.extractor.get_roi_color(name)
                    cv2.rectangle(frame_display, (x, y), (x + w, y + h), color, 2)

            canvas = tk.Canvas(left, bg="#222222")
            canvas.pack(fill="both", expand=True)

            # 缩放帧到画布
            def _show_on_canvas(c, img, ev=None):
                cw, ch = c.winfo_width(), c.winfo_height()
                if cw < 10 or ch < 10:
                    return
                h, w = img.shape[:2]
                s = min(cw / w, ch / h)
                tw, th = int(w * s), int(h * s)
                small = cv2.resize(img, (tw, th), interpolation=cv2.INTER_NEAREST)
                rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
                tk_img = ImageTk.PhotoImage(pil)
                c.delete("all")
                c.create_image((cw - tw) // 2, (ch - th) // 2, anchor="nw", image=tk_img)
                c._tk_img = tk_img  # 保持引用

            canvas.bind("<Configure>", lambda e, c=canvas, img=frame_display: _show_on_canvas(c, img, e))

            # 右侧：识别结果
            right = ttk.Frame(tab)
            right.pack(side="right", fill="y", padx=4, pady=4)

            ttk.Label(right, text=f"帧号: {frame_num}", font=("", 11, "bold")).pack(anchor="w", pady=2)
            ttk.Label(right, text=f"时间戳: {result.get('timestamp', '?')}").pack(anchor="w")
            ttk.Label(right, text=f"豆温: {result.get('temp1_full', '?')}").pack(anchor="w")
            ttk.Label(
                right, text=f"豆温正常位: {result.get('temp1_normal', '?')}",
                foreground="red" if '?' in str(result.get('temp1_full', '')) else "black"
            ).pack(anchor="w")
            ttk.Label(
                right, text=f"故障位: {result.get('temp1_faulty_digit', '?')}",
                foreground="red" if result.get('temp1_faulty_digit', -1) == -1 else "black"
            ).pack(anchor="w")
            ttk.Label(
                right, text=f"风温: {result.get('temp2', '?')}",
                foreground="red" if '?' in str(result.get('temp2', '')) else "black"
            ).pack(anchor="w")

            # 显示各ROI裁剪图
            ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)
            ttk.Label(right, text="ROI裁剪区域:", font=("", 10, "bold")).pack(anchor="w")

            roi_frame = ttk.Frame(right)
            roi_frame.pack(fill="x", pady=4)

            for name in ['temp1_normal', 'temp1_faulty', 'temp2_normal_3digits', 'temp2_normal_lastdigit', 'temp2_normal']:
                if name not in self.rois:
                    continue
                x, y, w, h = self.rois[name]
                crop = frame_bgr[y:y+h, x:x+w].copy()
                # 放大2倍
                crop_big = cv2.resize(crop, (w * 2, h * 2), interpolation=cv2.INTER_NEAREST)
                crop_rgb = cv2.cvtColor(crop_big, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(crop_rgb)
                tk_img = ImageTk.PhotoImage(pil)

                item_frame = ttk.Frame(roi_frame)
                item_frame.pack(anchor="w", pady=2)
                ttk.Label(item_frame, text=name, font=("", 8)).pack()
                lbl = ttk.Label(item_frame, image=tk_img)
                lbl.image = tk_img  # 保持引用
                lbl.pack()

        # 底部按钮
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btn_frame, text="关闭", command=win.destroy).pack(side="right")

    def _export_data(self):
        """导出数据为.slog（含events）"""
        if not self.results:
            messagebox.showwarning("警告", "没有可导出的数据")
            return

        export = {
            'version': 1,
            'results': self.results,
            'events': self.stats_panel.events if hasattr(self.stats_panel, 'events') else [],
            'heater_initial': self.stats_panel.heater_initial if hasattr(self.stats_panel, 'heater_initial') else 0,
            'fan_initial': self.stats_panel.fan_initial if hasattr(self.stats_panel, 'fan_initial') else 0,
        }

        path = filedialog.asksaveasfilename(
            defaultextension=".slog",
            filetypes=[("Slog files", "*.slog"), ("All files", "*.*")]
        )
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(export, f, indent=2, ensure_ascii=False)
            self._log(f"数据已导出: {path}")

    def _log(self, message):
        """记录日志（转发到父窗口的log方法）"""
        if hasattr(self.parent, 'log'):
            self.parent.log(f"[实时] {message}")

    def _on_closing(self):
        """窗口关闭"""
        if self.processing_thread and not self.processing_thread.is_stopped():
            if messagebox.askyesno("确认退出", "实时识别正在运行，确定退出吗？"):
                self.processing_thread.stop()
            else:
                return
        self._stop_preview()
        self.destroy()
