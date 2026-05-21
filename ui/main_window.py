"""
主窗口类

包含：
1. 菜单栏
2. 控制面板（视频选择、ROI配置、参数设置）
3. 结果展示区域（数据表格、日志、统计预留）
4. 状态栏（进度条、状态信息）
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
from queue import Queue
import time
import cv2
import sys

from core.video_extractor import VideoDigitExtractor
from ui.data_table import DataTable
from ui.async_worker import ProcessingThread
from ui.frame_viewer import FrameViewer
from utils.cache_manager import get_cache_manager
from utils.screen_utils import center_window


class MainWindow(tk.Tk):
    """主窗口类"""

    def __init__(self):
        super().__init__()

        # 初始化变量
        self.video_path = None
        self.rois = None
        self.results = []
        self.events = []            # 用户标记的事件列表
        self.processing_thread = None
        self.extractor = VideoDigitExtractor()
        self._slog_viewer = None  # 单例slog viewer窗口
        self.cache_manager = get_cache_manager()
        self._realtime_window = None  # 实时识别窗口单例

        # 配置窗口
        self.title("SantokrOCR - 视频数字提取工具")
        self.minsize(1100, 600)
        center_window(self, 3200, 1900)

        # 设置图标（如果有）
        try:
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(base_path, 'icon.ico')
            if os.path.exists(icon_path):
                self.iconbitmap(default=icon_path)
        except:
            pass

        # 创建UI组件
        self.create_menu()
        self.create_center_panel()
        self.create_bottom_panel()

        # 初始化状态
        self.update_status("就绪")

        # 绑定窗口关闭事件
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_menu(self):
        """创建菜单栏"""
        menubar = tk.Menu(self)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开视频", command=self.open_video, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="实时识别 (摄像头)", command=self.open_realtime)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_closing, accelerator="Ctrl+Q")
        menubar.add_cascade(label="文件", menu=file_menu)

        # 视图菜单
        view_menu = tk.Menu(menubar, tearoff=0)
        self.show_log_var = tk.BooleanVar(value=True)
        view_menu.add_checkbutton(label="显示日志", variable=self.show_log_var,
                                  command=self.toggle_log_display)
        menubar.add_cascade(label="视图", menu=view_menu)

        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.config(menu=menubar)

        # 绑定快捷键
        self.bind('<Control-o>', lambda e: self.open_video())
        self.bind('<Control-q>', lambda e: self.on_closing())

    def create_top_panel(self, parent=None):
        """创建顶部控制面板"""
        if parent is None:
            parent = self
        top_frame = ttk.LabelFrame(parent, text="控制面板", padding=10)
        top_frame.pack(fill="x", padx=10, pady=5)

        # 视频选择行
        video_row = ttk.Frame(top_frame)
        video_row.pack(fill="x", pady=5)

        ttk.Label(video_row, text="视频文件:").pack(side="left", padx=(0, 5))
        self.video_label = ttk.Label(video_row, text="未选择", width=60, relief="sunken", padding=5)
        self.video_label.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(video_row, text="选择视频", command=self.open_video).pack(side="left", padx=5)

        # ROI配置行
        roi_row = ttk.Frame(top_frame)
        roi_row.pack(fill="x", pady=5)

        ttk.Label(roi_row, text="ROI配置:").pack(side="left", padx=(0, 5))
        self.roi_status_label = ttk.Label(roi_row, text="未配置", width=40, relief="sunken", padding=5)
        self.roi_status_label.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(roi_row, text="框选ROI", command=self.select_roi,
                  state="disabled").pack(side="left", padx=5)
        ttk.Button(roi_row, text="查看ROI预览", command=self.show_roi_preview,
                  state="disabled").pack(side="left", padx=5)

        # 参数设置行
        params_row = ttk.Frame(top_frame)
        params_row.pack(fill="x", pady=5)

        ttk.Label(params_row, text="采样间隔 (秒):").pack(side="left", padx=(0, 5))
        self.interval_var = tk.StringVar(value="0.25")
        ttk.Entry(params_row, textvariable=self.interval_var, width=10).pack(side="left", padx=5)

        # 测试模式开关
        ttk.Label(params_row, text="测试模式:").pack(side="left", padx=(20, 5))
        self.test_mode_var = tk.BooleanVar(value=False)
        self.test_checkbox = ttk.Checkbutton(params_row, variable=self.test_mode_var)
        self.test_checkbox.pack(side="left")

        # 旋转角度输入
        ttk.Label(params_row, text="旋转角度(°):").pack(side="left", padx=(20, 5))
        self.rotation_angle_var = tk.StringVar(value="5")
        ttk.Entry(params_row, textvariable=self.rotation_angle_var, width=6).pack(side="left", padx=5)

        # 处理控制按钮
        control_row = ttk.Frame(top_frame)
        control_row.pack(fill="x", pady=10)

        self.start_button = ttk.Button(control_row, text="开始处理", command=self.start_processing,
                                      state="disabled")
        self.start_button.pack(side="left", padx=5)

        self.pause_button = ttk.Button(control_row, text="暂停", command=self.pause_processing,
                                      state="disabled")
        self.pause_button.pack(side="left", padx=5)

        self.stop_button = ttk.Button(control_row, text="停止", command=self.stop_processing,
                                     state="disabled")
        self.stop_button.pack(side="left", padx=5)

        ttk.Separator(control_row, orient="vertical").pack(side="left", padx=10, fill="y")
        ttk.Button(control_row, text="实时识别", command=self.open_realtime).pack(side="left", padx=5)

        # 统计按钮行（导出.alog等）
        stats_row = ttk.Frame(top_frame)
        stats_row.pack(fill="x", pady=5)

        ttk.Label(stats_row, text="初始火力(%):").pack(side="left", padx=(0, 2))
        self.heater_initial_var = tk.DoubleVar(value=60.0)
        heater_entry = ttk.Entry(stats_row, textvariable=self.heater_initial_var, width=6)
        heater_entry.pack(side="left", padx=2)
        heater_entry.bind('<FocusOut>', self.on_initial_value_changed)

        ttk.Label(stats_row, text="初始风门(%):").pack(side="left", padx=(10, 2))
        self.fan_initial_var = tk.DoubleVar(value=50.0)
        fan_entry = ttk.Entry(stats_row, textvariable=self.fan_initial_var, width=6)
        fan_entry.pack(side="left", padx=2)
        fan_entry.bind('<FocusOut>', self.on_initial_value_changed)

        ttk.Button(stats_row, text="绘制曲线",
                  command=self.open_slog_viewer).pack(side="left", padx=15)

        # 数据清理控制行
        cleanup_row = ttk.Frame(top_frame)
        cleanup_row.pack(fill="x", pady=5)

        ttk.Button(cleanup_row, text="排除非法数据",
                  command=self.remove_invalid_data).pack(side="left", padx=5)

        ttk.Label(cleanup_row, text="合理温度差值:").pack(side="left", padx=(20, 5))
        self.temp_diff_threshold_var = tk.StringVar(value="1.0")
        self.temp_diff_entry = ttk.Entry(cleanup_row, textvariable=self.temp_diff_threshold_var, width=8, state="disabled")
        self.temp_diff_entry.pack(side="left", padx=5)

        self.detect_abnormal_button = ttk.Button(cleanup_row, text="检测异常数据",
                  command=self.detect_abnormal_data, state="disabled")
        self.detect_abnormal_button.pack(side="left", padx=5)

        # 筛选器控制行
        filter_row = ttk.Frame(top_frame)
        filter_row.pack(fill="x", pady=5)

        ttk.Label(filter_row, text="筛选记录颜色:").pack(side="left", padx=(0, 5))
        self.filter_color_var = tk.StringVar(value="全部")
        tk.Radiobutton(filter_row, text="全部", variable=self.filter_color_var,
                      value="全部", command=self.apply_color_filter).pack(side="left", padx=3)
        tk.Radiobutton(filter_row, text="识别失败", variable=self.filter_color_var,
                      value="红色-识别失败", command=self.apply_color_filter,
                      foreground="red").pack(side="left", padx=3)
        tk.Radiobutton(filter_row, text="温差异常", variable=self.filter_color_var,
                      value="温差异常", command=self.apply_color_filter,
                      foreground="purple").pack(side="left", padx=3)

    def remove_invalid_data(self):
        """排除非法数据：删除temp1_full为????或temp1_faulty_digit为-1的记录"""
        if not self.results:
            self.log("没有可处理的数据")
            return

        # 统计非法数据
        def is_invalid(r):
            return r.get('temp1_full') == '????' or r.get('temp1_faulty_digit') == -1

        invalid_count = sum(1 for r in self.results if is_invalid(r))
        if invalid_count == 0:
            self.log("没有非法/识别失败的数据")
            return

        self.log(f"开始排除非法数据，共发现{invalid_count}条非法/识别失败记录...")

        # 创建新结果列表，只保留有效数据
        new_results = []
        removed_indices = []

        for i, result in enumerate(self.results):
            if is_invalid(result):
                removed_indices.append(i)
            else:
                new_results.append(result)

        # 更新结果列表
        self.results = new_results

        # 重新加载表格
        self.data_table.clear()
        for result in self.results:
            self.data_table.add_row(result)

        self.log(f"排除完成，删除了{len(removed_indices)}条非法/识别失败记录")

        # 启用检测异常数据相关控件
        self.temp_diff_entry.config(state="normal")
        self.detect_abnormal_button.config(state="normal")

        # 更新缓存
        self.update_cache()

    def detect_abnormal_data(self):
        """检测异常数据：标记温差大于阈值的记录为黑色"""
        if not self.results:
            self.log("没有可处理的数据")
            return

        try:
            threshold = float(self.temp_diff_threshold_var.get())
            if threshold <= 0:
                raise ValueError("合理温度差值必须大于0")
        except ValueError as e:
            messagebox.showerror("错误", f"参数错误: {e}")
            return

        self.log(f"开始检测异常数据，温度差值阈值: {threshold}")
        self.log(f"检测前的results数量: {len(self.results)}")

        # 首先清除之前的异常标记
        for result in self.results:
            if 'abnormal_category' in result:
                del result['abnormal_category']

        # 检测异常温差
        abnormal_count = 0
        for i in range(1, len(self.results)):
            prev_result = self.results[i-1]
            curr_result = self.results[i]

            # 获取温度值（temp1_full）
            prev_temp_str = prev_result.get('temp1_full', '')
            curr_temp_str = curr_result.get('temp1_full', '')

            # 跳过非法温度值
            if prev_temp_str == '????' or curr_temp_str == '????':
                continue

            try:
                prev_temp = float(prev_temp_str)
                curr_temp = float(curr_temp_str)
                temp_diff = abs(curr_temp - prev_temp)

                if temp_diff > threshold:
                    # 标记当前记录为异常
                    curr_result['abnormal_category'] = 'temperature_diff'
                    abnormal_count += 1
                    self.log(f"发现异常温差: 记录{i} ({curr_temp_str}) - 记录{i-1} ({prev_temp_str}) = {temp_diff:.2f} > {threshold}")
            except ValueError:
                # 温度值无法转换为浮点数，跳过
                continue

        # 自动切换到温差异常筛选
        self.filter_color_var.set("温差异常")
        self.apply_color_filter()

        self.log(f"检测完成，发现{abnormal_count}条异常温差记录")

    def apply_color_filter(self, event=None):
        """应用颜色筛选"""
        if not hasattr(self, 'data_table') or not self.data_table:
            return

        selected = self.filter_color_var.get()
        self.data_table.clear()

        if selected == "全部":
            for result in self.results:
                self.data_table.add_row(result)
            self.log(f"显示全部 {len(self.results)} 条记录")
            return

        if selected == "红色-识别失败":
            count = 0
            for result in self.results:
                if result.get('temp1_faulty_digit') == -1 or result.get('temp2', '') == '????':
                    self.data_table.add_row(result)
                    count += 1
            self.log(f"筛选完成，显示 {count} 条识别失败记录")
            return

        if selected == "温差异常":
            # 收集所有温差异常索引
            abnormal_indices = []
            for idx, r in enumerate(self.results):
                if r.get('abnormal_category') == 'temperature_diff':
                    abnormal_indices.append(idx)

            if not abnormal_indices:
                self.log("没有温差异常记录")
                return

            # 将间距≤5的异常记录合并为组
            groups = []
            current_group = [abnormal_indices[0]]
            for idx in abnormal_indices[1:]:
                if idx - current_group[-1] <= 5:
                    current_group.append(idx)
                else:
                    groups.append(current_group)
                    current_group = [idx]
            groups.append(current_group)

            # 每组前后各取5条上下文，生成显示索引集合
            context_sets = []  # list of (start, end) tuples
            for group in groups:
                start = max(0, group[0] - 5)
                end = min(len(self.results) - 1, group[-1] + 5)
                context_sets.append((start, end))

            # 合并有重叠的显示区间
            merged = []
            for start, end in context_sets:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))

            # 插入行和分隔符
            for gi, (start, end) in enumerate(merged):
                for idx in range(start, end + 1):
                    self.data_table.add_row(self.results[idx])
                if gi < len(merged) - 1:
                    self.data_table.add_separator()

            total = sum(end - start + 1 for start, end in merged)
            self.log(f"筛选完成，{len(abnormal_indices)} 条异常分 {len(groups)} 组，显示 {total} 条记录")
            return

    def on_rows_deleted(self, deleted_items, deleted_data):
        """
        行删除回调函数

        Args:
            deleted_items: 被删除的treeview项ID列表
            deleted_data: 被删除的行数据列表
        """
        if not self.results:
            return

        self.log(f"删除 {len(deleted_items)} 行数据")
        self.log(f"删除前的results数量: {len(self.results)}")
        self.log(f"删除数据: {[(d.get('frame'), d.get('timestamp')) for d in deleted_data]}")

        # 从results中删除对应的记录
        # 由于treeview删除后无法直接映射到results索引，我们需要根据数据匹配
        new_results = []
        deleted_count = 0

        for result in self.results:
            # 检查当前结果是否在删除的数据中
            should_delete = False
            for deleted in deleted_data:
                # 比较关键字段：frame（应该是唯一的）
                # 处理可能的类型不匹配（int vs str）
                result_frame = result.get('frame')
                deleted_frame = deleted.get('frame')

                # 确保两者都是整数进行比较
                try:
                    result_frame_int = int(result_frame) if result_frame is not None else None
                    deleted_frame_int = int(deleted_frame) if deleted_frame is not None else None
                except (ValueError, TypeError):
                    # 如果转换失败，使用原始值
                    result_frame_int = result_frame
                    deleted_frame_int = deleted_frame

                if result_frame_int is not None and deleted_frame_int is not None:
                    if result_frame_int == deleted_frame_int:
                        should_delete = True
                        self.log(f"匹配到要删除的记录: frame={result_frame_int}")
                        break

            if not should_delete:
                new_results.append(result)
            else:
                deleted_count += 1

        # 更新结果列表
        self.results = new_results
        self.log(f"从结果列表中删除了 {deleted_count} 条记录，删除后的results数量: {len(self.results)}")
        # 更新缓存
        self.update_cache()

    def update_cache(self):
        """更新缓存：将当前results和events保存到缓存"""
        try:
            if self.video_path and self.results is not None:
                video_hash = self.cache_manager.compute_video_hash(self.video_path)
                self.cache_manager.save_results(video_hash, self.results)
                self.cache_manager.save_events(video_hash, self.events)
                self.log(f"缓存已更新 (hash: {video_hash}, 记录数: {len(self.results)}, 事件数: {len(self.events)})")
        except Exception as e:
            self.log(f"更新缓存失败: {e}")

    def create_center_panel(self):
        """创建中心展示面板"""
        center_frame = ttk.Frame(self)
        center_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # 创建控制面板（顶部）
        self.create_top_panel(parent=center_frame)

        # 内层Notebook（数据表格 + 日志）
        self.notebook = ttk.Notebook(center_frame)
        self.notebook.pack(fill="both", expand=True)

        # 数据表格标签页
        self.data_tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.data_tab_frame, text="数据表格")

        # 使用 PanedWindow 实现可拖拽的 70/30 分割
        self.data_paned = ttk.PanedWindow(self.data_tab_frame, orient="horizontal")
        self.data_paned.pack(fill="both", expand=True)

        # 左：数据表格（初始占 70%）
        self.data_table = DataTable(self.data_paned)
        self.data_paned.add(self.data_table, weight=70)

        # 右：事件表格（初始占 30%）
        self.events_frame = ttk.Frame(self.data_paned)
        self.data_paned.add(self.events_frame, weight=30)
        self.create_events_tab()

        # 日志标签页
        self.log_text = tk.Text(self.notebook, height=20, wrap="word")
        self.log_scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=self.log_scrollbar.set)
        self.notebook.add(self.log_text, text="日志")

        # 设置帧查看器回调
        self.data_table.set_view_frame_callback(self.open_frame_viewer)
        # 设置行删除回调
        self.data_table.set_rows_deleted_callback(self.on_rows_deleted)

    def create_bottom_panel(self):
        """创建底部状态栏"""
        bottom_frame = ttk.Frame(self, relief="sunken", borderwidth=1)
        bottom_frame.pack(side="bottom", fill="x", padx=10, pady=5)

        # 状态标签
        self.status_label = ttk.Label(bottom_frame, text="就绪")
        self.status_label.pack(side="left", padx=10, pady=5)

        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(bottom_frame, variable=self.progress_var,
                                           maximum=100, length=200)
        self.progress_bar.pack(side="left", padx=10, pady=5)

        # 进度文本
        self.progress_label = ttk.Label(bottom_frame, text="0/0")
        self.progress_label.pack(side="left", padx=10, pady=5)

    # ===== 事件处理方法 =====

    def open_video(self):
        """打开视频文件"""
        video_path = self.extractor.select_video()
        if video_path:
            # 清空上一个视频的数据
            self.clear_video_data()

            self.video_path = video_path
            self.video_label.config(text=os.path.basename(video_path))
            self.update_status(f"已选择视频: {os.path.basename(video_path)}")

            # 计算视频hash
            try:
                video_hash = self.cache_manager.compute_video_hash(video_path)
                self.log(f"视频hash: {video_hash}")

                # 检查缓存是否有效
                if self.cache_manager.check_cache_valid(video_path, video_hash):
                    self.log("缓存有效，尝试加载缓存数据...")

                    # 加载ROI配置
                    cached_data = self.cache_manager.load_rois(video_hash)
                    if cached_data:
                        self.rois = cached_data['rois']
                        self.roi_status_label.config(text="已配置（从缓存）")
                        self.log(f"从缓存加载ROI配置: {len(self.rois)}个区域")

                        # 恢复旋转角度
                        angle = cached_data.get('rotation_angle')
                        if angle is not None:
                            self.rotation_angle_var.set(str(angle))
                            self.extractor.rotation_angle = float(angle)
                            self.log(f"从缓存恢复旋转角度: {angle}")

                        # 加载识别结果
                        cached_results = self.cache_manager.load_results(video_hash)
                        if cached_results:
                            self.results = cached_results
                            self.data_table.clear()
                            for result in self.results:
                                self.data_table.add_row(result)
                            self.log(f"从缓存加载识别结果: {len(cached_results)}条记录")
                            self.update_status(f"已从缓存加载{len(cached_results)}条记录")

                            # 从缓存加载事件
                            cached_events = self.cache_manager.load_events(video_hash)
                            if cached_events:
                                self.events = cached_events
                                self.log(f"从缓存加载事件: {len(cached_events)}条")
                                self.refresh_events_display()

                            # 启用异常检测相关控件
                            self.temp_diff_entry.config(state="normal")
                            self.detect_abnormal_button.config(state="normal")

                        # 启用开始处理按钮和ROI按钮
                        self.start_button.config(state="normal")
                        self.enable_roi_buttons()
                        self.log("缓存加载完成，可以开始处理或重新框选ROI")
                    else:
                        self.log("缓存中没有ROI配置，需要手动框选")
                        # 启用ROI选择按钮
                        self.enable_roi_buttons()
                else:
                    self.log("缓存无效或不存在，需要手动框选ROI")
                    # 启用ROI选择按钮
                    self.enable_roi_buttons()

            except Exception as e:
                self.log(f"缓存检查失败: {e}")
                # 出错时启用ROI选择按钮
                self.enable_roi_buttons()

            self.log(f"打开视频: {video_path}")

    def select_roi(self):
        """选择ROI区域"""
        self.log("开始框选ROI...")
        if not self.video_path:
            messagebox.showwarning("警告", "请先选择视频文件")
            return

        # 如果已有ROI，提示确认重选
        if self.rois is not None:
            if not messagebox.askyesno("确认", "重新框选ROI会清空现有的数据，是否继续？"):
                return
            # 清空数据（但保留事件列表）
            self.results = []
            self.data_table.clear()
            self.rois = None
            self.roi_status_label.config(text="未配置")
            self.start_button.config(state="disabled")

        # 使用RoiSelector模态对话框（主线程运行，无需后台线程）
        try:
            from ui.roi_selector import RoiSelector
            selector = RoiSelector(
                self,
                self.video_path
            )
            rois = selector.get_results()

            if rois:
                self.rois = rois
                self.on_roi_selected(rois)
            else:
                self.on_roi_selection_failed()
        except Exception as e:
            self.on_roi_selection_error(str(e))
            import traceback
            traceback.print_exc()

    def on_roi_selected(self, rois):
        """ROI选择成功回调"""
        self.roi_status_label.config(text="已配置")
        self.update_status("ROI选择完成")
        self.log(f"ROI选择完成: {rois}")

        # 保存ROI到缓存
        try:
            if self.video_path:
                video_hash = self.cache_manager.compute_video_hash(self.video_path)
                # 保存视频信息
                self.cache_manager.save_video_info(self.video_path, video_hash)
                # 保存ROI配置（附带旋转角度和启动帧）
                try:
                    angle = float(self.rotation_angle_var.get())
                except ValueError:
                    angle = 5.0
                self.cache_manager.save_rois(video_hash, rois, rotation_angle=angle, start_frame=0)
                self.log(f"ROI配置已保存到缓存 (hash: {video_hash}, 角度: {angle})")
        except Exception as e:
            self.log(f"保存ROI到缓存失败: {e}")

        # 启用开始处理按钮
        self.start_button.config(state="normal")

    def on_roi_selection_failed(self):
        """ROI选择失败回调"""
        messagebox.showwarning("警告", "ROI选择失败或已取消")

    def on_roi_selection_error(self, error_msg):
        """ROI选择错误回调"""
        messagebox.showerror("错误", f"ROI选择过程中出错:\n{error_msg}")

    def show_roi_preview(self):
        """显示ROI预览"""
        if not self.video_path or not self.rois:
            messagebox.showwarning("警告", "请先选择视频和配置ROI")
            return

        self.log(f"显示ROI预览，ROI数量: {len(self.rois) if self.rois else 0}")
        if self.rois:
            for name, roi in self.rois.items():
                self.log(f"  ROI '{name}': {roi}")

        try:
            # 使用FrameViewer显示第一帧的ROI预览
            viewer = FrameViewer(
                parent=self,
                extractor=self.extractor,
                video_path=self.video_path,
                rois=self.rois,
                frame_num=0,  # 第一帧
                timestamp=0.0,
                results={},  # 不需要OCR结果
                rotate_angle=float(self.rotation_angle_var.get())
            )
            viewer.title("ROI预览 - 第一帧")
            self.log("打开ROI预览窗口")

        except Exception as e:
            error_msg = f"打开ROI预览失败: {e}"
            messagebox.showerror("错误", error_msg)
            self.log(error_msg)
            import traceback
            self.log(traceback.format_exc())

    def test_single_frame_processing(self):
        """测试模式：随机挑选100帧完整走一遍识别流程"""
        import random
        try:
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                self.log("错误：无法打开视频文件")
                return

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                fps = 30.0
            self.extractor.get_video_info(self.video_path)

            # 随机选出100个帧号
            sample_count = min(100, total_frames)
            frame_nums = sorted(random.sample(range(total_frames), sample_count))
            self.log(f"=== 测试模式：从 {total_frames} 帧中随机抽选 {sample_count} 帧 ===")

            # 检查ROI
            if 'temp1_normal' not in self.rois or 'temp1_faulty' not in self.rois:
                self.log("错误：缺少必要ROI（temp1_normal / temp1_faulty）")
                cap.release()
                return
            has_temp2_new = 'temp2_normal_3digits' in self.rois and 'temp2_normal_lastdigit' in self.rois
            has_temp2_old = 'temp2_normal' in self.rois

            recognizer = self.extractor._get_digit_recognizer()

            for i, frame_num in enumerate(frame_nums):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                ret, frame = cap.read()
                if not ret:
                    continue

                timestamp = frame_num / fps

                temp1_normal_img = self.extractor.crop_roi(frame, self.rois['temp1_normal'])
                temp1_faulty_img = self.extractor.crop_roi(frame, self.rois['temp1_faulty'])

                # temp2
                if has_temp2_new:
                    temp2_3digits_img = self.extractor.crop_roi(frame, self.rois['temp2_normal_3digits'])
                    temp2_lastdigit_img = self.extractor.crop_roi(frame, self.rois['temp2_normal_lastdigit'])
                elif has_temp2_old:
                    temp2_3digits_img = self.extractor.crop_roi(frame, self.rois['temp2_normal'])
                    temp2_lastdigit_img = None
                else:
                    temp2_3digits_img = None
                    temp2_lastdigit_img = None

                recognizer.set_mode('normal')
                temp1_normal_text, temp1_conf = recognizer.recognize_temperature(temp1_normal_img, digit_count=3)

                # 更新进度条
                progress_pct = int((i + 1) / sample_count * 100)
                if i == 0 or (i + 1) % 10 == 0 or i == sample_count - 1:
                    self.progress_var.set(progress_pct)
                    self.progress_label.config(text=f"{i + 1}/{sample_count}")
                    self.after(0, self.update_idletasks)

                # temp2 识别
                if temp2_lastdigit_img is not None:
                    temp2_3digits_text, temp2_3digits_conf = recognizer.recognize_temperature(temp2_3digits_img, digit_count=3)
                    temp2_lastdigit, temp2_lastdigit_conf, _ = recognizer.multi_digit_recognizer.recognize_single_digit(temp2_lastdigit_img)
                    if temp2_3digits_text and len(temp2_3digits_text) >= 3 and temp2_lastdigit >= 0:
                        temp2_text = f"{temp2_3digits_text[:3]}.{temp2_lastdigit}"
                    else:
                        temp2_text = "????"
                elif temp2_3digits_img is not None:
                    temp2_text, _ = recognizer.recognize_temperature(temp2_3digits_img, digit_count=4)
                else:
                    temp2_text = "????"

                # 故障位
                faulty_digit_result, method = self.extractor.recognize_faulty_digit(temp1_faulty_img)

                if temp1_normal_text and len(temp1_normal_text) >= 3:
                    if faulty_digit_result == -2:
                        faulty_digit = -2
                        temp1_full = "????"
                    elif faulty_digit_result != -1:
                        faulty_digit = faulty_digit_result
                        temp1_full = temp1_normal_text + "." + str(faulty_digit)
                    else:
                        faulty_digit = -1
                        temp1_full = "????"
                else:
                    faulty_digit = -1
                    temp1_full = "????"

                result = self.extractor.build_result(
                    frame=frame_num,
                    timestamp=timestamp,
                    temp1_full=temp1_full,
                    temp1_normal=temp1_normal_text,
                    temp1_faulty_digit=faulty_digit,
                    temp2=temp2_text,
                )
                self.results.append(result)

                if (i + 1) % 10 == 0 or i == 0:
                    self.log(f"  [{i+1}/{sample_count}] 帧 {frame_num}: 豆温={temp1_full}, 风温={temp2_text}")

            cap.release()

            self.data_table.load_all(self.results)
            self.progress_label.config(text=f"{len(self.results)}/{sample_count}")
            self.update_status(f"测试模式完成：{len(self.results)} 条记录")
            self.log(f"=== 测试模式完成：成功处理 {len(self.results)}/{sample_count} 帧 ===")

            # 自动切换到识别失败筛选
            self.filter_color_var.set("红色-识别失败")
            self.apply_color_filter()

        except Exception as e:
            self.log(f"测试模式处理出错: {e}")
            import traceback
            traceback.print_exc()

    def start_processing(self):
        """开始处理视频"""
        if not self.video_path or not self.rois:
            messagebox.showwarning("警告", "请先选择视频和配置ROI")
            return

        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError("采样间隔必须大于0")
        except ValueError as e:
            messagebox.showerror("错误", f"参数错误: {e}")
            return

        # 更新旋转角度到提取器
        try:
            self.extractor.rotation_angle = float(self.rotation_angle_var.get())
        except ValueError:
            self.log(f"无效的旋转角度: {self.rotation_angle_var.get()}，使用默认值5")
            self.extractor.rotation_angle = 5
        # 重置懒加载识别器，确保新角度生效
        self.extractor.digit_recognizer = None
        self.extractor._pipeline = None

        # 测试模式：只处理一帧
        if self.test_mode_var.get():
            self.log("=== 测试模式启动 ===")
            # 禁用开始按钮
            self.start_button.config(state="disabled")
            self.pause_button.config(state="disabled")
            self.stop_button.config(state="disabled")

            # 清空之前的结果
            self.results = []
            self.data_table.clear()

            # 执行单帧测试处理
            self.test_single_frame_processing()

            # 重新启用开始按钮
            self.start_button.config(state="normal")
            return

        # 禁用开始按钮，启用暂停/停止按钮（正常模式）
        self.start_button.config(state="disabled")
        self.pause_button.config(state="normal")
        self.stop_button.config(state="normal")

        # 重置进度
        self._last_progress_pct = -1
        self.progress_var.set(0)
        self.progress_label.config(text="0/0")

        # 清空之前的结果
        self.results = []
        self.data_table.clear()

        # 启动异步处理线程
        self.update_status("正在处理视频...")

        # 启动处理线程
        self.processing_thread = ProcessingThread(
            extractor=self.extractor,
            video_path=self.video_path,
            rois=self.rois,
            interval=interval
        )

        # 连接信号
        self.processing_thread.progress_signal.connect(self.on_processing_progress)
        self.processing_thread.status_signal.connect(self.on_processing_status)
        self.processing_thread.result_signal.connect(self.on_processing_result)
        self.processing_thread.finished_signal.connect(self.on_processing_finished)

        # 开始处理
        self.processing_thread.start()

        self.log(f"开始处理视频: {self.video_path}")
        self.log(f"参数: 间隔={interval}s")

    def pause_processing(self):
        """暂停处理"""
        if self.processing_thread:
            self.extractor.pause_processing()
            self.pause_button.config(text="继续", command=self.resume_processing)
            self.update_status("处理已暂停")

    def resume_processing(self):
        """继续处理"""
        if self.processing_thread:
            self.extractor.resume_processing()
            self.pause_button.config(text="暂停", command=self.pause_processing)
            self.update_status("继续处理...")

    def stop_processing(self):
        """停止处理"""
        if self.processing_thread:
            self.extractor.stop_processing()
            self.update_status("正在停止处理...")

    def on_processing_progress(self, processed, total):
        """处理进度更新回调（降频更新）"""
        pct = int(processed / max(total, 1) * 100)
        if pct == getattr(self, '_last_progress_pct', -1):
            return
        self._last_progress_pct = pct
        self.progress_var.set(pct)
        self.progress_label.config(text=f"{processed}/{total}")

    def on_processing_status(self, message):
        """处理状态更新回调"""
        self.update_status(message)
        self.log(f"状态: {message}")

    def on_processing_result(self, result):
        """处理结果回调（单条记录）—— 只存数据，不实时插入表格"""
        self.results.append(result)

    def on_processing_finished(self, success, message):
        """处理完成回调"""
        if success:
            # 在主线程中加载结果到表格（避免后台线程逐行刷新）
            self.after_idle(self._finish_loading)
        else:
            self.update_status("处理失败")
            self.log(f"处理失败: {message}")
            messagebox.showerror("错误", f"处理失败:\n{message}")
            self.start_button.config(state="normal")
            self.pause_button.config(state="disabled")
            self.stop_button.config(state="disabled")
            self.pause_button.config(text="暂停", command=self.pause_processing)

    def _finish_loading(self):
        """在主线程中加载结果并完成处理（被 on_processing_finished 用 after_idle 调用）"""
        self.update_status("正在加载结果...")
        self.data_table.load_all(self.results)

        self.update_status("处理完成")
        self.log(f"处理完成，共处理 {len(self.results)} 条记录")
        messagebox.showinfo("完成", f"处理完成！\n共处理 {len(self.results)} 条记录")

        # 保存结果到缓存
        try:
            if self.video_path and self.results:
                video_hash = self.cache_manager.compute_video_hash(self.video_path)
                self.cache_manager.save_results(video_hash, self.results)
                self.log(f"识别结果已保存到缓存 (hash: {video_hash}, 记录数: {len(self.results)})")
        except Exception as e:
            self.log(f"保存结果到缓存失败: {e}")

        # 重置按钮状态
        self.start_button.config(state="normal")
        self.pause_button.config(state="disabled")
        self.stop_button.config(state="disabled")
        self.pause_button.config(text="暂停", command=self.pause_processing)

    def toggle_log_display(self):
        """切换日志显示"""
        if self.show_log_var.get():
            self.notebook.add(self.log_text, text="日志")
        else:
            # 隐藏日志标签页
            index = self.notebook.index("日志")
            if index >= 0:
                self.notebook.forget(index)

    def show_help(self):
        """显示帮助信息"""
        help_text = """使用说明：

