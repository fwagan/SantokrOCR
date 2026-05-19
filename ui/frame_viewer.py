"""
帧查看器组件

显示指定时间点的视频帧，绘制ROI区域，展示识别结果。
支持导航（上一帧/下一帧）和缩放功能。
包含识别调试标签页，显示中间识别步骤和7段数码管区域划分。
"""

import tkinter as tk
from tkinter import ttk
import cv2
import numpy as np
from PIL import Image, ImageTk
import threading
from core.digit_recognition_pipeline import DigitRecognitionPipeline
# 仅用于SEGMENT_AREAS常量（可视化），不参与实际识别
from core.white_led_recognizer import WhiteLEDRecognizer

from utils.screen_utils import center_window, calc_image_window_size


class FrameViewer(tk.Toplevel):
    """帧查看器窗口"""

    EVENT_TYPES = [
        "入豆", "回温", "一爆开始", "一爆结束",
        "二爆开始", "烘焙结束", "调整火力", "调整风门"
    ]

    def __init__(self, parent, extractor, video_path, rois, frame_num, timestamp,
                 results=None, events=None, on_mark_event_callback=None,
                 heater_initial=50.0, fan_initial=80.0,
                 rotate_angle: float = 5,
                 on_edit_callback=None):
        """
        初始化帧查看器
        Args:
            parent: 父窗口
            extractor: VideoDigitExtractor实例
            video_path: 视频文件路径
            rois: ROI字典
            frame_num: 帧号
            timestamp: 时间戳
            results: 当前帧的识别结果（可选）
            events: 已有事件列表
            on_mark_event_callback: 标记事件回调函数
            heater_initial: 初始火力值
            fan_initial: 初始风门值
            rotate_angle: 旋转角度（正数=逆时针，0=不旋转）
            on_edit_callback: 手动修正回调函数(frame_num, temp1_value, temp2_value)
        """
        super().__init__(parent)

        self.extractor = extractor
        self.video_path = video_path
        self.rois = rois
        self.current_frame_num = frame_num
        self.current_timestamp = timestamp
        self.results = results or {}
        self.events = events if events is not None else []
        self.on_edit_callback = on_edit_callback

        # 从结果字典中提取相对时间戳用于显示
        self.relative_timestamp = self.current_timestamp
        if isinstance(self.results, dict):
            try:
                self.relative_timestamp = float(self.results.get('timestamp', self.current_timestamp))
            except (ValueError, TypeError):
                pass
        self.on_mark_event_callback = on_mark_event_callback
        self.heater_initial = heater_initial
        self.fan_initial = fan_initial

        # 调试可视化相关
        self.debug_photo_images = {}  # 存储PhotoImage引用，防止被GC
        self._debug_generating = False  # 防止重复生成

        # 统一识别管道（debug模式开启，记录中间数据供可视化）
        self._pipeline = DigitRecognitionPipeline(is_debug=True, rotate_angle=rotate_angle)

        # 自动调整大小标记
        self._has_auto_resized = False

        # 配置窗口
        self.title(f"帧查看器 - 帧 {frame_num} ({self.relative_timestamp:.3f}秒)")
        self.minsize(800, 700)

        # 初始暂定居中（图片加载后 auto_resize_window 会再次调整）
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        center_window(self, int(sw * 0.7), int(sh * 0.75))

        # 绑定关闭事件
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        # 创建UI组件
        self.create_widgets()

        # 加载并显示当前帧
        self.load_and_display_frame()

        # 聚焦窗口
        self.focus_set()
        self.grab_set()

    def create_widgets(self):
        """创建UI组件"""
        # 主框架
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        # 控制面板（顶部）
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill="x", pady=(0, 10))

        # 导航按钮
        nav_frame = ttk.Frame(control_frame)
        nav_frame.pack(side="left")

        ttk.Button(nav_frame, text="◀ 上一帧",
                  command=self.prev_frame).pack(side="left", padx=2)
        ttk.Button(nav_frame, text="下一帧 ▶",
                  command=self.next_frame).pack(side="left", padx=2)

        # 帧号输入
        input_frame = ttk.Frame(control_frame)
        input_frame.pack(side="left", padx=20)

        ttk.Label(input_frame, text="跳转到帧号:").pack(side="left", padx=(0, 5))
        self.frame_var = tk.StringVar(value=str(self.current_frame_num))
        frame_entry = ttk.Entry(input_frame, textvariable=self.frame_var, width=10)
        frame_entry.pack(side="left", padx=(0, 5))
        ttk.Button(input_frame, text="跳转",
                  command=self.jump_to_frame).pack(side="left")

        # 时间戳显示
        info_frame = ttk.Frame(control_frame)
        info_frame.pack(side="right")

        ttk.Label(info_frame, text=f"时间戳: {self.relative_timestamp:.3f} 秒").pack(side="left", padx=5)
        ttk.Label(info_frame, text=f"帧号: {self.current_frame_num}").pack(side="left", padx=5)

        # 标签页（中间区域）
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill="both", expand=True, pady=(0, 10))

        # === Tab 1: 视频帧 ===
        self.video_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.video_tab, text="  视频帧  ")
        self._create_video_tab()

        # === Tab 2: 识别调试 ===
        self.debug_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.debug_tab, text="  识别调试  ")
        self._create_debug_tab()

        # === 识别结果 + 手动修正（并排） ===
        hbox = ttk.Frame(main_frame)
        hbox.pack(fill="x", pady=(0, 10))
        hbox.columnconfigure(0, weight=1)

        # 识别结果（左侧）
        result_frame = ttk.LabelFrame(hbox, text="识别结果", padding=10)
        result_frame.grid(row=0, column=0, sticky="nsew")

        # 创建结果网格
        self.create_result_grid(result_frame)

        # 手动修正（右侧）
        edit_frame = ttk.LabelFrame(hbox, text="手动修正", padding=8)
        edit_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        edit_row = ttk.Frame(edit_frame)
        edit_row.pack(fill="x")

        # 颜色指示器
        self.color_indicator = tk.Canvas(edit_row, width=24, height=24,
                                         highlightthickness=1, highlightbackground="gray")
        self.color_indicator.pack(side="left", padx=(0, 10))

        ttk.Label(edit_row, text="豆温:").pack(side="left", padx=(0, 2))
        self.edit_temp1_var = tk.StringVar(value=self.results.get('temp1_full', ''))
        ttk.Entry(edit_row, textvariable=self.edit_temp1_var, width=10).pack(side="left", padx=5)

        ttk.Label(edit_row, text="风温:").pack(side="left", padx=(10, 2))
        self.edit_temp2_var = tk.StringVar(value=self.results.get('temp2', ''))
        ttk.Entry(edit_row, textvariable=self.edit_temp2_var, width=10).pack(side="left", padx=5)

        button_row = ttk.Frame(edit_frame)
        button_row.pack(fill="x", pady=(8, 0))
        ttk.Button(button_row, text="确认修改", command=self.confirm_edit).pack(side="left")

        # 更新颜色指示器
        self.update_color_indicator()

        # 操作按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x")

        ttk.Button(button_frame, text="保存截图",
                  command=self.save_screenshot).pack(side="left", padx=5)
        ttk.Button(button_frame, text="关闭",
                  command=self.destroy).pack(side="right", padx=5)

        # === 事件标记区域 ===
        event_frame = ttk.LabelFrame(main_frame, text="事件标记", padding=8)
        event_frame.pack(fill="x", pady=(5, 0))

        event_row = ttk.Frame(event_frame)
        event_row.pack(fill="x")

        ttk.Label(event_row, text="事件类型:").pack(side="left", padx=(0, 5))
        if not any(e.get('type') == '入豆' for e in self.events):
            default_event = "入豆"
        elif not any(e.get('type') == '回温' for e in self.events):
            default_event = "回温"
        else:
            default_event = "调整火力"
        self.event_type_var = tk.StringVar(value=default_event)
        self.after_idle(lambda: self.on_event_type_changed())
        self.event_combo = ttk.Combobox(event_row, textvariable=self.event_type_var,
                                      values=self.EVENT_TYPES, state="readonly", width=14)
        self.event_combo.pack(side="left", padx=5)
        self.event_combo.bind("<<ComboboxSelected>>", self.on_event_type_changed)

        ttk.Label(event_row, text="数值(%):").pack(side="left", padx=(10, 2))
        self.event_value_var = tk.StringVar(value="")
        self.event_value_entry = ttk.Entry(event_row, textvariable=self.event_value_var, width=8, state="disabled")
        self.event_value_entry.pack(side="left", padx=2)

        ttk.Button(event_row, text="标记事件", command=self.mark_event).pack(side="left", padx=15)

        ttk.Label(event_row, text="(事件发生在当前帧的时间点)", font=("TkDefaultFont", 8)).pack(side="left", padx=5)

        # 绑定标签页切换事件
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def _create_video_tab(self):
        """创建视频帧标签页（原有显示逻辑）"""
        # 图像显示区域
        image_frame = ttk.LabelFrame(self.video_tab, text="视频帧", padding=5)
        image_frame.pack(fill="both", expand=True)

        # 创建画布用于显示图像
        self.canvas = tk.Canvas(image_frame, bg="gray20", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # 添加滚动条（如果图像大于画布）
        self.scroll_x = ttk.Scrollbar(image_frame, orient="horizontal", command=self.canvas.xview)
        self.scroll_y = ttk.Scrollbar(image_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.scroll_x.set, yscrollcommand=self.scroll_y.set)

        # 缩放控制
        zoom_frame = ttk.Frame(image_frame)
        zoom_frame.pack(side="bottom", fill="x", pady=(5, 0))

        ttk.Label(zoom_frame, text="缩放:").pack(side="left", padx=(0, 5))
        self.zoom_var = tk.DoubleVar(value=1.0)
        ttk.Scale(zoom_frame, from_=0.1, to=3.0, variable=self.zoom_var,
                 orient="horizontal", length=150,
                 command=self.on_zoom_change).pack(side="left", padx=(0, 10))
        ttk.Label(zoom_frame, textvariable=self.zoom_var).pack(side="left")

    def _create_debug_tab(self):
        """创建识别调试标签页"""
        debug_outer = ttk.Frame(self.debug_tab, padding=5)
        debug_outer.pack(fill="both", expand=True)

        # 顶部提示
        info_label = ttk.Label(debug_outer,
            text="调试信息：显示ROI裁剪、数字分割、预处理和7段数码管识别过程",
            font=("TkDefaultFont", 9))
        info_label.pack(fill="x", pady=(0, 5))

        # 滚动区域
        scroll_container = ttk.Frame(debug_outer)
        scroll_container.pack(fill="both", expand=True)

        self.debug_canvas = tk.Canvas(scroll_container, bg="#2b2b2b", highlightthickness=0)
        self.debug_scroll_y = ttk.Scrollbar(scroll_container, orient="vertical",
                                           command=self.debug_canvas.yview)
        self.debug_scroll_x = ttk.Scrollbar(scroll_container, orient="horizontal",
                                           command=self.debug_canvas.xview)
        self.debug_canvas.configure(yscrollcommand=self.debug_scroll_y.set,
                                   xscrollcommand=self.debug_scroll_x.set)

        self.debug_canvas.grid(row=0, column=0, sticky="nsew")
        self.debug_scroll_y.grid(row=0, column=1, sticky="ns")
        self.debug_scroll_x.grid(row=1, column=0, sticky="ew")
        scroll_container.grid_rowconfigure(0, weight=1)
        scroll_container.grid_columnconfigure(0, weight=1)

        # 鼠标滚轮滚动
        self.debug_canvas.bind("<MouseWheel>", self._on_debug_vertical_scroll)
        self.debug_canvas.bind("<Shift-MouseWheel>", self._on_debug_horizontal_scroll)

    def create_result_grid(self, parent):
        """创建识别结果网格"""
        grid_frame = ttk.Frame(parent)
        grid_frame.pack(fill="x")

        # 定义要显示的字段
        fields = [
            ("豆温", "temp1_full", "????"),
            ("豆温正常位", "temp1_normal", "????"),
            ("豆温故障位", "temp1_faulty_digit", "-1"),
            ("风温", "temp2", "????")
        ]

        for i, (label, key, default) in enumerate(fields):
            row = i // 3
            col = i % 3

            frame = ttk.Frame(grid_frame)
            frame.grid(row=row, column=col, padx=10, pady=5, sticky="w")

            ttk.Label(frame, text=f"{label}:").pack(side="left")
            value = self.results.get(key, default)
            value_label = ttk.Label(frame, text=str(value), font=("TkDefaultFont", 10, "bold"))
            value_label.pack(side="left", padx=(5, 0))

            # 存储引用以便更新
            if not hasattr(self, 'result_labels'):
                self.result_labels = {}
            self.result_labels[key] = value_label

    def load_and_display_frame(self):
        """加载并显示当前帧（裁剪到只包含ROI区域，向下扩展100%以显示更多数据）"""
        # 在新线程中加载帧，避免阻塞UI
        def load_frame():
            # 获取裁剪到ROI区域的帧（外扩10%，再额外向下扩展100%高度）
            frame = self.extractor.get_frame_with_rois_cropped(
                self.video_path,
                self.current_timestamp,
                self.rois,
                expand_ratio=0.1,
                downward_expand_ratio=1.0
            )

            if frame is not None:
                # 转换颜色空间（OpenCV使用BGR，Tkinter需要RGB）
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # 更新UI（必须在主线程中执行）
                self.after(0, lambda: self.display_frame(frame_rgb))

        # 启动加载线程
        thread = threading.Thread(target=load_frame, daemon=True)
        thread.start()

        # 只在调试标签页可见时生成调试可视化（避免影响正常处理效率）
        if hasattr(self, 'notebook') and self.notebook.index(self.notebook.select()) == 1:
            self._generate_debug_visualization()

    def display_frame(self, frame):
        """在画布上显示帧"""
        # 转换为PIL图像
        pil_image = Image.fromarray(frame)

        # 应用缩放
        zoom = self.zoom_var.get()
        if zoom != 1.0:
            new_width = int(pil_image.width * zoom)
            new_height = int(pil_image.height * zoom)
            pil_image = pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # 首次加载且100%缩放时自动调整窗口大小以适应截图尺寸
        if not self._has_auto_resized and abs(zoom - 1.0) < 0.01:
            self._has_auto_resized = True
            self.auto_resize_window(pil_image.width, pil_image.height)

        # 转换为Tkinter PhotoImage
        self.tk_image = ImageTk.PhotoImage(pil_image)

        # 清除画布并显示新图像
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)

        # 更新画布滚动区域
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        # 显示/隐藏滚动条
        self.update_scrollbars(pil_image.width, pil_image.height)

        # 更新窗口标题
        self.title(f"帧查看器 - 帧 {self.current_frame_num} ({self.current_timestamp:.3f}秒)")

    def auto_resize_window(self, img_width, img_height):
        """自动调整窗口大小以适应图像尺寸（100%缩放时）"""
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        extra_w = 40
        extra_h = 310
        w, h = calc_image_window_size(sw, sh, img_width, img_height, extra_w, extra_h)
        w = max(w, 800)
        h = max(h, 700)
        center_window(self, w, h)

    def update_scrollbars(self, img_width, img_height):
        """根据需要更新滚动条"""
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        # 如果画布尺寸为0，跳过
        if canvas_width <= 1 or canvas_height <= 1:
            return

        need_x_scroll = img_width > canvas_width
        need_y_scroll = img_height > canvas_height

        if need_x_scroll:
            self.scroll_x.pack(side="bottom", fill="x")
        else:
            self.scroll_x.pack_forget()

        if need_y_scroll:
            self.scroll_y.pack(side="right", fill="y")
        else:
            self.scroll_y.pack_forget()

    def on_zoom_change(self, value):
        """缩放改变事件"""
        self.load_and_display_frame()

    def on_tab_changed(self, event):
        """标签页切换事件"""
        # 切换到调试标签页时，确保调试可视化已生成
        if self.notebook.index(self.notebook.select()) == 1:  # 调试标签页索引为1
            if not self._debug_generating:
                self._generate_debug_visualization()

    def _on_debug_vertical_scroll(self, event):
        """鼠标滚轮垂直滚动调试画布"""
        self.debug_canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _on_debug_horizontal_scroll(self, event):
        """Shift+滚轮水平滚动调试画布"""
        self.debug_canvas.xview_scroll(-1 * (event.delta // 120), "units")

    def _draw_seven_seg_overlay(self, digit_img: np.ndarray, segments: list,
                                 seg_areas: dict) -> np.ndarray:
        """
        在数字图像上绘制7段数码管区域划分
        绿色=亮，红色=灭，白色边框=区域边界

        Args:
            digit_img: 30x50的预处理后数字图像
            segments: 7段特征向量 [a,b,c,d,e,f,g]
            seg_areas: SEGMENT_AREAS字典 {name: (y1,y2,x1,x2)}

        Returns:
            带区域划分覆盖的图像（BGR格式）
        """
        # 确保是彩色图像
        if len(digit_img.shape) == 2:
            overlay = cv2.cvtColor(digit_img, cv2.COLOR_GRAY2BGR)
        else:
            overlay = digit_img.copy()

        seg_names = list(seg_areas.keys())

        for i, seg_name in enumerate(seg_names):
            y1, y2, x1, x2 = seg_areas[seg_name]
            is_lit = segments[i] == 1 if i < len(segments) else False

            # 选择颜色：绿色亮，红色灭
            color = (0, 200, 0) if is_lit else (0, 0, 200)
            # 绘制半透明填充
            sub_img = overlay[y1:y2, x1:x2]
            color_overlay = np.full(sub_img.shape, color, dtype=np.uint8)
            overlay[y1:y2, x1:x2] = cv2.addWeighted(sub_img, 0.5, color_overlay, 0.5, 0)

            # 绘制区域边框（白色）
            cv2.rectangle(overlay, (x1, y1), (x2 - 1, y2 - 1), (255, 255, 255), 1)

            # 在区域中心写段名称
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            cv2.putText(overlay, seg_name, (cx - 3, cy + 3),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 255, 255), 1)

        return overlay

    def _generate_debug_visualization(self):
        """生成识别调试可视化（使用统一识别管道）"""
        if self._debug_generating:
            return

        self._debug_generating = True

        def generate():
            try:
                # 获取原始帧（不带ROI框）
                frame = self.extractor.get_frame_at_timestamp(
                    self.video_path,
                    self.current_timestamp
                )
                if frame is None:
                    return

                # 清除之前管道的debug数据
                self._pipeline.clear_debug()

                # 创建调试图片列表
                debug_images = []

                # 定义需要调试的ROI名称（数字区域）
                digit_roi_names = ['temp1_normal', 'temp1_faulty', 'temp2_normal_3digits', 'temp2_normal_lastdigit']

                for roi_name in digit_roi_names:
                    if roi_name not in self.rois:
                        continue

                    # 裁剪ROI
                    x, y, w, h = self.rois[roi_name]
                    roi_img = self.extractor.crop_roi(frame, self.rois[roi_name])
                    if roi_img is None or roi_img.size == 0:
                        continue

                    # 生成ROI缩略图（带标注）
                    roi_thumb = cv2.resize(roi_img, (min(300, roi_img.shape[1] * 3), min(100, roi_img.shape[0] * 3)))
                    # 添加ROI名称和坐标标签
                    roi_thumb_with_label = roi_thumb.copy()
                    cv2.putText(roi_thumb_with_label, f"{roi_name} ({x},{y},{w},{h})",
                               (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

                    mode = 'broken' if roi_name == 'temp1_faulty' else 'normal'
                    segments_data = []
                    results = []

                    # 全部ROI都走分割→识别路径，匹配实际处理管道中recognize_temperature的逻辑
                    self._pipeline.recognize_roi(roi_img, mode=mode, roi_name=roi_name)
                    roi_debug = self._pipeline.get_roi_debug_data(roi_name)
                    if roi_debug:
                        segments_data = roi_debug.get('segments_data', [])
                        results = roi_debug.get('results', [])

                    # 在ROI缩略图上绘制分割边界框
                    seg_overlay = roi_thumb_with_label.copy()
                    scale_x = roi_thumb.shape[1] / roi_img.shape[1]
                    scale_y = roi_thumb.shape[0] / roi_img.shape[0]
                    for si, seg in enumerate(segments_data):
                        bx, by, bw, bh = seg['bbox']
                        # 缩放到缩略图坐标
                        tx = int(bx * scale_x)
                        ty = int(by * scale_y)
                        tw = int(bw * scale_x)
                        th = int(bh * scale_y)
                        cv2.rectangle(seg_overlay, (tx, ty), (tx + tw, ty + th), (0, 255, 0), 1)
                        cv2.putText(seg_overlay, f"{si+1}", (tx + 2, ty + 12),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

                    # 收集本节所有调试图像（起始为ROI缩略图）
                    section_images = [seg_overlay]
                    for si, result in enumerate(results):
                        # 从debug数据中提取信息
                        preprocessed = result.get('preprocessed')
                        segments_vector = result.get('segments')
                        is_digit_1 = result.get('is_digit_1', False)
                        aspect_ratio = result.get('aspect_ratio', 0)
                        digit = result.get('digit', -1)
                        confidence = result.get('confidence', 0)

                        if preprocessed is None or segments_vector is None:
                            continue

                        # 生成预处理后的数字缩略图（放大以便观察）
                        preproc_thumb = cv2.resize(preprocessed, (90, 150))
                        if len(preproc_thumb.shape) == 2:
                            preproc_thumb = cv2.cvtColor(preproc_thumb, cv2.COLOR_GRAY2BGR)

                        # 添加识别结果标签
                        digit_label = f"{digit}" if digit >= 0 else ("?" if digit == -2 else "?")
                        cv2.putText(preproc_thumb, f"#{si+1}={digit_label} c={confidence:.2f}",
                                   (2, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
                        # 添加宽高比和数字1判断信息
                        cv2.putText(preproc_thumb, f"AR={aspect_ratio:.2f} is1={is_digit_1}",
                                   (2, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 255, 0), 1)

                        # 生成7段区域覆盖图 - 使用WhiteLEDRecognizer.SEGMENT_AREAS进行可视化
                        seg_overlay_img = self._draw_seven_seg_overlay(
                            preprocessed, segments_vector, WhiteLEDRecognizer.SEGMENT_AREAS
                        )
                        seg_overlay_thumb = cv2.resize(seg_overlay_img, (90, 150))

                        # 合并：上方是预处理数字，下方是7段覆盖
                        digit_viz = np.vstack([preproc_thumb, seg_overlay_thumb])
                        section_images.append(digit_viz)

                    # 水平拼接本节所有图像
                    if len(section_images) > 0:
                        # 统一高度
                        max_h = max(img.shape[0] for img in section_images)
                        padded = []
                        for img in section_images:
                            h_i, w_i = img.shape[:2]
                            if h_i < max_h:
                                # 填充到相同高度（底部填充黑色）
                                pad = np.zeros((max_h - h_i, w_i, 3), dtype=np.uint8)
                                padded.append(np.vstack([img, pad]))
                            else:
                                padded.append(img)

                        # 添加间隔
                        spacer = np.zeros((max_h, 5, 3), dtype=np.uint8)
                        row_images = []
                        for i, img in enumerate(padded):
                            if i > 0:
                                row_images.append(spacer.copy())
                            row_images.append(img)

                        section_row = np.hstack(row_images)

                        # 添加ROI标题（左侧）
                        section_h, section_w = section_row.shape[:2]
                        # 用黑色填充来加标题
                        title_bar = np.zeros((25, section_w, 3), dtype=np.uint8)
                        cv2.putText(title_bar, f"ROI: {roi_name}  |  segments={len(results)}",
                                   (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
                        final_section = np.vstack([title_bar, section_row])

                        debug_images.append((roi_name, final_section))

                # 如果没有生成任何调试图像
                if not debug_images:
                    # 创建提示信息
                    no_data = np.zeros((100, 600, 3), dtype=np.uint8)
                    cv2.putText(no_data, "No digit ROIs found (temp1_normal, temp1_faulty, temp2_normal_3digits, temp2_normal_lastdigit)",
                               (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                    cv2.putText(no_data, "Select video and configure ROIs in main window first",
                               (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
                    debug_images.append(("info", no_data))

                # 更新UI（必须在主线程中）
                self.after(0, lambda: self._display_debug_visualization(debug_images))

            except Exception as e:
                import traceback
                error_img = np.zeros((100, 600, 3), dtype=np.uint8)
                cv2.putText(error_img, f"Debug viz failed: {str(e)}",
                           (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                cv2.putText(error_img, "See console output for details",
                           (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
                self.after(0, lambda: self._display_debug_visualization([("error", error_img)]))
                traceback.print_exc()
            finally:
                self._debug_generating = False

        # 在后台线程中运行
        thread = threading.Thread(target=generate, daemon=True)
        thread.start()

    def _display_debug_visualization(self, debug_images):
        """在调试标签页中显示调试可视化"""
        # 清除之前的内容
        self.debug_canvas.delete("all")
        self.debug_photo_images.clear()

        if not debug_images:
            return

        y_offset = 10

        for roi_name, img in debug_images:
            # 转换BGR到RGB
            if len(img.shape) == 3:
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            else:
                img_rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)

            # 转换为PhotoImage
            pil_img = Image.fromarray(img_rgb)
            photo = ImageTk.PhotoImage(pil_img)

            # 保存引用
            key = f"debug_{roi_name}_{y_offset}"
            self.debug_photo_images[key] = photo

            # 在画布上显示
            self.debug_canvas.create_image(10, y_offset, anchor="nw", image=photo)
            y_offset += pil_img.height + 10

        # 更新滚动区域
        self.debug_canvas.configure(scrollregion=self.debug_canvas.bbox("all"))

    def on_event_type_changed(self, event=None):
        """事件类型下拉框选择变化时，控制数值输入框的可用状态"""
        event_type = self.event_type_var.get()
        if event_type in ("调整火力", "调整风门"):
            self.event_value_entry.config(state="normal")
            # 查找该类型最近一次标记的值作为默认值
            last_val = None
            for ev in reversed(self.events):
                if ev.get('type') == event_type and ev.get('value') is not None:
                    last_val = ev['value']
                    break
            if last_val is not None:
                self.event_value_var.set(str(int(last_val)))
            else:
                default = self.heater_initial if event_type == "调整火力" else self.fan_initial
                self.event_value_var.set(str(int(default)))
        else:
            self.event_value_entry.config(state="disabled")
            self.event_value_var.set("")

    def mark_event(self):
        """标记事件到当前帧"""
        event_type = self.event_type_var.get()
        value = None
        if event_type in ("调整火力", "调整风门"):
            try:
                value = float(self.event_value_var.get())
                if value < 0 or value > 100:
                    from tkinter import messagebox
                    messagebox.showwarning("数值错误", "火力/风门值必须在0-100之间")
                    return
            except ValueError:
                from tkinter import messagebox
                messagebox.showwarning("数值错误", "请输入有效的数值（0-100）")
                return

        # 调整火力/调整风门可以多次记录（不检查重复）
        # 其他事件只能记录一次（检查重复并提示覆盖）
        if event_type not in ("调整火力", "调整风门"):
            for ev in self.events:
                if ev.get('type') == event_type:
                    from tkinter import messagebox
                    if not messagebox.askyesno("重复事件", f"已存在'{event_type}'事件，是否覆盖？"):
                        return
                    # 覆盖旧事件
                    ev['frame'] = self.current_frame_num
                    ev['time'] = self.current_timestamp
                    ev['value'] = value
                    if self.on_mark_event_callback:
                        self.on_mark_event_callback(ev, is_overwrite=True)
                    self.destroy()
                    return

        # 创建新事件
        event_data = {
            'type': event_type,
            'frame': self.current_frame_num,
            'time': round(self.current_timestamp, 1),
            'value': value
        }
        self.events.append(event_data)
        if self.on_mark_event_callback:
            self.on_mark_event_callback(event_data, is_overwrite=False)
        self.destroy()

    def _get_record_color(self):
        """根据记录数据获取状态颜色"""
        faulty = self.results.get('temp1_faulty_digit')
        temp1 = self.results.get('temp1_full', '')
        abnormal = self.results.get('abnormal_category')

        if abnormal == 'temperature_diff':
            return 'black'
        if faulty == -1 or temp1 == '????':
            return 'red'
        return 'lightgreen'

    def update_color_indicator(self):
        """更新颜色指示器"""
        color = self._get_record_color()
        self.color_indicator.configure(bg=color)

    def confirm_edit(self):
        """确认手动修正"""
        temp1_value = self.edit_temp1_var.get().strip()
        temp2_value = self.edit_temp2_var.get().strip()

        if not temp1_value and not temp2_value:
            from tkinter import messagebox
            messagebox.showwarning("输入错误", "至少输入一个值")
            return

        if self.on_edit_callback:
            self.on_edit_callback(
                self.current_frame_num,
                temp1_value if temp1_value else None,
                temp2_value if temp2_value else None
            )
            from tkinter import messagebox
            messagebox.showinfo("修改成功", "值已更新")

    def prev_frame(self):
        """跳转到上一帧"""

    def next_frame(self):
        """跳转到下一帧"""
        fps = 30
        self.current_timestamp += 1/fps
        self.current_frame_num += 1
        self.load_and_display_frame()
        self.update_frame_info()

    def jump_to_frame(self):
        """跳转到指定帧号"""
        try:
            frame_num = int(self.frame_var.get())
            fps = 30  # 应该从视频中获取

            self.current_frame_num = frame_num
            self.current_timestamp = frame_num / fps
            self.load_and_display_frame()
            self.update_frame_info()
        except ValueError:
            pass

    def update_frame_info(self):
        """更新帧信息显示"""
        # 更新输入框
        self.frame_var.set(str(self.current_frame_num))

        # 更新结果标签（如果有新结果）
        if self.results:
            for key, label in self.result_labels.items():
                value = self.results.get(key, "")
                label.config(text=str(value))

    def save_screenshot(self):
        """保存当前显示的帧为图片"""
        from tkinter import filedialog
        import os

        file_path = filedialog.asksaveasfilename(
            title="保存截图",
            defaultextension=".png",
            filetypes=[("PNG图片", "*.png"), ("JPEG图片", "*.jpg"), ("所有文件", "*.*")]
        )

        if file_path:
            # 获取原始帧（不带ROI框）
            frame = self.extractor.get_frame_at_timestamp(
                self.video_path,
                self.current_timestamp
            )

            if frame is not None:
                # 保存图像
                cv2.imwrite(file_path, frame)
                tk.messagebox.showinfo("保存成功", f"截图已保存到:\n{file_path}")

    def destroy(self):
        """关闭窗口"""
        # 释放资源
        if hasattr(self, 'tk_image'):
            self.tk_image = None
        super().destroy()


if __name__ == "__main__":
    # 测试代码
    root = tk.Tk()
    root.withdraw()

    # 模拟数据
    class MockExtractor:
        def get_frame_with_rois(self, video_path, timestamp, rois):
            import numpy as np
            # 创建测试图像
            img = np.ones((480, 640, 3), dtype=np.uint8) * 100
            cv2.putText(img, f"Test Frame {timestamp}s", (50, 50),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            return img

        def get_frame_at_timestamp(self, video_path, timestamp):
            return self.get_frame_with_rois(video_path, timestamp, {})

    extractor = MockExtractor()
    rois = {
        'temp1_normal': (200, 50, 100, 50),
        'temp1_faulty': (350, 50, 100, 50)
    }

    viewer = FrameViewer(root, extractor, "test.mp4", rois, 100, 10.5)
    root.mainloop()