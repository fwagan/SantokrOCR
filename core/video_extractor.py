"""
视频数字提取器

主要功能：
1. 视频选择和ROI框选
2. 自动定位启动时间点
3. 异步视频处理
4. 帧缓存和快速帧访问
"""

import cv2
import numpy as np
import pandas as pd
from .digit_recognizer import DigitRecognizer
import tkinter as tk
from tkinter import filedialog, messagebox
import os
from datetime import datetime
import threading
from functools import lru_cache
import time
from queue import Queue

from .led_classifier import LEDDigitClassifier


class FrameCache:
    """帧缓存管理（LRU缓存）"""

    def __init__(self, maxsize=100):
        self.cache = {}
        self.lock = threading.Lock()
        self.maxsize = maxsize
        self.access_order = []  # 用于LRU策略

    def get_frame(self, video_path, timestamp):
        """
        获取指定时间戳的视频帧（带缓存）
        返回: (ret, frame) 或 None
        """
        key = (video_path, timestamp)

        with self.lock:
            # 检查缓存
            if key in self.cache:
                # 更新访问顺序
                self.access_order.remove(key)
                self.access_order.append(key)
                return self.cache[key]

        # 缓存未命中，从视频读取
        frame = self._read_frame_from_video(video_path, timestamp)
        if frame is not None:
            with self.lock:
                # 添加到缓存
                self.cache[key] = frame
                self.access_order.append(key)

                # 如果超过最大大小，移除最久未使用的
                if len(self.cache) > self.maxsize:
                    oldest_key = self.access_order.pop(0)
                    del self.cache[oldest_key]

        return frame

    def _read_frame_from_video(self, video_path, timestamp):
        """从视频文件读取指定时间戳的帧"""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_num = int(timestamp * fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        cap.release()

        if ret:
            return frame
        return None

    def clear(self):
        """清空缓存"""
        with self.lock:
            self.cache.clear()
            self.access_order.clear()



class VideoDigitExtractor:
    """视频数字提取器主类"""

    def __init__(self):
        self.digit_recognizer = None  # 替代PaddleOCR的数字识别器
        self.faulty_classifier = LEDDigitClassifier()
        self.rois = {}  # 存储三个ROI区域
        self.start_frame = 0
        self.frame_cache = FrameCache(maxsize=50)
        self._pipeline = None  # 统一识别管道（懒加载）
        self.processing_stats = {
            'total_frames': 0,
            'processed_frames': 0,
            'current_status': 'idle',
            'start_time': None,
            'elapsed_time': 0
        }

    def _get_digit_recognizer(self):
        """获取数字识别器实例（懒加载）"""
        if self.digit_recognizer is None:
            self.digit_recognizer = DigitRecognizer()
        return self.digit_recognizer

    def _get_recognition_pipeline(self):
        """获取统一识别管道实例（懒加载）"""
        if self._pipeline is None:
            from .digit_recognition_pipeline import DigitRecognitionPipeline
            self._pipeline = DigitRecognitionPipeline(is_debug=False)
        return self._pipeline

    def select_video(self):
        """选择视频文件"""
        root = tk.Tk()
        root.withdraw()
        video_path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[("视频文件", "*.mp4 *.mov *.avi *.mkv"), ("所有文件", "*.*")]
        )
        return video_path

    def find_start_frame(self, video_path, timer_roi):
        """自动找到计时器开始变化的帧（00:00:01的前1秒）"""
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)

        # 初始化数字识别器
        recognizer = self._get_digit_recognizer()

        prev_text = None
        frame_count = 0

        print("正在定位启动时间点...")

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # 每隔0.5秒检测一次（加快速度）
            if frame_count % int(fps * 0.5) == 0:
                x, y, w, h = timer_roi
                timer_img = frame[y:y+h, x:x+w]

                # 设置正常模式识别时间
                recognizer.set_mode('normal')
                timer_text, _ = recognizer.recognize_timer(timer_img)
                if timer_text:
                    text = timer_text  # 已经是格式化字符串，如"00:00:00"

                    # 检查是否变为00:00:01
                    if text == "00:00:01" and prev_text == "00:00:00":
                        start_second = frame_count / fps - 1  # 前1秒
                        cap.release()
                        return int(start_second * fps)

                    prev_text = text

            frame_count += 1

        cap.release()
        # 如果没找到，返回第10秒（保守估计）
        return int(10 * fps)

    def collect_faulty_samples(self, video_path, faulty_roi, start_frame, num_samples=50):
        """
        收集故障位的训练样本
        返回 [(image, label)]，需要用户手动标注
        """
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)

        # 跳转到启动帧附近
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        samples = []
        frame_count = start_frame

        print(f"请准备标注{num_samples}个故障位数字样本...")

        while len(samples) < num_samples:
            ret, frame = cap.read()
            if not ret:
                break

            # 每隔10帧取一个样本
            if frame_count % 10 == 0:
                x, y, w, h = faulty_roi
                digit_img = frame[y:y+h, x:x+w]

                # 显示图片，让用户输入数字
                cv2.imshow("请输入这个数字 (0-9)，按q退出", digit_img)
                key = cv2.waitKey(0)

                if key == ord('q'):
                    break

                # 数字键 0-9
                if 48 <= key <= 57:
                    digit = chr(key)
                    samples.append((digit_img.copy(), int(digit)))
                    print(f"已收集 {len(samples)}/{num_samples}: {digit}")

            frame_count += 1

        cv2.destroyAllWindows()
        cap.release()
        return samples

    def process_video_async(self, video_path, rois, start_frame, interval=0.25,
                          progress_callback=None, status_callback=None, result_callback=None):
        """
        异步处理视频，支持进度回调
        progress_callback: function(processed, total)
        status_callback: function(message)
        result_callback: function(result_dict) - 每处理完一个时间点调用，传入结果字典
        返回: threading.Thread对象
        """
        # 更新处理状态
        self.processing_stats.update({
            'total_frames': 0,  # 将在线程中计算
            'processed_frames': 0,
            'current_status': 'processing',
            'start_time': time.time(),
            'elapsed_time': 0
        })

        def process_thread():
            try:
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    if status_callback:
                        status_callback(f"无法打开视频文件: {video_path}")
                    return

                fps = cap.get(cv2.CAP_PROP_FPS)
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                frame_interval = int(fps * interval)

                # 计算从启动帧开始的剩余帧数和实际可处理的时间点
                remaining_frames = total_frames - start_frame
                if remaining_frames <= 0:
                    total_time_points = 0
                else:
                    total_time_points = (remaining_frames - 1) // frame_interval + 1  # 确保包含启动帧且不超出范围

                # 更新总帧数
                self.processing_stats['total_frames'] = total_frames

                if status_callback:
                    status_callback(f"开始处理视频，总帧数: {total_frames}")

                # 初始化数字识别器
                recognizer = self._get_digit_recognizer()

                # 准备数据存储
                results = []
                prev_temp_full = None  # 用于时间序列推断的前一个完整温度值

                # 跳转到启动帧
                cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
                frame_count = start_frame

                while True:
                    # 检查是否应该停止
                    if self.processing_stats['current_status'] == 'stopped':
                        if status_callback:
                            status_callback("处理已停止")
                        break

                    # 检查是否应该暂停
                    while self.processing_stats['current_status'] == 'paused':
                        time.sleep(0.5)
                        if self.processing_stats['current_status'] == 'stopped':
                            break

                    ret, frame = cap.read()
                    if not ret:
                        break

                    timestamp = frame_count / fps

                    # 提取ROI
                    temp1_normal_img = self.crop_roi(frame, rois['temp1_normal'])
                    temp1_faulty_img = self.crop_roi(frame, rois['temp1_faulty'])

                    # temp2 ROI处理（支持新旧格式）
                    temp2_3digits_img = None
                    temp2_lastdigit_img = None
                    temp2_normal_img = None  # 旧格式

                    if 'temp2_normal_3digits' in rois and 'temp2_normal_lastdigit' in rois:
                        # 新格式：两个ROI
                        temp2_3digits_img = self.crop_roi(frame, rois['temp2_normal_3digits'])
                        temp2_lastdigit_img = self.crop_roi(frame, rois['temp2_normal_lastdigit'])
                    elif 'temp2_normal' in rois:
                        # 旧格式：单个ROI包含4位数字
                        temp2_normal_img = self.crop_roi(frame, rois['temp2_normal'])
                        temp2_3digits_img = temp2_normal_img  # 用于向后兼容
                    else:
                        # 没有temp2 ROI
                        pass

                    # 处理timer ROI（如果存在）
                    timer_text = None
                    if 'timer' in rois:
                        timer_img = self.crop_roi(frame, rois['timer'])
                    else:
                        # 创建一个空的占位符图像，避免识别器错误
                        timer_img = np.zeros((50, 100, 3), dtype=np.uint8)

                    # 使用数字识别器识别正常区域（正常模式）
                    recognizer.set_mode('normal')
                    if 'timer' in rois:
                        timer_text, timer_conf = recognizer.recognize_timer(timer_img)
                    else:
                        timer_text, timer_conf = None, 0.0
                    temp1_normal_text, temp1_conf = recognizer.recognize_temperature(temp1_normal_img, digit_count=3)
                    # 识别temp2（支持新旧格式）
                    if temp2_lastdigit_img is not None:
                        # 新格式：两个ROI分别识别
                        temp2_3digits_text, temp2_3digits_conf = recognizer.recognize_temperature(temp2_3digits_img, digit_count=3)
                        # 识别最后一位数字：先分割再识别（匹配debug标签页的正确做法）
                        _seg_result = recognizer.multi_digit_recognizer.segmenter.segment_digits(temp2_lastdigit_img)
                        if _seg_result:
                            temp2_lastdigit, temp2_lastdigit_conf, _ = recognizer.multi_digit_recognizer.recognize_single_digit(_seg_result[0]['image'])
                        else:
                            temp2_lastdigit, temp2_lastdigit_conf = -1, 0.0
                        # 组合temp2温度值：xxx.x格式
                        if temp2_3digits_text and len(temp2_3digits_text) >= 3 and temp2_lastdigit >= 0:
                            temp2_text = f"{temp2_3digits_text[:3]}.{temp2_lastdigit}"
                            temp2_conf = (temp2_3digits_conf + temp2_lastdigit_conf) / 2
                        else:
                            temp2_text = "????"
                            temp2_conf = 0.0
                    elif temp2_normal_img is not None:
                        # 旧格式：单个ROI包含4位数字
                        temp2_text, temp2_conf = recognizer.recognize_temperature(temp2_normal_img, digit_count=4)
                    else:
                        # 没有temp2 ROI
                        temp2_text = "????"
                        temp2_conf = 0.0

                    # 故障位数字识别（集成PaddleOCR和分类器）
                    faulty_digit_result, method, is_suspicious = self.recognize_faulty_digit(temp1_faulty_img)

                    # 初始化完整温度值
                    temp1_full = "????"
                    faulty_digit = -1
                    quality = 'low'

                    # 如果正常位识别成功，尝试组合完整温度值
                    if temp1_normal_text and len(temp1_normal_text) >= 3:
                        # 故障位识别结果处理
                        if faulty_digit_result == -2:
                            # 数字0/8情况，保留原始数据，后续统一推断
                            faulty_digit = -2
                            temp1_full = "????"
                            quality = 'low'
                        elif faulty_digit_result != -1:
                            faulty_digit = faulty_digit_result
                            temp1_full = temp1_normal_text + "." + str(faulty_digit)
                            quality = 'suspicious' if is_suspicious else 'high'
                        else:
                            faulty_digit = -1
                            temp1_full = "????"
                            quality = 'low'
                    else:
                        faulty_digit = -1
                        temp1_full = "????"
                        quality = 'low'

                    # 记录结果
                    result = {
                        'frame': frame_count,
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
                    results.append(result)

                    # 发射结果回调
                    if result_callback:
                        result_callback(result)

                    # 更新前一个完整温度值（用于时间序列推断）
                    if temp1_full != "????" and faulty_digit not in [-1, -2]:
                        prev_temp_full = temp1_full

                    # 更新进度
                    self.processing_stats['processed_frames'] = len(results)
                    self.processing_stats['elapsed_time'] = time.time() - self.processing_stats['start_time']

                    if progress_callback:
                        progress_callback(len(results), total_time_points)

                    # 跳转到下一个要处理的帧（跳过中间不需要的帧）
                    frame_count += frame_interval
                    if frame_count >= total_frames:
                        break
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count)

                cap.release()

                # 更新状态
                self.processing_stats['current_status'] = 'completed'
                if status_callback:
                    status_callback(f"处理完成，共处理 {len(results)} 个时间点")

                return results

            except Exception as e:
                self.processing_stats['current_status'] = 'error'
                if status_callback:
                    status_callback(f"处理出错: {e}")
                import traceback
                traceback.print_exc()
                return []

        # 创建并启动线程
        thread = threading.Thread(target=process_thread, daemon=True)
        thread.start()
        return thread

    def get_frame_at_timestamp(self, video_path, timestamp):
        """
        获取指定时间戳的视频帧
        返回: (ret, frame) 或 None
        """
        # 先尝试从缓存获取
        cached = self.frame_cache.get_frame(video_path, timestamp)
        if cached is not None:
            return cached

        # 缓存未命中，从视频读取
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_num = int(timestamp * fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        cap.release()

        if ret:
            # 存入缓存
            self.frame_cache.cache[(video_path, timestamp)] = frame
            return frame

        return None

    def get_frame_with_rois(self, video_path, timestamp, rois):
        """
        获取帧并在图上绘制ROI框
        返回: 绘制了ROI框的图像
        """
        frame = self.get_frame_at_timestamp(video_path, timestamp)
        if frame is None:
            return None

        # 复制帧以避免修改原始数据
        frame_with_rois = frame.copy()

        # 绘制每个ROI框
        for roi_name, roi in rois.items():
            x, y, w, h = roi
            color = self.get_roi_color(roi_name)
            cv2.rectangle(frame_with_rois, (x, y), (x+w, y+h), color, 2)
            cv2.putText(frame_with_rois, roi_name, (x, y-5),
                      cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        return frame_with_rois

    def get_frame_with_rois_cropped(self, video_path, timestamp, rois, expand_ratio=0.1,
                                    downward_expand_ratio=0.0):
        """
        获取帧并裁剪到只包含所有ROI区域（外扩指定比例）

        Args:
            video_path: 视频文件路径
            timestamp: 时间戳
            rois: ROI字典
            expand_ratio: 外扩比例（默认10%，四个方向均匀扩展）
            downward_expand_ratio: 额外向下扩展比例（相对于裁剪高度，默认0%）

        Returns:
            裁剪到ROI区域的图像，坐标已偏移
        """
        frame = self.get_frame_at_timestamp(video_path, timestamp)
        if frame is None:
            return None

        if not rois:
            return frame

        # 计算所有ROI的边界
        min_x = min(roi[0] for roi in rois.values())
        min_y = min(roi[1] for roi in rois.values())
        max_x = max(roi[0] + roi[2] for roi in rois.values())
        max_y = max(roi[1] + roi[3] for roi in rois.values())

        # 外扩
        width = max_x - min_x
        height = max_y - min_y
        expand_x = int(width * expand_ratio)
        expand_y = int(height * expand_ratio)
        # 额外向下扩展
        extra_down = int(height * downward_expand_ratio)

        # 确保不超出帧边界
        h, w = frame.shape[:2]
        crop_x1 = max(0, min_x - expand_x)
        crop_y1 = max(0, min_y - expand_y)
        crop_x2 = min(w, max_x + expand_x)
        crop_y2 = min(h, max_y + expand_y + extra_down)

        # 裁剪
        cropped = frame[crop_y1:crop_y2, crop_x1:crop_x2].copy()

        # 在裁剪后的图像上绘制ROI框（坐标需要偏移）
        offset_x = crop_x1
        offset_y = crop_y1
        for roi_name, roi in rois.items():
            x, y, w_roi, h_roi = roi
            color = self.get_roi_color(roi_name)
            cv2.rectangle(cropped, (x - offset_x, y - offset_y),
                         (x + w_roi - offset_x, y + h_roi - offset_y), color, 2)
            cv2.putText(cropped, roi_name, (x - offset_x, max(0, y - offset_y - 5)),
                      cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        return cropped

    def get_processing_stats(self):
        """
        获取处理统计信息
        返回: dict包含总帧数、已处理数、当前状态等
        """
        stats = self.processing_stats.copy()
        if stats['start_time'] and stats['current_status'] == 'processing':
            stats['elapsed_time'] = time.time() - stats['start_time']
        return stats

    def crop_roi(self, frame, roi):
        """裁剪ROI区域"""
        x, y, w, h = roi
        return frame[y:y+h, x:x+w]




    def infer_zero_eight_digit(self, current_temp_full, prev_temp_full=None, next_temp_full=None,
                              current_idx=None, results=None, window_size=10):
        """
        推断数字是0还是8（基于温度变化连续性）

        扩展支持两种模式：
        1. 简单模式：只使用前后帧（保持向后兼容）
        2. 上下文模式：使用前后各window_size个有效读数

        Args:
            current_temp_full: 当前完整的4位温度字符串
            prev_temp_full: 前一帧的完整温度字符串（简单模式）
            next_temp_full: 后一帧的完整温度字符串（简单模式）
            current_idx: 当前记录在results中的索引（上下文模式）
            results: 所有结果记录的列表（上下文模式）
            window_size: 前后窗口大小（上下文模式）

        Returns:
            简单模式: 0, 8, 或 -2（无法推断）
            上下文模式: (digit, category)
                digit: 0, 8, 或 -2（无法推断）
                category: 'determined', 'inconsistent', 'ambiguous', 'no_context'
        """
        # 上下文模式：使用前后各window_size个有效读数
        if results is not None and current_idx is not None:
            return self._infer_zero_eight_digit_with_context(
                current_temp_full, current_idx, results, window_size
            )

        # 简单模式：保持原有逻辑（向后兼容）
        try:
            if not current_temp_full or len(current_temp_full) != 4:
                return -2

            # 提取故障位数字（第4位）
            current_digit = int(current_temp_full[3])
            # 提取前三位数字（正常位）
            current_normal = int(current_temp_full[:3])

            # 如果没有前后帧数据，无法推断
            if prev_temp_full is None and next_temp_full is None:
                return -2

            # 尝试使用前一帧推断
            if prev_temp_full and len(prev_temp_full) == 4:
                prev_digit = int(prev_temp_full[3])
                prev_normal = int(prev_temp_full[:3])

                # 如果前一帧的故障位数字已知（不是0/8）
                if prev_digit not in [0, 8, -2]:
                    # 检查温度变化是否连续
                    # 示例：前一帧180.6，当前帧故障位=0/8，正常位=181
                    # 可能的序列：180.6 → 181.0 或 180.6 → 181.8
                    # 计算可能的温度值
                    possible_temp1 = current_normal * 10 + 0  # 假设是0
                    possible_temp2 = current_normal * 10 + 8  # 假设是8
                    prev_temp = prev_normal * 10 + prev_digit

                    # 检查哪个变化更连续（差值更小）
                    diff1 = abs(possible_temp1 - prev_temp)
                    diff2 = abs(possible_temp2 - prev_temp)

                    # 温度变化通常是连续的，选择差值较小的
                    if diff1 < diff2:
                        return 0
                    else:
                        return 8

            # 尝试使用后一帧推断
            if next_temp_full and len(next_temp_full) == 4:
                next_digit = int(next_temp_full[3])
                next_normal = int(next_temp_full[:3])

                if next_digit not in [0, 8, -2]:
                    # 类似逻辑，但检查与后一帧的连续性
                    possible_temp1 = current_normal * 10 + 0
                    possible_temp2 = current_normal * 10 + 8
                    next_temp = next_normal * 10 + next_digit

                    diff1 = abs(possible_temp1 - next_temp)
                    diff2 = abs(possible_temp2 - next_temp)

                    if diff1 < diff2:
                        return 0
                    else:
                        return 8

            # 如果前后帧都是0/8或未知，无法推断
            return -2

        except:
            return -2

    def _infer_zero_eight_digit_with_context(self, current_temp_full, current_idx, results, window_size):
        """
        基于上下文推断0/8数字（内部方法）

        Args:
            current_temp_full: 当前完整的4位温度字符串
            current_idx: 当前记录在results中的索引
            results: 所有结果记录的列表
            window_size: 前后窗口大小

        Returns:
            (digit, category)
            digit: 0, 8, 或 -2（无法推断）
            category: 'determined', 'inconsistent', 'ambiguous', 'no_context'
        """
        try:
            # 1. 收集前后窗口内的有效读数
            context_readings = self._collect_context_readings(current_idx, results, window_size)

            if not context_readings:
                return -2, 'no_context'

            # 2. 提取当前温度的正常位（前3位数字）
            if not current_temp_full or len(current_temp_full) != 4:
                return -2, 'no_context'

            current_normal = int(current_temp_full[:3])

            # 3. 计算两个候选温度值
            candidate_0_temp = current_normal * 10 + 0  # 假设是0
            candidate_8_temp = current_normal * 10 + 8  # 假设是8

            # 4. 评估每个候选值与上下文温度序列的连续性
            continuity_0 = self._evaluate_continuity(candidate_0_temp, context_readings)
            continuity_8 = self._evaluate_continuity(candidate_8_temp, context_readings)

            # 5. 根据评估结果分类
            return self._classify_inference_result(continuity_0, continuity_8)

        except Exception as e:
            # 记录错误但不中断
            return -2, 'no_context'

    def _collect_context_readings(self, current_idx, results, window_size):
        """
        收集前后窗口内的有效读数（简化版）

        有效读数条件：
        1. temp1_full不是"????"且不为空
        2. 可以提取有效的温度值

        返回: 有效温度值的列表（浮点数）
        """
        context_temps = []

        # 收集前window_size个读数
        start_idx = max(0, current_idx - window_size)
        for i in range(start_idx, current_idx):
            if i < 0 or i >= len(results):
                continue

            result = results[i]
            temp_full = result.get('temp1_full', '')

            # 跳过无效温度值
            if temp_full == '????' or not temp_full:
                continue

            # 提取温度值：格式为xxx.x（如1814表示181.4）
            try:
                if len(temp_full) == 4:
                    # 格式：xxx.x，如1814表示181.4
                    temp_value = float(temp_full[:3] + '.' + temp_full[3])
                    context_temps.append(temp_value)
                else:
                    # 尝试直接转换
                    temp_value = float(temp_full)
                    context_temps.append(temp_value)
            except:
                continue

        # 收集后window_size个读数
        end_idx = min(len(results), current_idx + window_size + 1)
        for i in range(current_idx + 1, end_idx):
            if i < 0 or i >= len(results):
                continue

            result = results[i]
            temp_full = result.get('temp1_full', '')

            if temp_full == '????' or not temp_full:
                continue

            try:
                if len(temp_full) == 4:
                    temp_value = float(temp_full[:3] + '.' + temp_full[3])
                    context_temps.append(temp_value)
                else:
                    temp_value = float(temp_full)
                    context_temps.append(temp_value)
            except:
                continue

        return context_temps

    def _evaluate_continuity(self, candidate_temp, context_readings):
        """
        评估候选温度值与上下文温度序列的连续性

        评估指标：
        1. 平均绝对误差（MAE）：候选值与相邻温度值的差异
        2. 斜率一致性：候选值是否保持温度变化趋势

        返回: 连续性分数（0-1，越高表示越连续）
        """
        if not context_readings:
            return 0.0

        # 计算候选值与所有上下文温度的平均绝对误差
        errors = []
        for context_temp in context_readings:
            error = abs(candidate_temp - context_temp)
            errors.append(error)

        # 归一化误差：误差越小，连续性越高
        if errors:
            # 假设温度变化通常在0-20度之间，误差超过20度认为不连续
            max_reasonable_error = 20.0
            # 使用最小误差（温度应该与最近的读数最接近）
            min_error = min(errors)
            normalized_error = min(min_error, max_reasonable_error) / max_reasonable_error
            continuity_score = 1.0 - normalized_error
            return max(0.0, min(1.0, continuity_score))

        return 0.0

    def _classify_inference_result(self, continuity_0, continuity_8):
        """
        根据连续性分数分类推断结果

        分类标准：
        1. 可确定（determined）：一个候选值的连续性分数显著高于另一个
        2. 不一致（inconsistent）：两个候选值都合理但温度变化方向相反
        3. 模糊（ambiguous）：两个候选值都合理且温度变化方向一致
        4. 无法推断（no_context）：连续性分数都太低

        返回: (digit, category)
        """
        threshold = 0.5  # 连续性阈值（降低）
        diff_threshold = 0.2  # 差异阈值（降低）

        # 检查是否都有足够的连续性
        if continuity_0 < threshold and continuity_8 < threshold:
            return -2, 'no_context'

        # 检查是否只有一个候选值有足够的连续性
        if continuity_0 >= threshold and continuity_8 < threshold:
            return 0, 'determined'
        if continuity_8 >= threshold and continuity_0 < threshold:
            return 8, 'determined'

        # 两个候选值都有足够的连续性
        diff = abs(continuity_0 - continuity_8)

        # 如果差异显著，选择连续性更高的
        if diff >= diff_threshold:
            if continuity_0 > continuity_8:
                return 0, 'determined'
            else:
                return 8, 'determined'

        # 差异不显著，两个都合理 - 返回模糊分类（黄色）
        return -2, 'ambiguous'

    def get_screen_resolution(self):
        """获取屏幕分辨率"""
        try:
            # 使用tkinter获取屏幕分辨率
            root = tk.Tk()
            root.withdraw()  # 不显示窗口
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            root.destroy()
            return screen_width, screen_height
        except:
            # 默认返回常见分辨率
            return 1920, 1080

    def get_roi_color(self, roi_name):
        """获取ROI对应的颜色"""
        colors = {
            'timer': (0, 255, 0),      # 绿色
            'temp1_normal': (255, 0, 0),  # 蓝色
            'temp1_faulty': (0, 0, 255),   # 红色
            'temp2_normal_3digits': (255, 255, 0),  # 黄色
            'temp2_normal_lastdigit': (255, 200, 0)  # 橙黄色
        }
        return colors.get(roi_name, (255, 255, 255))  # 默认白色

    def export_to_csv(self, results, video_path):
        """导出结果到CSV"""
        df = pd.DataFrame(results)

        # 添加时间列（秒转换为mm:ss:xxx格式，负时间戳显示'-'）
        df['time_str'] = df['timestamp'].apply(
            lambda x: f"{int(x//60):02d}:{int(x%60):02d}:{int((x%1)*1000):03d}" if x >= 0 else '-'
        )

        # 重新排列列
        columns = ['frame', 'timestamp', 'original_timestamp', 'time_str', 'timer', 'temp1_full',
                   'temp1_normal', 'temp1_faulty_digit', 'temp2', 'quality']
        df = df[columns]

        # 生成输出路径
        output_path = os.path.splitext(video_path)[0] + '_extracted.csv'
        df.to_csv(output_path, index=False, encoding='utf-8-sig')

        print(f"\n处理完成！")
        print(f"总记录数: {len(results)}")
        print(f"高质量数据: {len(df[df['quality']=='high'])}")
        print(f"低质量数据: {len(df[df['quality']=='low'])}")
        print(f"结果已保存到: {output_path}")

        return output_path

    def recognize_faulty_digit(self, faulty_roi_image, prev_temp_full=None, next_temp_full=None):
        """
        识别故障位数字（使用统一识别管道）

        策略：
        1. 使用统一识别管道识别单个数字（经过完整预处理）
        2. 支持正常位和故障位模式识别
        3. 如果返回-2（0/8），应用时间序列推断

        Args:
            faulty_roi_image: 故障位区域图像
            prev_temp_full: 前一帧的完整温度字符串（用于0/8推断）
            next_temp_full: 后一帧的完整温度字符串（用于0/8推断）

        Returns:
            (digit, method, is_suspicious) - 数字、识别方法标识和是否存疑
            digit: 识别的数字（-1表示无法识别）
            method: 'digit_recognizer'、'inference' 或 'unknown'
            is_suspicious: True表示识别到的亮段与映射不一致
        """
        try:
            # 策略：先直接识别，如果失败（-1）再尝试分割后识别
            # 直接分割故障位会导致g段缺失的4被误分成两个1，所以不能先分割
            pipeline = self._get_recognition_pipeline()
            pipeline.set_mode('broken')
            digit, confidence, is_suspicious, _ = pipeline.recognize_digit_image(
                faulty_roi_image, mode='broken'
            )

            # 直接识别失败时，尝试分割后重试（可能获得更准确的裁剪区域）
            if digit == -1:
                _segments = pipeline._segmenter.segment_digits(faulty_roi_image)
                if _segments:
                    digit, confidence, is_suspicious, _ = pipeline.recognize_digit_image(
                        _segments[0]['image'], mode='broken'
                    )

            # 检查识别结果
            if digit == -2:
                # 数字0/8情况，需要时间序列推断
                return -2, 'digit_recognizer_0or8', False
            elif digit != -1:
                # 成功识别数字
                return digit, 'digit_recognizer', is_suspicious
            else:
                # 无法识别
                return -1, 'unknown', False

        except Exception as e:
            print(f"数字识别器识别故障位数字失败: {e}")
            return -1, 'unknown'

    # 控制方法
    def pause_processing(self):
        """暂停处理"""
        if self.processing_stats['current_status'] == 'processing':
            self.processing_stats['current_status'] = 'paused'

    def resume_processing(self):
        """继续处理"""
        if self.processing_stats['current_status'] == 'paused':
            self.processing_stats['current_status'] = 'processing'

    def stop_processing(self):
        """停止处理"""
        if self.processing_stats['current_status'] in ['processing', 'paused']:
            self.processing_stats['current_status'] = 'stopped'