1. 选择视频：点击"选择视频"按钮选择要处理的视频文件
2. 框选ROI：选择视频后，点击"框选ROI"按钮，按照提示框选三个区域：
   - 温度正常位区域
   - 温度故障位区域
   - 风温区域
3. 设置参数：调整采样间隔（默认0.25秒）
4. 开始处理：点击"开始处理"按钮开始异步处理
5. 查看结果：在数据表格中查看识别结果，双击行可查看对应帧截图

快捷键：
- Ctrl+O: 打开视频
- Ctrl+Q: 退出程序"""
        messagebox.showinfo("使用说明", help_text)

    def show_about(self):
        """显示关于信息"""
        about_text = """SantokrOCR 视频数字提取工具

版本: 1.0.0
作者: SantokrOCR Team
描述: 基于OpenCV和自定义分类器的视频数字提取工具

功能：
- 视频选择与ROI框选
- 异步视频处理
- 数据表格预览与验证
- 故障位LED数字识别
- 结果导出为.slog格式"""
        messagebox.showinfo("关于", about_text)

    def on_closing(self):
        """窗口关闭事件处理"""
        if self.processing_thread and self.processing_thread.is_alive():
            if messagebox.askyesno("确认退出", "处理仍在进行中，确定要退出吗？"):
                self.extractor.stop_processing()
                self.quit()
                self.destroy()
                sys.exit(0)
        else:
            self.quit()
            self.destroy()
            sys.exit(0)

    def open_slog_viewer(self):
        """打开slog viewer显示曲线（单例，重复点击激活已有窗口）"""
        if not self.results:
            self.log("没有数据，无法绘制曲线")
            return

        # 检查是否已有打开的viewer
        if self._slog_viewer is not None:
            try:
                if self._slog_viewer.winfo_exists():
                    self._slog_viewer.lift()
                    self._slog_viewer.focus_set()
                    return
            except tk.TclError:
                pass  # 窗口已销毁，重新创建

        import tempfile
        import json
        from ui.slog_viewer import open_slog_viewer as open_slv

        # 生成临时.slog文件
        video_name = os.path.splitext(os.path.basename(self.video_path or 'export'))[0]
        fd, path = tempfile.mkstemp(
            suffix='.slog',
            prefix=video_name + '_'
        )
        os.close(fd)

        # 构建导出数据
        data = {
            'version': 1,
            'results': self.results,
            'events': self.events,
            'heater_initial': self.heater_initial_var.get(),
            'fan_initial': self.fan_initial_var.get(),
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.log(f"生成临时数据文件: {path}")
        self._slog_viewer = open_slv(self, path)

    # ===== 工具方法 =====

    def update_status(self, message):
        """更新状态栏"""
        self.status_label.config(text=message)
        self.status_label.update()

    def log(self, message):
        """添加日志"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.update()

    def enable_roi_buttons(self):
        """启用ROI相关按钮"""
        self.log("启用ROI按钮...")
        # 递归查找所有包含"ROI"文本的按钮并启用
        def find_roi_buttons(parent, depth=0):
            for widget in parent.winfo_children():
                if isinstance(widget, ttk.Button):
                    text = widget.cget("text")
                    if "ROI" in text:
                        self.log(f"找到ROI按钮: '{text}'，深度: {depth}")
                        widget.config(state="normal")
                # 递归查找子组件
                find_roi_buttons(widget, depth+1)

        find_roi_buttons(self)
        self.log("ROI按钮启用完成")

    def clear_video_data(self):
        """清空当前视频数据（切换到新视频时使用）"""
        # 停止任何正在进行的处理
        if self.processing_thread and self.processing_thread.is_alive():
            self.extractor.stop_processing()
            self.processing_thread = None

        # 清空全部数据
        self.rois = None
        self.results = []
        self.events = []
        self.roi_status_label.config(text="未配置")
        self.data_table.clear()
        self.refresh_events_display()

        # 重置按钮状态
        self.start_button.config(state="disabled")
        self.pause_button.config(state="disabled", text="暂停",
                                  command=self.pause_processing)
        self.stop_button.config(state="disabled")

        # 重置统计面板（StatisticsPanel 没有 clear 方法，跳过）

    def open_realtime(self):
        """打开实时识别窗口（单例）"""
        if self._realtime_window is not None:
            try:
                if self._realtime_window.winfo_exists():
                    self._realtime_window.lift()
                    self._realtime_window.focus_set()
                    return
            except tk.TclError:
                pass
        from ui.camera_realtime_window import CameraRealtimeWindow
        self._realtime_window = CameraRealtimeWindow(self)
        self.log("打开实时识别窗口")

    def open_frame_viewer(self, frame_num, timestamp, data):
        """打开帧查看器窗口"""
        if not self.video_path or not self.rois:
            messagebox.showwarning("警告", "请先选择视频和配置ROI")
            return

        try:
            # 创建并显示帧查看器窗口
            viewer = FrameViewer(
                parent=self,
                extractor=self.extractor,
                video_path=self.video_path,
                rois=self.rois,
                frame_num=frame_num,
                timestamp=timestamp,
                interval=float(self.interval_var.get()),
                results=data,
                events=self.events,
                on_mark_event_callback=self.on_event_marked,
                heater_initial=self.heater_initial_var.get(),
                fan_initial=self.fan_initial_var.get(),
                rotate_angle=float(self.rotation_angle_var.get()),
                on_edit_callback=self.on_edit_record
            )
            self.log(f"打开帧查看器: 帧号={frame_num}, 时间戳={timestamp}")
        except Exception as e:
            error_msg = f"打开帧查看器失败: {e}"
            messagebox.showerror("错误", error_msg)
            self.log(error_msg)

    def on_event_marked(self, event_data, is_overwrite=False):
        """帧查看器中标记事件后的回调"""
        # event_data 已通过共享列表引用添加到 self.events，不需要再次 append
        action = "覆盖" if is_overwrite else "标记"
        self.log(f"事件已{action}: {event_data['type']} @ 帧{event_data['frame']} 时间{event_data['time']:.1f}s")
        self.update_cache()
        self.refresh_events_display()

    def on_edit_record(self, frame_num, temp1_value, temp2_value):
        """帧查看器中手动修正后的回调"""
        for result in self.results:
            if result.get('frame') == frame_num:
                if temp1_value is not None:
                    result['temp1_full'] = temp1_value
                if temp2_value is not None:
                    result['temp2'] = temp2_value
                break

        # 刷新表格显示
        self.data_table.clear()
        for result in self.results:
            self.data_table.add_row(result)
        self.log(f"帧 {frame_num} 已手动修正: 豆温={temp1_value}, 风温={temp2_value}")
        self.update_cache()

    def ensure_initial_events(self):
        """确保存在初始火力/风门事件（时间0秒）"""
        heater_val = self.heater_initial_var.get()
        fan_val = self.fan_initial_var.get()

        has_heater = any(
            ev['type'] == '调整火力' and ev['time'] == 0
            for ev in self.events
        )
        has_fan = any(
            ev['type'] == '调整风门' and ev['time'] == 0
            for ev in self.events
        )

        if not has_heater:
            self.events.append({
                'type': '调整火力', 'frame': 0, 'time': 0.0,
                'value': heater_val
            })
        if not has_fan:
            self.events.append({
                'type': '调整风门', 'frame': 0, 'time': 0.0,
                'value': fan_val
            })

        self.refresh_events_display()
        self.update_cache()

    def on_initial_value_changed(self, event=None):
        """初始火力/风门输入框失去焦点时，验证并同步已有的事件"""
        try:
            heater_val = self.heater_initial_var.get()
            fan_val = self.fan_initial_var.get()
        except tk.TclError:
            return
        updated = False
        for ev in self.events:
            if ev['type'] == '调整火力' and ev['time'] == 0:
                ev['value'] = heater_val
                updated = True
            if ev['type'] == '调整风门' and ev['time'] == 0:
                ev['value'] = fan_val
                updated = True
        if updated:
            self.refresh_events_display()

    def create_events_tab(self):
        """创建事件表格（嵌入数据表格标签页右侧）"""
        # 标题
        title_frame = ttk.Frame(self.events_frame)
        title_frame.pack(fill="x", padx=5, pady=(5, 2))
        ttk.Label(title_frame, text="已标记事件",
                 font=("TkDefaultFont", 10, "bold")).pack(side="left")

        # 事件列表（TreeView）
        columns = ('timestamp', 'type', 'value', 'time_str')
        self.events_tree = ttk.Treeview(self.events_frame, columns=columns, show='headings',
                                        selectmode='extended', height=12)
        self.events_tree.heading('timestamp', text='时间戳')
        self.events_tree.heading('type', text='事件类型')
        self.events_tree.heading('value', text='数值')
        self.events_tree.heading('time_str', text='时间')
        self.events_tree.column('timestamp', width=80, anchor='center')
        self.events_tree.column('type', width=100, anchor='center')
        self.events_tree.column('value', width=60, anchor='center')
        self.events_tree.column('time_str', width=100, anchor='center')

        # 添加滚动条
        tree_scroll = ttk.Scrollbar(self.events_frame, orient="vertical", command=self.events_tree.yview)
        self.events_tree.configure(yscrollcommand=tree_scroll.set)
        self.events_tree.pack(side="left", fill="both", expand=True, padx=(5, 0), pady=5)
        tree_scroll.pack(side="left", fill="y", pady=5)

        # 右键菜单
        self.events_context_menu = tk.Menu(self.events_frame, tearoff=0)
        self.events_context_menu.add_command(label="删除选中事件", command=self.delete_selected_event)
        self.events_tree.bind("<Button-3>", self.show_events_context_menu)

    def show_events_context_menu(self, event):
        """显示事件表格右键菜单"""
        item = self.events_tree.identify_row(event.y)
        if item:
            if item not in self.events_tree.selection():
                self.events_tree.selection_set(item)
            self.events_context_menu.post(event.x_root, event.y_root)

    def refresh_events_display(self):
        """刷新事件列表显示"""
        if not hasattr(self, 'events_tree'):
            return
        for item in self.events_tree.get_children():
            self.events_tree.delete(item)

        for ev in sorted(self.events, key=lambda x: x.get('time', 0)):
            t = ev.get('time', 0)

            mins = int(t // 60)
            secs = int(t % 60)
            time_str = f"{mins:02d}:{secs:02d}"
            display_time_str = f"{mins:02d}:{secs:02d}:{int((t % 1) * 1000):03d}"

            value_str = f"{int(ev['value'])}%" if ev.get('value') is not None else "-"
            self.events_tree.insert('', 'end',
                values=(time_str, ev['type'], value_str, display_time_str))

    def delete_selected_event(self):
        """删除选中事件（支持多选）"""
        selected = self.events_tree.selection()
        if not selected:
            return

        from tkinter import messagebox
        if not messagebox.askyesno("确认删除", f"确定要删除选中的 {len(selected)} 个事件吗？"):
            return

        # 先收集所有要删除的索引，避免删除时索引偏移
        indices_to_delete = set()
        for item in selected:
            values = self.events_tree.item(item, 'values')
            if not values:
                continue
            time_str = values[0]  # 时间戳列
            ev_type = values[1]        # 事件类型列

            # 在 events 列表中查找匹配的事件
            for i, ev in enumerate(self.events):
                t = ev.get('time', 0)
                ev_mins = int(t // 60)
                ev_secs = int(t % 60)
                ev_time_str = f"{ev_mins:02d}:{ev_secs:02d}"
                if ev['type'] == ev_type and ev_time_str == time_str:
                    has_value = values[2] != "-"
                    if has_value:
                        val_str = values[2].rstrip('%')
                        ev_val = ev.get('value')
                        if ev_val is not None and int(ev_val) == int(val_str):
                            indices_to_delete.add(i)
                            break
                    else:
                        if ev.get('value') is None:
                            indices_to_delete.add(i)
                            break

        # 按索引降序删除，避免偏移
        for idx in sorted(indices_to_delete, reverse=True):
            del self.events[idx]

        self.refresh_events_display()
        self.update_cache()
        self.log(f"已删除 {len(indices_to_delete)} 个事件")

    def run(self):
        """运行主循环"""
        self.mainloop()


if __name__ == "__main__":
    app = MainWindow()
    app.mainloop()