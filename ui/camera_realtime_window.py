"""
实时识别窗口 — 摄像头实时数字识别与曲线绘制

布局：
- 顶部控制栏：数据源选择、ROI选择、采样间隔、旋转角度
- 主体 PanedWindow：左 预览画布 + 右 Notebook（Tab1 实时曲线 + Tab2 数据表格）
- 中部按钮栏：开始/暂停/停止
- 状态栏
"""

import tkinter as tk
from tkinter import ttk, messagebox
import cv2
import time
from PIL import Image, ImageTk

from core.video_extractor import VideoDigitExtractor
from core.camera_capture import CameraProcessingThread
from ui.data_table import DataTable
from ui.statistics_panel import StatisticsPanel
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

        # 预览
        self._preview_cap = None
        self._preview_after_id = None
        self._preview_img_id = None
        self._preview_tk_image = None
        self._no_data_text_id = None

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

        # 强制布局，确保图表canvas有正确尺寸后再绘制
        self.update_idletasks()
        self.stats_panel.fig.tight_layout()
        self.stats_panel.canvas.draw()

        # 启动预览
        self._start_preview()

    # ═══════════════════════════════════════════════════════════
    # UI创建
    # ═══════════════════════════════════════════════════════════

    def _create_ui(self):
        """创建完整UI布局"""
        # ── 顶部控制栏 ──
        top_bar = ttk.Frame(self, padding=8)
        top_bar.pack(fill="x")

        # 数据源选择（打开下拉时动态检测可用摄像头）
        ttk.Label(top_bar, text="数据源:").pack(side="left", padx=(0, 4))
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(top_bar, textvariable=self.source_var,
                                          state="readonly", width=30,
                                          postcommand=self._detect_cameras)
        self.source_combo.pack(side="left", padx=4)
        self.source_combo.bind("<<ComboboxSelected>>", self._on_source_changed)

        # 选择ROI按钮
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

        # Tab 1: 实时曲线
        curve_tab = ttk.Frame(self.notebook)
        curve_tab.pack_propagate(False)  # 阻止FigureCanvasTkAgg塌缩父容器
        self.notebook.add(curve_tab, text="实时曲线")

        self.stats_panel = StatisticsPanel(curve_tab, results=[])
        self.stats_panel.pack(side="top", fill="both", expand=True)

        # 曲线控制 dock bottom（实时模式：仅显示原曲线checkbox）
        ctrl_row = ttk.Frame(curve_tab)
        self.stats_panel.create_controls(ctrl_row, realtime_mode=True)
        ctrl_row.pack(side="bottom", fill="x", pady=(4, 0))

        # Tab 2: 数据表格
        table_tab = ttk.Frame(self.notebook)
        self.notebook.add(table_tab, text="数据表格")

        self.data_table = DataTable(table_tab)
        self.data_table.pack(fill="both", expand=True)
        self.data_table.set_view_frame_callback(self._on_view_frame)

        # ── 中部按钮栏 ──
        btn_bar = ttk.Frame(self, padding=8)
        btn_bar.pack(side="bottom", fill="x")

        self.start_btn = ttk.Button(btn_bar, text="开始实时识别", command=self._start_realtime, state="disabled")
        self.start_btn.pack(side="left", padx=4)

        self.pause_btn = ttk.Button(btn_bar, text="暂停", command=self._pause_realtime, state="disabled")
        self.pause_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(btn_bar, text="停止", command=self._stop_realtime, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        ttk.Button(btn_bar, text="导出数据", command=self._export_data).pack(side="right", padx=4)

        # ── 状态栏 ──
        status_bar = ttk.Frame(self, relief="sunken", borderwidth=1)
        status_bar.pack(side="bottom", fill="x", padx=8, pady=(0, 4))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_bar, textvariable=self.status_var, padding=(8, 4)).pack(side="left")

        self.time_var = tk.StringVar(value="运行时长: 00:00")
        ttk.Label(status_bar, textvariable=self.time_var, padding=(8, 4)).pack(side="right")

    # ═══════════════════════════════════════════════════════════
    # 数据源管理
    # ═══════════════════════════════════════════════════════════

    _MAX_CAMERA_INDEX = 9

    def _detect_cameras(self):
        """检测可用摄像头并刷新下拉列表"""
        available = []
        self._source_map = {}
        for i in range(self._MAX_CAMERA_INDEX + 1):
            try:
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        label = f"摄像头 {i}"
                        available.append(label)
                        self._source_map[label] = i
                cap.release()
            except Exception:
                pass

        self.source_combo["values"] = available

        # 清除不再有效的选中项
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
            self.after(200, self._show_no_data)
            return
        self.preview_canvas.delete("all")
        self._no_data_text_id = self.preview_canvas.create_text(
            cw // 2, ch // 2, text="无数据",
            fill="#666666", font=("", 24), anchor="center"
        )

    def _start_preview(self):
        """启动/重启摄像头预览"""
        self._stop_preview()

        # 清除"无数据"文字
        if self._no_data_text_id:
            self.preview_canvas.delete(self._no_data_text_id)
            self._no_data_text_id = None

        if not hasattr(self, '_current_source'):
            self._show_no_data()
            return

        source = self._current_source
        self._preview_cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        if not self._preview_cap.isOpened():
            self._preview_cap = None
            self._show_no_data()
            return

        # 获取源帧率，决定预览刷新间隔
        src_fps = self._preview_cap.get(cv2.CAP_PROP_FPS)
        if src_fps > 0:
            self._preview_interval = int(1000 / src_fps)  # ms per frame
        else:
            self._preview_interval = 33  # 默认~30fps
        self._preview_interval = max(16, min(self._preview_interval, 100))  # 限幅 10-60fps

        self._preview_retry_count = 0
        self._preview_loop()

    def _stop_preview(self):
        """停止预览"""
        if self._preview_after_id:
            self.after_cancel(self._preview_after_id)
            self._preview_after_id = None
        if self._preview_cap:
            self._preview_cap.release()
            self._preview_cap = None

    def _preview_loop(self):
        """预览循环"""
        if not self._preview_cap or not self._preview_cap.isOpened():
            self._preview_retry_count += 1
            if self._preview_retry_count < 30:
                self._preview_after_id = self.after(self._preview_interval, self._preview_loop)
            return

        ret, frame = self._preview_cap.read()
        if not ret:
            self._preview_retry_count += 1
            if self._preview_retry_count > 30:
                return
            self._preview_after_id = self.after(self._preview_interval, self._preview_loop)
            return

        self._preview_retry_count = 0

        # 先调度下一次循环，保证帧率不受当前帧处理耗时影响
        self._preview_after_id = self.after(self._preview_interval, self._preview_loop)

        # 绘制ROI叠加框
        if self.rois:
            for name, (x, y, w, h) in self.rois.items():
                color = self.extractor.get_roi_color(name)
                cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

        # 缩放并显示
        self._display_preview_frame(frame)

    def _display_preview_frame(self, frame_bgr):
        """在预览画布上显示一帧（全部用OpenCV缩放，PIL仅做PhotoImage转换）"""
        cw = self.preview_canvas.winfo_width()
        ch = self.preview_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        # 清除"无数据"文字
        if self._no_data_text_id:
            self.preview_canvas.delete(self._no_data_text_id)
            self._no_data_text_id = None

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
        source = self._current_source
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            messagebox.showerror("错误", "无法打开数据源")
            return

        ret, frame = cap.read()
        cap.release()
        if not ret:
            messagebox.showerror("错误", "无法从数据源读取帧")
            return

        # 恢复暂存的预览
        self._stop_preview()

        try:
            from ui.roi_selector import RoiSelector
            selector = RoiSelector(parent=self, frame=frame)
            rois = selector.get_results()
            if rois:
                self.rois = rois
                self.roi_status_var.set("已配置")
                self.start_btn.config(state="normal")
                self._log(f"ROI选择完成: {len(rois)}个区域")
        finally:
            self._start_preview()

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

        # 创建处理线程
        self.processing_thread = CameraProcessingThread(
            extractor=self.extractor,
            source=self._current_source,
            rois=self.rois,
            interval=interval
        )

        # 连接信号
        self.processing_thread.result_signal.connect(self._on_result)
        self.processing_thread.frame_signal.connect(self._on_processing_frame)
        self.processing_thread.status_signal.connect(self._on_status)
        self.processing_thread.finished_signal.connect(self._on_finished)

        # 启动
        self.processing_thread.start()

        # 停止独立预览循环，改由处理线程 frame_signal 驱动预览
        self._stop_preview()

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

    def _on_processing_frame(self, frame):
        """处理线程发来的帧 — 调度到UI线程显示（替代独立预览循环）"""
        self.after_idle(lambda f=frame.copy(): self._display_processing_frame(f))

    def _display_processing_frame(self, frame):
        """在UI线程绘制 ROI 叠加框并显示"""
        if self.rois:
            for name, (x, y, w, h) in self.rois.items():
                color = self.extractor.get_roi_color(name)
                cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        self._display_preview_frame(frame)

    def _on_status(self, message):
        """处理状态更新"""
        self.status_var.set(message)
        self._log(message)

    def _on_finished(self, success, message):
        """处理完成"""
        self._reset_buttons()
        self.status_var.set(message)
        self._log(message)
        self.processing_thread = None
        # 恢复独立预览循环
        self._start_preview()

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
                foreground="red" if result.get('temp1_full') == '????' else "black"
            ).pack(anchor="w")
            ttk.Label(
                right, text=f"故障位: {result.get('temp1_faulty_digit', '?')}",
                foreground="red" if result.get('temp1_faulty_digit', -1) == -1 else "black"
            ).pack(anchor="w")
            ttk.Label(
                right, text=f"风温: {result.get('temp2', '?')}",
                foreground="red" if result.get('temp2') == '????' else "black"
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
        """导出数据为.slog"""
        if not self.results:
            messagebox.showwarning("警告", "没有可导出的数据")
            return

        from tkinter import filedialog
        import json

        export = {
            'version': 1,
            'results': self.results,
            'events': [],
            'heater_initial': 0,
            'fan_initial': 0,
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
