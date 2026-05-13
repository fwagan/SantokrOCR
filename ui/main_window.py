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
from ui.sample_collector import SampleCollector
from utils.cache_manager import get_cache_manager


class MainWindow(tk.Tk):
    """主窗口类"""

    def __init__(self):
        super().__init__()

        # 初始化变量
        self.video_path = None
        self.rois = None
        self.results = []
        self.events = []            # 用户标记的事件列表
        self.timer_start_offset = 0.0  # 计时起点偏移量
        self.processing_thread = None
        self.extractor = VideoDigitExtractor()
        self.cache_manager = get_cache_manager()

        # 配置窗口
        self.title("SantokrOCR - 视频数字提取工具")
        self.geometry("1400x800")
        self.minsize(1100, 600)

        # 设置图标（如果有）
        try:
            self.iconbitmap(default='icon.ico')
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

    @property
    def auto_start_var(self):
        """向后兼容性：返回enable_timer_recognition_var"""
        return self.enable_timer_recognition_var

    def create_menu(self):
        """创建菜单栏"""
        menubar = tk.Menu(self)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开视频", command=self.open_video, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="导出CSV", command=self.export_csv, accelerator="Ctrl+S")
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
        self.bind('<Control-s>', lambda e: self.export_csv())
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

        ttk.Label(params_row, text="启用Timer识别:").pack(side="left", padx=(20, 5))
        self.enable_timer_recognition_var = tk.BooleanVar(value=False)  # 默认禁用
        self.timer_checkbox = ttk.Checkbutton(params_row, variable=self.enable_timer_recognition_var,
                                             command=self.on_timer_recognition_toggle)
        self.timer_checkbox.pack(side="left")

        # 手动时间输入控件
        ttk.Label(params_row, text="开始时间 (mm:ss):").pack(side="left", padx=(20, 5))
        self.start_time_var = tk.StringVar(value="00:00")
        self.start_time_entry = ttk.Entry(params_row, textvariable=self.start_time_var, width=8)
        self.start_time_entry.pack(side="left", padx=5)

        # 添加输入验证
        def validate_time_input(new_value):
            if new_value == "": return True
            if len(new_value) > 5: return False
            if ":" not in new_value: return False
            parts = new_value.split(":")
            if len(parts) != 2: return False
            try:
                minutes = int(parts[0])
                seconds = int(parts[1])
                return 0 <= minutes <= 59 and 0 <= seconds <= 59
            except ValueError:
                return False

        vcmd = (self.register(validate_time_input), '%P')
        self.start_time_entry.config(validate="key", validatecommand=vcmd)

        # 测试模式开关
        ttk.Label(params_row, text="测试模式:").pack(side="left", padx=(20, 5))
        self.test_mode_var = tk.BooleanVar(value=False)
        self.test_checkbox = ttk.Checkbutton(params_row, variable=self.test_mode_var)
        self.test_checkbox.pack(side="left")

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

        ttk.Button(control_row, text="样本收集模式", command=self.open_sample_collector).pack(side="left", padx=5)

        # 推断控制行
        inference_row = ttk.Frame(top_frame)
        inference_row.pack(fill="x", pady=5)

        ttk.Button(inference_row, text="推断broken位",
                  command=self.infer_broken_digits).pack(side="left", padx=5)
        ttk.Button(inference_row, text="重新推断选中行",
                  command=self.reinfer_selected).pack(side="left", padx=5)

        # 统计按钮行（导出.alog等）
        stats_row = ttk.Frame(top_frame)
        stats_row.pack(fill="x", pady=5)

        ttk.Label(stats_row, text="初始火力(%):").pack(side="left", padx=(0, 2))
        self.heater_initial_var = tk.DoubleVar(value=60.0)
        ttk.Entry(stats_row, textvariable=self.heater_initial_var, width=6).pack(side="left", padx=2)
        self.heater_initial_var.trace('w', self.on_initial_value_changed)

        ttk.Label(stats_row, text="初始风门(%):").pack(side="left", padx=(10, 2))
        self.fan_initial_var = tk.DoubleVar(value=50.0)
        ttk.Entry(stats_row, textvariable=self.fan_initial_var, width=6).pack(side="left", padx=2)
        self.fan_initial_var.trace('w', self.on_initial_value_changed)

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
        filter_combo = ttk.Combobox(filter_row, textvariable=self.filter_color_var,
                                   values=["全部", "黑色-温差异常", "红色-识别失败", "绿色-可确定", "红色-不一致", "黄色-模糊", "蓝色-可编辑"],
                                   state="readonly", width=20)
        filter_combo.pack(side="left", padx=5)
        filter_combo.bind("<<ComboboxSelected>>", self.apply_color_filter)

    def on_timer_recognition_toggle(self):
        """Timer识别开关切换时的处理"""
        if self.enable_timer_recognition_var.get():
            # 启用timer识别时，禁用手动时间输入
            self.start_time_entry.config(state="disabled")
            self.log("Timer识别已启用")
        else:
            # 禁用timer识别时，启用手动时间输入
            self.start_time_entry.config(state="normal")
            self.log("Timer识别已禁用，请手动输入开始时间")

    def infer_broken_digits(self):
        """遍历所有结果，推断broken位数字"""
        if not self.results:
            self.log("没有可推断的数据")
            return

        # 统计需要推断的记录数
        total = sum(1 for r in self.results if r.get('temp1_faulty_digit') == -2)
        if total == 0:
            self.log("没有需要推断的broken位记录")
            return

        self.log(f"开始推断broken位，共{total}条记录需要处理...")

        processed = 0
        for i, result in enumerate(self.results):
            if result.get('temp1_faulty_digit') == -2:
                # 使用扩展的infer_zero_eight_digit方法，传递上下文参数
                # 注意：current_temp_full可能是"????"，需要构建候选值
                temp1_normal = result.get('temp1_normal', '')
                if temp1_normal and len(temp1_normal) >= 3:
                    # 构建候选温度字符串（格式：xxx0或xxx8）
                    temp_candidate = temp1_normal[:3] + "0"
                else:
                    # 如果没有正常位数据，跳过
                    continue

                # 调用推断方法
                try:
                    digit, category = self.extractor.infer_zero_eight_digit(
                        temp_candidate,
                        None, None,  # prev_temp_full和next_temp_full设为None，使用上下文模式
                        current_idx=i,
                        results=self.results,
                        window_size=10
                    )
                except Exception as e:
                    # 如果推断方法出错（可能因为返回格式不兼容），尝试简单模式
                    self.log(f"推断记录{i}时出错: {e}")
                    digit = -2
                    category = 'no_context'

                # 更新结果
                result['inferred_digit'] = digit if digit != -2 else -2
                result['inference_category'] = category
                result['is_editable'] = category in ['inconsistent', 'ambiguous']

                # 如果推断成功，更新temp1_full
                if digit in [0, 8]:
                    temp1_normal_text = result.get('temp1_normal', '')
                    if temp1_normal_text and len(temp1_normal_text) >= 3:
                        result['temp1_full'] = temp1_normal_text + "." + str(digit)
                        result['quality'] = 'high'

                # 更新表格显示
                self.data_table.update_row_display(i, result)

                processed += 1
                # 更新进度（每10条记录更新一次）
                if processed % 10 == 0 or processed == total:
                    self.log(f"已推断 {processed}/{total} 条记录")

        self.log(f"推断完成，处理了{processed}个broken位")
        # 更新缓存
        self.update_cache()

    def reinfer_selected(self):
        """重新推断选中行"""
        selected_data = self.data_table.get_selected_row()
        if not selected_data:
            self.log("请先选择一行数据")
            return

        # 找到选中行在results中的索引
        selected_index = None
        selected_frame_str = selected_data.get('frame', '')

        # 尝试将选中的frame转换为整数（因为results中的frame是整数）
        try:
            selected_frame_int = int(selected_frame_str)
        except (ValueError, TypeError):
            selected_frame_int = None

        for i, result in enumerate(self.results):
            result_frame = result.get('frame')
            # 尝试两种匹配方式：直接匹配或整数匹配
            if (result_frame == selected_frame_str or
                (selected_frame_int is not None and result_frame == selected_frame_int)):
                selected_index = i
                break

        if selected_index is None:
            self.log("无法找到选中行在数据中的位置")
            return

        result = self.results[selected_index]
        if result.get('temp1_faulty_digit') != -2:
            self.log("选中行不是broken位记录（temp1_faulty_digit != -2）")
            return

        # 重新推断
        temp1_normal = result.get('temp1_normal', '')
        if temp1_normal and len(temp1_normal) >= 3:
            temp_candidate = temp1_normal[:3] + "0"
        else:
            self.log("选中行没有有效的正常位数据")
            return

        try:
            digit, category = self.extractor.infer_zero_eight_digit(
                temp_candidate,
                None, None,
                current_idx=selected_index,
                results=self.results,
                window_size=10
            )
        except Exception as e:
            self.log(f"重新推断时出错: {e}")
            digit = -2
            category = 'no_context'

        # 更新结果
        result['inferred_digit'] = digit if digit != -2 else -2
        result['inference_category'] = category
        result['is_editable'] = category in ['inconsistent', 'ambiguous']

        if digit in [0, 8]:
            temp1_normal_text = result.get('temp1_normal', '')
            if temp1_normal_text and len(temp1_normal_text) >= 3:
                result['temp1_full'] = temp1_normal_text + "." + str(digit)
                result['quality'] = 'high'

        # 更新表格显示
        self.data_table.update_row_display(selected_index, result)
        self.log(f"重新推断完成: 索引={selected_index}, 结果={digit}, 分类={category}")
        # 更新缓存
        self.update_cache()

    def on_cell_edited(self, item, column_id, new_value):
        """
        单元格编辑回调函数

        Args:
            item: treeview项ID
            column_id: 列ID（如'temp1_faulty_digit'）
            new_value: 新值
        """
        if column_id != 'temp1_faulty_digit':
            return

        # 找到项在treeview中的索引
        items = list(self.data_table.tree.get_children())
        try:
            item_index = items.index(item)
        except ValueError:
            self.log(f"无法找到项 {item} 在treeview中的位置")
            return

        if item_index < 0 or item_index >= len(self.results):
            self.log(f"项索引 {item_index} 超出范围")
            return

        # 更新结果数据
        result = self.results[item_index]
        try:
            new_digit = int(new_value)
        except ValueError:
            self.log(f"无效的数字值: {new_value}")
            return

        # 更新故障位数字
        result['temp1_faulty_digit'] = new_digit

        # 如果数字有效（0或8），更新temp1_full
        if new_digit in [0, 8]:
            temp1_normal_text = result.get('temp1_normal', '')
            if temp1_normal_text and len(temp1_normal_text) >= 3:
                result['temp1_full'] = temp1_normal_text + "." + str(new_digit)
                result['quality'] = 'high'
        elif new_digit == -2:
            # 恢复为未知
            result['temp1_full'] = '????'
            result['quality'] = 'low'

        # 清除推断相关字段（因为用户手动修改了）
        result.pop('inferred_digit', None)
        result.pop('inference_category', None)
        result.pop('is_editable', None)

        # 更新表格显示
        self.data_table.update_row_display(item_index, result)
        self.log(f"单元格已更新: 索引={item_index}, {column_id}={new_value}")
        # 更新缓存
        self.update_cache()

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

        # 更新表格显示
        self.data_table.clear()
        for result in self.results:
            self.data_table.add_row(result)

        self.log(f"检测完成，发现{abnormal_count}条异常温差记录")

    def apply_color_filter(self, event=None):
        """应用颜色筛选"""
        if not hasattr(self, 'data_table') or not self.data_table:
            return

        selected_filter = self.filter_color_var.get()
        self.log(f"应用筛选: {selected_filter}")

        # 清空表格
        self.data_table.clear()

        if selected_filter == "全部":
            # 显示所有记录
            for result in self.results:
                self.data_table.add_row(result)
            self.log(f"显示全部 {len(self.results)} 条记录")
            return

        # 根据筛选条件显示记录
        filtered_count = 0
        for result in self.results:
            should_show = False

            if selected_filter == "黑色-温差异常":
                should_show = result.get('abnormal_category') == 'temperature_diff'
            elif selected_filter == "红色-识别失败":
                should_show = result.get('temp1_faulty_digit') == -1
            elif selected_filter == "绿色-可确定":
                should_show = result.get('inference_category') == 'determined'
            elif selected_filter == "红色-不一致":
                should_show = result.get('inference_category') == 'inconsistent'
            elif selected_filter == "黄色-模糊":
                should_show = result.get('inference_category') == 'ambiguous'
            elif selected_filter == "蓝色-可编辑":
                should_show = result.get('is_editable', False)
            else:
                # 未知筛选条件，显示所有
                should_show = True

            if should_show:
                self.data_table.add_row(result)
                filtered_count += 1

        self.log(f"筛选完成，显示 {filtered_count} 条记录")

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

    def on_timer_start(self, frame, original_timestamp):
        """
        将指定行设为计时起点，所有行的时间戳重新计算为相对值

        Args:
            frame: 选中行的帧号
            original_timestamp: 选中行的原始时间戳
        """
        offset = round(-original_timestamp, 3)
        self.timer_start_offset = offset

        self.log(f"设置计时起点: 帧号={frame}, 原始时间戳={original_timestamp}s, "
                 f"偏移量={offset}s")

        # 更新所有结果行的相对时间戳
        for result in self.results:
            rel_ts = round(result['original_timestamp'] + offset, 3)
            result['timestamp'] = rel_ts

            # 时间字符串：仅对非负时间戳计算
            if rel_ts >= 0:
                mins = int(rel_ts // 60)
                secs = int(rel_ts % 60)
                millis = int((rel_ts % 1) * 1000)
                result['time_str'] = f"{mins:02d}:{secs:02d}:{millis:03d}"
            else:
                result['time_str'] = '-'

        # 将初始事件（frame=0, time=0.0 的火力/风门）绑定到计时起点行
        for ev in self.events:
            if ev.get('frame') == 0 and ev.get('time') == 0.0:
                ev['frame'] = frame
                ev['time'] = round(original_timestamp, 1)
                self.log(f"已绑定初始事件 '{ev['type']}' 到帧 {frame}")

        # 重新加载数据表格
        self.data_table.clear()
        for result in self.results:
            self.data_table.add_row(result)

        # 刷新事件显示
        self.refresh_events_display()

        # 保存偏移量到缓存
        try:
            if self.video_path:
                video_hash = self.cache_manager.compute_video_hash(self.video_path)
                self.cache_manager.save_timer_start_offset(video_hash, offset)
        except Exception as e:
            self.log(f"保存计时起点偏移量到缓存失败: {e}")

        # 更新缓存
        self.update_cache()

        self.log(f"计时起点设置完成，共更新 {len(self.results)} 条记录")

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
        # 设置单元格编辑回调
        self.data_table.set_cell_edited_callback(self.on_cell_edited)
        # 设置行删除回调
        self.data_table.set_rows_deleted_callback(self.on_rows_deleted)
        # 设置计时起点回调
        self.data_table.set_timer_start_callback(self.on_timer_start)

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

        # 已处理帧数
        self.processed_label = ttk.Label(bottom_frame, text="已处理: 0")
        self.processed_label.pack(side="right", padx=10, pady=5)

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
                    cached_rois = self.cache_manager.load_rois(video_hash)
                    if cached_rois:
                        self.rois = cached_rois
                        self.roi_status_label.config(text="已配置（从缓存）")
                        self.log(f"从缓存加载ROI配置: {len(cached_rois)}个区域")

                        # 加载识别结果
                        cached_results = self.cache_manager.load_results(video_hash)
                        if cached_results:
                            # 检查缓存是否为旧格式（缺少 original_timestamp）
                            if 'original_timestamp' not in cached_results[0]:
                                self.log("检测到旧格式缓存（缺少original_timestamp），清除并重新处理...")
                                self.cache_manager.clear_cache(video_hash)
                                cached_results = None
                                self.results = []
                            else:
                                self.results = cached_results
                                self.data_table.clear()
                                for result in self.results:
                                    self.data_table.add_row(result)
                                self.log(f"从缓存加载识别结果: {len(cached_results)}条记录")
                                self.update_status(f"已从缓存加载{len(cached_results)}条记录")

                                # 加载计时起点偏移量
                                self.timer_start_offset = self.cache_manager.load_timer_start_offset(video_hash)
                                if self.timer_start_offset != 0:
                                    self.log(f"从缓存加载计时起点偏移量: {self.timer_start_offset}")

                                # 从缓存加载事件
                                cached_events = self.cache_manager.load_events(video_hash)
                                if cached_events:
                                    self.events = cached_events
                                    self.log(f"从缓存加载事件: {len(cached_events)}条")
                                    self.refresh_events_display()

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
                self.video_path,
                enable_timer=self.enable_timer_recognition_var.get()
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
                # 保存ROI配置
                self.cache_manager.save_rois(video_hash, rois)
                self.log(f"ROI配置已保存到缓存 (hash: {video_hash})")
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
                results={}  # 不需要OCR结果
            )
            viewer.title("ROI预览 - 第一帧")
            self.log("打开ROI预览窗口")

        except Exception as e:
            error_msg = f"打开ROI预览失败: {e}"
            messagebox.showerror("错误", error_msg)
            self.log(error_msg)
            import traceback
            self.log(traceback.format_exc())

    def test_single_frame_processing(self, start_frame):
        """测试模式：只处理一帧并显示详细步骤"""
        try:
            self.log("=== 测试模式：开始处理单帧 ===")

            # 打开视频
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                self.log("错误：无法打开视频文件")
                return

            # 跳转到指定帧
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            ret, frame = cap.read()
            if not ret:
                self.log(f"错误：无法读取帧 {start_frame}")
                cap.release()
                return

            fps = cap.get(cv2.CAP_PROP_FPS)
            timestamp = start_frame / fps
            self.log(f"处理帧: {start_frame}, 时间戳: {timestamp:.3f}s, FPS: {fps:.2f}")

            # 裁剪ROI区域
            self.log("裁剪ROI区域:")
            for roi_name, roi in self.rois.items():
                self.log(f"  {roi_name}: {roi}")
                # 实际裁剪
                roi_img = self.extractor.crop_roi(frame, roi)
                # 保存图像用于调试（可选）

            # 提取各个ROI图像
            if 'temp1_normal' in self.rois:
                temp1_normal_img = self.extractor.crop_roi(frame, self.rois['temp1_normal'])
                self.log("已裁剪temp1_normal区域")
            else:
                self.log("错误：缺少temp1_normal ROI")
                cap.release()
                return

            if 'temp1_faulty' in self.rois:
                temp1_faulty_img = self.extractor.crop_roi(frame, self.rois['temp1_faulty'])
                self.log("已裁剪temp1_faulty区域")
            else:
                self.log("错误：缺少temp1_faulty ROI")
                cap.release()
                return

            # 检查temp2 ROI（新格式：两个ROI）
            if 'temp2_normal_3digits' in self.rois and 'temp2_normal_lastdigit' in self.rois:
                temp2_3digits_img = self.extractor.crop_roi(frame, self.rois['temp2_normal_3digits'])
                temp2_lastdigit_img = self.extractor.crop_roi(frame, self.rois['temp2_normal_lastdigit'])
                self.log("已裁剪temp2_normal_3digits和temp2_normal_lastdigit区域")
            elif 'temp2_normal' in self.rois:
                # 向后兼容：旧格式，单个ROI包含4位数字
                temp2_normal_img = self.extractor.crop_roi(frame, self.rois['temp2_normal'])
                self.log("已裁剪temp2_normal区域（旧格式，向后兼容）")
                temp2_3digits_img = temp2_normal_img
                temp2_lastdigit_img = None
            else:
                self.log("错误：缺少temp2 ROI（需要temp2_normal_3digits和temp2_normal_lastdigit）")
                cap.release()
                return

            # Timer ROI（可选）
            timer_img = None
            if 'timer' in self.rois:
                timer_img = self.extractor.crop_roi(frame, self.rois['timer'])
                self.log("已裁剪timer区域")
            else:
                self.log("Timer ROI未选择（正常，当Timer识别禁用时）")

            # 初始化数字识别器
            self.log("初始化数字识别器...")
            recognizer = self.extractor._get_digit_recognizer()

            # 识别正常位温度（正常模式）
            self.log("识别正常位温度（正常模式）...")
            recognizer.set_mode('normal')

            # 识别temp1_normal（3位数字）
            temp1_normal_text, temp1_conf = recognizer.recognize_temperature(temp1_normal_img, digit_count=3)
            self.log(f"temp1_normal识别结果: {temp1_normal_text}, 置信度: {temp1_conf:.3f}")

            # 识别temp2（新格式：两个ROI分别识别）
            if temp2_lastdigit_img is not None:
                # 新格式：分别识别3位数字和最后1位数字
                temp2_3digits_text, temp2_3digits_conf = recognizer.recognize_temperature(temp2_3digits_img, digit_count=3)
                temp2_lastdigit, temp2_lastdigit_conf = recognizer.multi_digit_recognizer.recognize_single_digit(temp2_lastdigit_img)
                # 组合temp2温度值：xxx.x格式
                if temp2_3digits_text and len(temp2_3digits_text) >= 3 and temp2_lastdigit >= 0:
                    temp2_text = f"{temp2_3digits_text[:3]}.{temp2_lastdigit}"
                    temp2_conf = (temp2_3digits_conf + temp2_lastdigit_conf) / 2
                else:
                    temp2_text = "????"
                    temp2_conf = 0.0
                self.log(f"temp2识别结果: {temp2_text}, 置信度: {temp2_conf:.3f} (新格式)")
            else:
                # 旧格式：单个ROI包含4位数字
                temp2_text, temp2_conf = recognizer.recognize_temperature(temp2_3digits_img, digit_count=4)
                self.log(f"temp2识别结果: {temp2_text}, 置信度: {temp2_conf:.3f} (旧格式，向后兼容)")

            # 识别timer（如果存在）
            timer_text, timer_conf = None, 0.0
            if timer_img is not None:
                timer_text, timer_conf = recognizer.recognize_timer(timer_img)
                self.log(f"timer识别结果: {timer_text}, 置信度: {timer_conf:.3f}")

            # 故障位数字识别（故障位模式）
            self.log("故障位数字识别（故障位模式）...")
            faulty_digit_result, method = self.extractor.recognize_faulty_digit(temp1_faulty_img)
            self.log(f"故障位识别结果: 数字={faulty_digit_result}, 方法={method}")

            # 组合完整温度值（逻辑复用process_video_async中的逻辑）
            temp1_full = "????"
            faulty_digit = -1
            quality = 'low'

            if temp1_normal_text and len(temp1_normal_text) >= 3:
                if faulty_digit_result == -2:
                    # 数字0/8情况
                    temp_candidate_0 = temp1_normal_text[:3] + "0"
                    temp_candidate_8 = temp1_normal_text[:3] + "8"
                    self.log(f"数字0/8情况，候选温度: {temp_candidate_0} 或 {temp_candidate_8}")
                    # 单帧无法推断，标记为-2
                    faulty_digit = -2
                    temp1_full = "????"
                    quality = 'low'
                    self.log("单帧无法推断0/8，需要时间序列推断")
                elif faulty_digit_result != -1:
                    # 成功识别数字
                    faulty_digit = faulty_digit_result
                    temp1_full = temp1_normal_text + "." + str(faulty_digit)
                    quality = 'high'
                    self.log(f"成功组合完整温度: {temp1_full}")
                else:
                    # 无法识别故障位数字
                    faulty_digit = -1
                    temp1_full = "????"
                    quality = 'low'
                    self.log("无法识别故障位数字")
            else:
                # 正常位识别失败
                faulty_digit = -1
                temp1_full = "????"
                quality = 'low'
                self.log("正常位识别失败")

            # 构建结果记录
            result = {
                'frame': start_frame,
                'timestamp': round(timestamp, 3),
                'original_timestamp': round(timestamp, 3),
                'time_str': f"{int(timestamp//60):02d}:{int(timestamp%60):02d}:{int((timestamp%1)*1000):03d}",
                'timer': timer_text,
                'temp1_full': temp1_full,
                'temp1_normal': temp1_normal_text if temp1_normal_text else "????",
                'temp1_faulty_digit': faulty_digit,
                'temp2': temp2_text if temp2_text else "????",
                'quality': quality
            }

            self.log("=== 处理结果 ===")
            for key, value in result.items():
                self.log(f"  {key}: {value}")

            # 添加到结果表格
            self.results.append(result)
            self.data_table.add_row(result)
            self.log(f"结果已添加到表格，共 {len(self.results)} 条记录")

            # 更新UI状态
            self.progress_var.set(100)
            self.progress_label.config(text="1/1")
            self.processed_label.config(text="已处理: 1")
            self.update_status("测试模式处理完成")

            cap.release()
            self.log("=== 测试模式：处理完成 ===")

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

            # 获取启动帧（与正常模式相同逻辑）
            start_frame = 0
            if self.enable_timer_recognition_var.get():
                # 启用timer识别：自动检测启动帧
                try:
                    if 'timer' in self.rois:
                        start_frame = self.extractor.find_start_frame(self.video_path, self.rois['timer'])
                        self.log(f"Timer识别已启用，自动检测到启动帧: {start_frame}")
                    else:
                        self.log("警告：Timer识别已启用但未选择timer区域，使用默认启动帧0")
                        start_frame = 0
                except Exception as e:
                    self.log(f"Timer识别失败: {e}")
                    start_frame = 0
            else:
                # 禁用timer识别：手动输入时间转换为帧数
                try:
                    time_str = self.start_time_var.get()
                    minutes, seconds = map(int, time_str.split(":"))

                    # 获取视频FPS
                    cap = cv2.VideoCapture(self.video_path)
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    cap.release()

                    # 计算帧数：时间(秒) × FPS
                    total_seconds = minutes * 60 + seconds
                    start_frame = int(total_seconds * fps)

                    self.log(f"Timer识别已禁用，使用手动输入时间: {time_str}")
                    self.log(f"转换为启动帧: {start_frame} (FPS: {fps:.2f})")
                except Exception as e:
                    self.log(f"时间转换失败: {e}，使用默认启动帧0")
                    start_frame = 0

            # 执行单帧测试处理
            self.test_single_frame_processing(start_frame)

            # 重新启用开始按钮
            self.start_button.config(state="normal")
            return

        # 禁用开始按钮，启用暂停/停止按钮（正常模式）
        self.start_button.config(state="disabled")
        self.pause_button.config(state="normal")
        self.stop_button.config(state="normal")

        # 重置进度
        self.progress_var.set(0)
        self.progress_label.config(text="0/0")
        self.processed_label.config(text="已处理: 0")

        # 清空之前的结果
        self.results = []
        self.data_table.clear()

        # 启动异步处理线程
        self.update_status("正在处理视频...")

        # 获取启动帧
        start_frame = 0
        if self.enable_timer_recognition_var.get():
            # 启用timer识别：自动检测启动帧
            try:
                if 'timer' in self.rois:
                    start_frame = self.extractor.find_start_frame(self.video_path, self.rois['timer'])
                    self.log(f"Timer识别已启用，自动检测到启动帧: {start_frame}")
                else:
                    self.log("警告：Timer识别已启用但未选择timer区域，使用默认启动帧0")
                    start_frame = 0
            except Exception as e:
                self.log(f"Timer识别失败: {e}")
                start_frame = 0
        else:
            # 禁用timer识别：手动输入时间转换为帧数
            try:
                time_str = self.start_time_var.get()
                minutes, seconds = map(int, time_str.split(":"))

                # 获取视频FPS
                cap = cv2.VideoCapture(self.video_path)
                fps = cap.get(cv2.CAP_PROP_FPS)
                cap.release()

                # 计算帧数：时间(秒) × FPS
                total_seconds = minutes * 60 + seconds
                start_frame = int(total_seconds * fps)

                self.log(f"Timer识别已禁用，使用手动输入时间: {time_str}")
                self.log(f"转换为启动帧: {start_frame} (FPS: {fps:.2f})")
            except Exception as e:
                self.log(f"时间转换失败: {e}，使用默认启动帧0")
                start_frame = 0

        # 启动处理线程
        self.processing_thread = ProcessingThread(
            extractor=self.extractor,
            video_path=self.video_path,
            rois=self.rois,
            start_frame=start_frame,
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
        self.log(f"参数: 间隔={interval}s, 启动帧={start_frame}")

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
        """处理进度更新回调"""
        self.progress_var.set(processed / total * 100 if total > 0 else 0)
        self.progress_label.config(text=f"{processed}/{total}")
        self.processed_label.config(text=f"已处理: {processed}")

    def on_processing_status(self, message):
        """处理状态更新回调"""
        self.update_status(message)
        self.log(f"状态: {message}")

    def on_processing_result(self, result):
        """处理结果回调（单条记录）"""
        self.results.append(result)
        self.data_table.add_row(result)

    def on_processing_finished(self, success, message):
        """处理完成回调"""
        if success:
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

        else:
            self.update_status("处理失败")
            self.log(f"处理失败: {message}")
            messagebox.showerror("错误", f"处理失败:\n{message}")

        # 重置按钮状态
        self.start_button.config(state="normal")
        self.pause_button.config(state="disabled")
        self.stop_button.config(state="disabled")
        self.pause_button.config(text="暂停", command=self.pause_processing)

    def export_csv(self):
        """导出CSV结果"""
        if not self.results:
            messagebox.showwarning("警告", "没有可导出的数据")
            return

        output_path = filedialog.asksaveasfilename(
            title="保存CSV文件",
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")]
        )

        if output_path:
            try:
                # 使用extractor的导出方法
                self.extractor.export_to_csv(self.results, output_path)
                self.update_status(f"结果已导出到: {os.path.basename(output_path)}")
                self.log(f"导出完成: {output_path}")
                messagebox.showinfo("导出成功", f"结果已导出到:\n{output_path}")
            except Exception as e:
                messagebox.showerror("导出失败", f"导出过程中出错:\n{e}")

    def open_sample_collector(self):
        """打开样本收集器"""
        if not self.video_path:
            messagebox.showwarning("警告", "请先选择视频文件")
            return

        if not self.rois or 'temp1_faulty' not in self.rois:
            messagebox.showwarning("警告", "请先配置ROI区域（特别是故障位区域）")
            return

        try:
            # 获取故障位ROI
            faulty_roi = self.rois['temp1_faulty']

            # 创建样本收集器窗口
            collector = SampleCollector(
                parent=self,
                extractor=self.extractor,
                video_path=self.video_path,
                faulty_roi=faulty_roi,
                start_frame=0,  # 从第0帧开始
                num_samples=50  # 默认收集50个样本
            )

            self.log(f"打开样本收集器: 故障位ROI={faulty_roi}")

        except Exception as e:
            error_msg = f"打开样本收集器失败: {e}"
            messagebox.showerror("错误", error_msg)
            self.log(error_msg)

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
   - 计时器区域
   - 温度正常位区域
   - 温度故障位区域
3. 设置参数：调整采样间隔（默认0.25秒）
4. 开始处理：点击"开始处理"按钮开始异步处理
5. 查看结果：在数据表格中查看识别结果，双击行可查看对应帧截图
6. 导出结果：点击"导出CSV"保存结果

快捷键：
- Ctrl+O: 打开视频
- Ctrl+S: 导出CSV
- Ctrl+Q: 退出程序"""
        messagebox.showinfo("使用说明", help_text)

    def show_about(self):
        """显示关于信息"""
        about_text = """SantokrOCR 视频数字提取工具

版本: 1.0.0
作者: SantokrOCR Team
描述: 基于PaddleOCR和自定义分类器的视频数字提取工具

功能：
- 视频选择与ROI框选
- 异步视频处理
- 数据表格预览与验证
- 故障位LED数字识别
- 结果导出为CSV格式"""
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
        """打开slog viewer显示曲线"""
        if not self.results:
            self.log("没有数据，无法绘制曲线")
            return

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
        open_slv(self, path)

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
        self.timer_start_offset = 0.0
        self.roi_status_label.config(text="未配置")
        self.data_table.clear()
        self.refresh_events_display()

        # 重置按钮状态
        self.start_button.config(state="disabled")
        self.pause_button.config(state="disabled", text="暂停",
                                  command=self.pause_processing)
        self.stop_button.config(state="disabled")

        # 重置统计面板（StatisticsPanel 没有 clear 方法，跳过）

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
                results=data,
                events=self.events,
                on_mark_event_callback=self.on_event_marked,
                heater_initial=self.heater_initial_var.get(),
                fan_initial=self.fan_initial_var.get()
            )
            self.log(f"打开帧查看器: 帧号={frame_num}, 原始时间戳={timestamp}")
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

    def on_initial_value_changed(self, *args):
        """初始火力/风门值改变时同步更新已有的事件"""
        heater_val = self.heater_initial_var.get()
        fan_val = self.fan_initial_var.get()
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
        columns = ('timestamp', 'original_time', 'type', 'value', 'time_str')
        self.events_tree = ttk.Treeview(self.events_frame, columns=columns, show='headings',
                                        selectmode='extended', height=12)
        self.events_tree.heading('timestamp', text='时间戳')
        self.events_tree.heading('original_time', text='原始时间戳')
        self.events_tree.heading('type', text='事件类型')
        self.events_tree.heading('value', text='数值')
        self.events_tree.heading('time_str', text='时间字符串')
        self.events_tree.column('timestamp', width=80, anchor='center')
        self.events_tree.column('original_time', width=80, anchor='center')
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
            orig_t = ev.get('time', 0)
            rel_t = orig_t + self.timer_start_offset

            # 原始时间戳（MM:SS）
            orig_mins = int(orig_t // 60)
            orig_secs = int(orig_t % 60)
            orig_time_str = f"{orig_mins:02d}:{orig_secs:02d}"

            # 相对时间戳（MM:SS）
            rel_mins = int(rel_t // 60)
            rel_secs = int(rel_t % 60)
            rel_time_str = f"{rel_mins:02d}:{rel_secs:02d}"

            # 时间字符串（仅非负相对时间戳）
            if rel_t >= 0:
                display_time_str = f"{rel_mins:02d}:{rel_secs:02d}:{int((rel_t % 1) * 1000):03d}"
            else:
                display_time_str = '-'

            value_str = f"{int(ev['value'])}%" if ev.get('value') is not None else "-"
            self.events_tree.insert('', 'end',
                values=(rel_time_str, orig_time_str, ev['type'], value_str, display_time_str))

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
            orig_time_str = values[1]  # 原始时间戳列
            ev_type = values[2]        # 事件类型列

            # 在 events 列表中查找匹配的事件
            for i, ev in enumerate(self.events):
                t = ev.get('time', 0)
                ev_mins = int(t // 60)
                ev_secs = int(t % 60)
                ev_time_str = f"{ev_mins:02d}:{ev_secs:02d}"
                if ev['type'] == ev_type and ev_time_str == orig_time_str:
                    has_value = values[3] != "-"
                    if has_value:
                        val_str = values[3].rstrip('%')
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